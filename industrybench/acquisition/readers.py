# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Per-device readers.

One reader per device, each on its own cadence. A single loop that read every
device and waited for all of them would pin every channel to the slowest: a
90 ms thermocouple beside two 12 ms DC reads holds all three to 11 Hz. Given
their own reader, the fast two run at 78 Hz and the slow one keeps its 11 Hz.

Threads rather than asyncio, deliberately. pyvisa over USB is a blocking call
into a C library; asyncio cannot make it yield, so an async design would hand
the same work to a thread pool and gain nothing — measured at 91.0 ms/cycle
either way. Async earns its place when the transport is genuinely async (LAN
instruments over VXI-11/HiSLIP, many of them); it does not here.

Two consumers with different needs read off the same readers:

  * the platform wants every sample with the instant it was taken, so a
    reading is published the moment it arrives;
  * a file wants one row per beat with a column per channel, so the assembler
    takes the latest held value of every channel on that beat.

Holding the last value is what makes those two compatible. The row carries the
beat's timestamp and the upstream reading carries its own, so neither claims a
freshness it does not have.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class Sample:
    """One channel's value at the instant that channel was read."""

    column: str
    value: float
    unit: str
    #: when the device was actually read, not when anything consumed it
    timestamp: datetime
    #: the device it came from, for the log and the plot card
    instrument: str


class LatestValues:
    """The most recent sample per channel, safe to read from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._values: Dict[str, Sample] = {}

    def put(self, sample: Sample) -> None:
        with self._lock:
            self._values[sample.column] = sample

    def snapshot(self) -> Dict[str, Sample]:
        """A shallow copy, so callers iterate without holding the lock."""
        with self._lock:
            return dict(self._values)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class ChannelReader(threading.Thread):
    """Reads one device on its own cadence, independent of the others.

    A slow device delays only itself. The reader never raises into the caller:
    a failed read is reported through *on_sample* as a non-finite value so the
    channel keeps its place in the series, and repeated failures are logged
    once per transition rather than once per cycle.
    """

    def __init__(self, device, interval: float,
                 on_sample: Callable[[Sample], None], name: str = ""):
        super().__init__(daemon=True, name=f"read-{name or 'device'}")
        self.device = device
        self.interval = max(0.01, float(interval))
        self._on_sample = on_sample
        # Not `_stop`: threading.Thread has a private _stop() that join()
        # calls, and shadowing it breaks join with a TypeError.
        self._halt = threading.Event()
        self.reads = 0
        self.failures = 0
        self._failing = False          # so a broken device logs once, not once
                                       # per cycle for as long as it is broken

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        name = getattr(self.device, "name", "device")
        while not self._halt.is_set():
            started = time.monotonic()
            try:
                readings = self.device.read_all()
                taken = datetime.now()
                for reading in readings:
                    self._on_sample(Sample(
                        column=reading.column, value=reading.value,
                        unit=reading.unit, timestamp=taken, instrument=name))
                self.reads += 1
                if self._failing:
                    logger.info("%s: reading again", name)
                    self._failing = False
            except Exception as e:
                self.failures += 1
                if not self._failing:
                    logger.error("%s: read failed (%s); the channel will show "
                                 "as suspect until it recovers", name, e)
                    self._failing = True
            except BaseException:
                logger.exception("%s: reader stopping on a fatal error", name)
                raise

            # Sleep the remainder of the interval, not the whole of it, so the
            # cadence is the interval rather than interval-plus-read-time.
            remaining = self.interval - (time.monotonic() - started)
            if remaining > 0:
                self._halt.wait(timeout=remaining)

    @property
    def healthy(self) -> bool:
        return not self._failing
