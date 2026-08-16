# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The run: what happens between Start and Stop.

Start is where identity is settled, and that ordering is the whole design. A
reading queued without a stable channel id and a subject to attribute it to
could never be placed once it left the bench, so before the first sample is
taken the runner mints and persists any missing ``sensor_id``, maps every
column to its device, and resolves the equipment under test from the current
project. Only then are the devices opened.

After that there are two paths out of every reading, and they are deliberately
different:

*Upstream, immediately.* Each sample goes to the outbox the moment its reader
produces it, stamped with the instant it was actually taken. Nothing waits for
a beat, and nothing is aggregated — the platform gets the timing the
instrument gave us.

*Locally, on a beat.* A file wants rows, so an assembler thread samples the
latest held value of every channel once per interval and hands one scan to the
journal. Rows are a presentation of the readings, not the readings themselves.

A failure anywhere on the local path costs a log line. A failure on the
upstream path costs nothing either, because the outbox is on disk and drains
whenever the link allows.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from industrybench.acquisition.journal import Journal
from industrybench.acquisition.readers import ChannelReader, LatestValues, Sample
from industrybench.acquisition.scan import Cell, Scan
from industrybench.edge.envelope import Reading, quality_for
from industrybench.edge.identity import ensure_sensor_ids, load_station
from industrybench.edge.outbox import Outbox
from industrybench.instruments.base import Device

#: config ``type`` -> the driver that serves it. The one place a type string
#: turns into a class; ``config.schema.DeviceKind`` holds the same set as an
#: enum so a typo is caught at load rather than here.
DRIVERS = {
    "scpi_visa": ("industrybench.instruments.visa", "VisaDevice"),
    "onewire_probe": ("industrybench.instruments.onewire", "OneWireProbe"),
    "sim_probe": ("industrybench.instruments.simulated", "SimulatedProbe"),
    "sim_voltmeter": ("industrybench.instruments.simulated", "SimulatedVoltmeter"),
    "sim_ammeter": ("industrybench.instruments.simulated", "SimulatedAmmeter"),
}

#: config ``type`` -> the sink that serves it
WRITERS = {
    "delimited": ("industrybench.sinks.csv", "CsvSink"),
    "jsonl": ("industrybench.sinks.json", "JsonSink"),
}


def _build(registry: Dict[str, tuple], kind: str, *args):
    """Import and construct whatever *kind* names, or None with a log line."""
    target = registry.get(kind)
    if target is None:
        logging.getLogger(__name__).error("Nothing is registered for %r", kind)
        return None
    module_name, class_name = target
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)(*args)
    except Exception:
        logging.getLogger(__name__).exception("Could not build %s", class_name)
        return None


def _loggable(name: str) -> str:
    """A device name safe for a console that may be on a legacy codepage."""
    try:
        return name.encode("ascii", errors="replace").decode("ascii")
    except Exception:
        return "(unprintable name)"


class Runner:
    """Owns the devices, the run loop, and where readings go."""

    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)

        self.devices: Dict[str, Device] = {}
        self.journal = Journal()

        # The outbox is written whether or not a platform is configured. An
        # unconfigured bench simply keeps a queue it never drains, which costs
        # a few kilobytes and means turning forwarding on later loses nothing.
        self.station = load_station(config)
        self.outbox = Outbox()

        # Resume delivery at startup rather than waiting for somebody to open
        # the platform page and press Apply. A bench that was configured last
        # week and restarted this morning has to drain what it queued
        # overnight without being asked.
        if self.station.can_forward():
            self.outbox.start_forwarding(self.station)

        #: column -> {"device": name, "sensor_id": uuid}
        self.channel_map: Dict[str, Dict[str, str]] = {}
        self.equipment_id: Optional[str] = None
        self.equipment_name: str = ""

        self.running = False
        self._gate = threading.Lock()
        self._assembler: Optional[threading.Thread] = None
        self._readers: List[ChannelReader] = []
        self._latest = LatestValues()

        self._writing = True
        self._writing_lock = threading.Lock()

        self.started_at: Optional[datetime] = None
        self.scans = 0
        self.empty_beats = 0

        self.load_devices()
        self.load_sinks()

    # ------------------------------------------------------------------ devices
    def load_devices(self) -> None:
        """Build a driver for every enabled device in the configuration."""
        for entry in self.config.enabled_devices():
            name, kind = entry.get("name", ""), entry.get("kind", "")
            if not name or not kind:
                self.logger.error("Skipping a device entry with no name or type")
                continue
            device = _build(DRIVERS, kind, name, entry)
            if device is None:
                self.logger.error("Device %s could not be built", _loggable(name))
                continue
            self.devices[name] = device
            self.logger.info("Device %s ready (%s)", _loggable(name), kind)

    def reload_devices(self) -> None:
        """Take up a configuration change without stopping the run.

        A device dropped from the configuration is switched off rather than
        deleted: the assembler holds this dict while it runs, and removing a
        key underneath it would fail a loop mid-beat for no gain.
        """
        try:
            entries = self.config.enabled_devices()
        except Exception:
            self.logger.exception("Could not re-read the device configuration")
            return

        wanted = {e.get("name") for e in entries if e.get("name")}
        for name, device in self.devices.items():
            if name not in wanted:
                device.enabled = False
                self.logger.info("Device %s dropped from the configuration",
                                 _loggable(name))

        for entry in entries:
            name, kind = entry.get("name", ""), entry.get("kind", "")
            if not name or not kind:
                continue
            existing = self.devices.get(name)
            if existing is not None:
                existing.enabled = entry.get("enabled", True)
                existing.channel_configs = entry.get("channels", []) or []
                existing.config = entry
                continue
            device = _build(DRIVERS, kind, name, entry)
            if device is not None:
                self.devices[name] = device
                self.logger.info("Device %s added mid-session", _loggable(name))

    def columns(self) -> List[str]:
        """Every output column across the enabled devices."""
        return [column
                for device in self.devices.values() if device.enabled
                for column in device.column_names()]

    # -------------------------------------------------------------------- sinks
    def load_sinks(self) -> None:
        """Attach the sink the session configured, if any."""
        try:
            spec = self.config.get_output_config()
        except Exception:
            self.logger.exception("Could not read the output configuration")
            return
        if not spec:
            return
        sink = _build(WRITERS, spec.get("type", ""), spec)
        if sink is not None:
            self.journal.attach("output", sink)

    # ----------------------------------------------------------------- identity
    def mint_channel_ids(self) -> int:
        """Give every channel a durable ``sensor_id`` and persist it.

        Minting without saving would be worse than not minting: the id would
        differ on the next launch, and the platform would see the same physical
        channel as two. So a failed save is loud.
        """
        try:
            devices = self.config.devices.get("devices", [])
        except Exception:
            self.logger.exception("Could not read devices to assign channel ids")
            return 0

        minted = ensure_sensor_ids(devices)
        if not minted:
            return 0
        if self.config.save_devices():
            self.logger.info("Assigned a durable id to %d new channel(s)", minted)
        else:
            self.logger.error(
                "Minted %d channel id(s) but could not save them. They will "
                "differ after a restart; readings stay queued locally.", minted)
        return minted

    def map_channels(self) -> Dict[str, Dict[str, str]]:
        """Map every column to the device that produces it and its stored id.

        Rebuilt whenever the devices change, so the envelope never has to guess
        which device a column came from, nor invent an identity for it.
        """
        self.mint_channel_ids()

        configured = {}
        try:
            for entry in self.config.devices.get("devices", []):
                configured[entry.get("name", "")] = entry
        except Exception as e:
            # Not fatal — each device carries its own copy — but it means no
            # channel resolves an id, so nothing is forwarded. Not quietly.
            self.logger.warning("Could not index the configured devices: %s", e)

        found, anonymous = {}, []
        for name, device in self.devices.items():
            channels = (configured.get(name, {}).get("channels")
                        or device.channel_configs or [])
            for channel in channels:
                column = channel.get("column")
                if not column:
                    continue
                sensor_id = channel.get("sensor_id")
                if sensor_id:
                    found[column] = {"device": name, "sensor_id": sensor_id}
                else:
                    anonymous.append(f"{name}/{column}")

        if anonymous:
            self.logger.warning(
                "No stored id for %s — recorded locally, not forwarded",
                ", ".join(anonymous))
        self.channel_map = found
        return found

    def resolve_equipment(self) -> None:
        """Take the subject under test from the current project."""
        self.equipment_id, self.equipment_name = None, ""
        try:
            from industrybench.acquisition.project import ProjectShelf
            project = ProjectShelf(self.config).current()
        except Exception:
            self.logger.exception("Could not read the current project")
            project = None

        if project is None:
            self.logger.warning(
                "No project chosen — this run records locally but forwards "
                "nothing, because there is no equipment to attribute it to")
            return

        self.equipment_id = project.equipment_id
        self.equipment_name = project.subject
        self.logger.info("Measuring %s (%s)", self.equipment_name, self.equipment_id)

    # -------------------------------------------------------------------- start
    def start(self, write: bool = True) -> bool:
        """Settle identity, open the devices, and begin reading.

        False if nothing could be opened, or if writing was asked for and no
        file could be claimed — starting a run that silently records nothing is
        the one failure an operator would not notice.
        """
        with self._gate:
            if self.running:
                self.logger.warning("A run is already in progress")
                return False

            self.writing = write
            self.map_channels()
            self.resolve_equipment()

            if not self.open_devices():
                return False

            if write and not self.journal.open_sinks():
                self.logger.error("No output file could be opened")
                self.close_devices()
                return False
            if not write:
                self.logger.info("Preview run — nothing will be written to file")

            self.journal.forget()
            self.started_at = datetime.now()
            self.scans = 0
            self.empty_beats = 0
            self.running = True

            self._start_readers()
            self._assembler = threading.Thread(
                target=self._assemble, name="assemble", daemon=True)
            self._assembler.start()

            self.logger.info("Run started (writing=%s)", write)
            return True

    def stop(self) -> bool:
        with self._gate:
            if not self.running:
                self.logger.warning("No run is in progress")
                return False

            self.running = False
            self._stop_readers()

            if self._assembler is not None:
                self._assembler.join(timeout=5.0)
                if self._assembler.is_alive():
                    self.logger.warning("The assembler did not stop in time")
                self._assembler = None

            self.close_devices()
            if self.writing:
                self.journal.close_sinks()

            self.logger.info("Run stopped after %d scan(s)", self.scans)
            return True

    def open_devices(self) -> bool:
        """Open every enabled device. True if at least one answered.

        One working instrument is a run worth having; the operator can see on
        the tree which of the others did not come up.
        """
        enabled = [(n, d) for n, d in self.devices.items() if d.enabled]
        if not enabled:
            self.logger.warning("No device is enabled")
            return False

        opened = 0
        for name, device in enabled:
            try:
                if device.open():
                    opened += 1
                else:
                    self.logger.warning("Device %s did not open", _loggable(name))
            except Exception:
                self.logger.exception("Device %s raised while opening",
                                      _loggable(name))

        self.logger.info("Opened %d of %d device(s)", opened, len(enabled))
        if not opened:
            self.logger.error("No device could be opened — the run cannot start")
        return opened > 0

    def close_devices(self) -> bool:
        released = True
        for name, device in self.devices.items():
            try:
                if not device.close():
                    released = False
            except Exception:
                self.logger.exception("Device %s raised while closing",
                                      _loggable(name))
                released = False
        return released

    # ------------------------------------------------------------------ readers
    def _start_readers(self) -> None:
        """One reader per open device, each free to run at its own rate."""
        self._latest.clear()
        beat = self.interval()
        self._readers = [
            ChannelReader(device, beat, self._sample_arrived, name=name)
            for name, device in self.devices.items()
            if device.enabled and device.is_open()
        ]
        for reader in self._readers:
            reader.start()
        self.logger.info("Reading %d device(s)", len(self._readers))

    def _stop_readers(self) -> None:
        for reader in self._readers:
            reader.stop()
        for reader in self._readers:
            reader.join(timeout=2.0)
            if reader.is_alive():
                self.logger.warning("Reader %s did not stop", reader.name)
        self._readers = []

    def _sample_arrived(self, sample: Sample) -> None:
        """A reading arrived: hold it for the next scan, queue it upstream now."""
        self._latest.put(sample)
        self._queue(sample)

    def _queue(self, sample: Sample) -> None:
        """Put one reading in the outbox, stamped when it was actually taken.

        Best effort and never fatal: the run owns the data, the outbox is only
        its courier. A channel with no stored identity is skipped rather than
        sent under an id that would not survive a restart.
        """
        if not self.equipment_id:
            return
        known = self.channel_map.get(sample.column)
        if not known or not known.get("sensor_id"):
            return
        try:
            self.outbox.append([Reading(
                timestamp=sample.timestamp,
                sensor_id=known["sensor_id"],
                equipment_id=self.equipment_id,
                site_id=self.station.site_id,
                value=sample.value,
                unit=sample.unit or None,
                quality_code=quality_for(sample.value),
                instrument=sample.instrument,
                channel=sample.column,
            )])
        except Exception as e:
            self.logger.error("Could not queue a reading for delivery: %s", e)

    # ---------------------------------------------------------------- assembly
    def _assemble(self) -> None:
        """Emit one aligned scan per interval from the latest value of each
        channel, so a file keeps its grid while the devices underneath sample
        at whatever rate each can manage."""
        beat = self.interval()
        while self.running:
            time.sleep(beat)
            if not self.running:
                break
            try:
                scan = self._take_scan()
                if scan is None:
                    self.empty_beats += 1
                else:
                    self.journal.keep(scan, to_file=self.writing)
                    self.scans += 1
            except Exception:
                # This guards the run's own thread. Swallowed without a
                # traceback, a defect here is invisible and repeats every beat.
                self.logger.exception("The assembler failed on a beat")
                self.empty_beats += 1
            beat = self.interval(beat)

    def _take_scan(self) -> Optional[Scan]:
        """One row from the most recent value of every channel."""
        held = self._latest.snapshot()
        if not held:
            return None
        return Scan.taken({column: Cell(sample.value, sample.unit)
                           for column, sample in held.items()})

    def interval(self, fallback: float = 1.0) -> float:
        """The configured beat, re-read every time.

        Read fresh rather than captured at start, so changing the cadence in
        the run bar takes effect on the next beat instead of the next run.
        """
        try:
            return float(self.config.get_run_settings().get("interval", fallback))
        except Exception:
            return fallback

    # -------------------------------------------------------------------- state
    @property
    def writing(self) -> bool:
        """Whether scans are being written to file, safe across threads."""
        with self._writing_lock:
            return self._writing

    @writing.setter
    def writing(self, value: bool) -> None:
        with self._writing_lock:
            self._writing = bool(value)

    def recent(self, count: int = 100) -> List[Scan]:
        return self.journal.recent(count)

    def since(self, cursor: int) -> Tuple[List[Scan], int]:
        return self.journal.since(cursor)

    def forget(self) -> None:
        """Drop what the console is drawing. Files and the outbox are untouched."""
        self.journal.forget()

    def report(self) -> Dict[str, Any]:
        """What the console shows about this run."""
        return {
            "running": self.running,
            "scans": self.scans,
            "empty_beats": self.empty_beats,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "devices": {name: device.status()
                        for name, device in self.devices.items()},
            "journal": self.journal.counters(),
        }
