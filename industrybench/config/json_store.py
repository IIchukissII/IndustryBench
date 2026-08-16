# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration held in plain JSON files.

Three files sit in the configuration directory — ``bench.json`` for app-wide
settings, ``devices.json`` for the bench itself, ``setups.json`` for named
arrangements. Each is read once into a dict at startup, edited in place by the
console, and written back on demand.

This is the base store. ``ConfigStore`` subclasses it to keep the same dicts in
SQLite and falls back to these methods whenever its tables are empty, so what
happens here is what a fresh install gets before anything is persisted.

Two properties matter more than they look:

*Losing a file must be survivable.* A bench that will not start because its
settings were truncated by a power cut is worse than one that starts with
defaults, so an unreadable file is replaced by its seed and a damaged one is
set aside first rather than deleted. The seeds live in ``config.defaults``;
this module holds none of its own.

*A write must not be able to truncate.* Configuration is rewritten on every
device edit, and an in-place write leaves a window in which the file on disk is
empty. Every write here lands in a temporary file that is renamed over the
target, so a reader sees either the previous contents or the new ones.
"""

from __future__ import annotations

import json
import locale
import logging
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from industrybench import paths
from industrybench.config.defaults import (
    BENCH_FILE,
    DEFAULT_BENCH,
    DEFAULT_DEVICES,
    DEFAULT_SETUPS,
    DEVICES_FILE,
    SETUPS_FILE,
)
from industrybench.config.schema import (
    BenchConfig,
    SetupsConfig,
    checked,
    checked_device,
    checked_devices,
)


def _ambient_encoding() -> str:
    """
    The codepage a file predating UTF-8 was most likely written in.

    A configuration file may come from an editor or a tool that used the
    machine's ANSI codepage. Anything non-ASCII in a device name or a unit —
    ``°C`` above all — is then not valid UTF-8, and a strict read raises
    instead of loading the bench.
    """
    return locale.getpreferredencoding(False) or "cp1252"


def decode_config(raw: bytes) -> Optional[str]:
    """
    Decode configuration bytes as UTF-8, tolerating a BOM or a legacy codepage.

    Shared with ``config.sanitize`` so a tool that rewrites these files reads
    them exactly as the store does. Decoding with ``errors="replace"`` would
    turn the ``°`` of an ANSI file into U+FFFD, and anything that wrote the
    result back would make the loss permanent.
    """
    # utf-8-sig reads plain UTF-8 unchanged and strips the BOM Windows editors
    # add, so it covers both of the cases this app writes.
    for encoding in ("utf-8-sig", _ambient_encoding()):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def write_atomically(path: Path, payload: Any, logger: logging.Logger) -> bool:
    """
    Write JSON through a temporary file renamed over the target.

    The target holds either its previous contents or the new ones, never half
    of either. Configuration is rewritten on every device edit, so the window
    an in-place write leaves open is one this app would eventually land in.
    """
    partial = path.with_name(path.name + ".partial")
    try:
        with open(partial, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(partial, path)
    except (OSError, TypeError, ValueError) as e:
        # TypeError/ValueError: the payload held something json cannot encode.
        # The target is untouched either way; clear the partial file so it is
        # not mistaken for configuration later.
        logger.error("Could not write %s: %s", path, e)
        partial.unlink(missing_ok=True)
        return False

    logger.info("Wrote %s", path)
    return True


class _File:
    """
    One JSON file on disk, and what to use when it cannot be read.

    Holds no state beyond that: the loaded dict belongs to the store, which
    hands it to the console to edit.
    """

    def __init__(self, path: Path, seed: Dict[str, Any], logger: logging.Logger):
        self.path = path
        self.seed = seed
        self.logger = logger

    # -- reading -----------------------------------------------------------
    def read(self) -> Dict[str, Any]:
        """Load the file, or lay down the seed if that is not possible."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return self._lay_seed("it does not exist yet")
        except OSError as e:
            # Locked or unreadable. Do not touch it — it may be intact and
            # simply busy, and overwriting would turn a transient failure into
            # a permanent one.
            self.logger.error("Cannot read %s (%s); using defaults", self.path, e)
            return deepcopy(self.seed)

        if not raw.strip():
            return self._lay_seed("it is empty")

        text = decode_config(raw)
        if text is None:
            return self._set_aside("its encoding is unreadable")

        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as e:
            return self._set_aside(f"invalid JSON at line {e.lineno}")

        if not isinstance(loaded, dict):
            # A list or a bare string parses cleanly but nothing downstream can
            # use it. Treat it as damage rather than letting the type error
            # surface somewhere much further away.
            return self._set_aside(
                f"expected an object, found {type(loaded).__name__}")

        self.logger.info("Read %s", self.path)
        return loaded

    # -- writing -----------------------------------------------------------
    def write(self, payload: Dict[str, Any]) -> bool:
        return write_atomically(self.path, payload, self.logger)

    # -- recovery ----------------------------------------------------------
    def _lay_seed(self, why: str) -> Dict[str, Any]:
        """Install the seed and return a copy the caller may edit freely."""
        self.logger.info("Seeding %s (%s)", self.path, why)
        fresh = deepcopy(self.seed)
        self.write(fresh)
        return fresh

    def _set_aside(self, why: str) -> Dict[str, Any]:
        """Keep the damaged file under a new name, then seed a fresh one."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        # Appended, not substituted: `.with_suffix()` would drop the `.json`
        # and leave a salvaged file that no longer looks like configuration.
        kept = self.path.with_name(f"{self.path.name}.damaged_{stamp}")
        try:
            os.replace(self.path, kept)
            self.logger.error("%s is damaged (%s); kept as %s",
                              self.path, why, kept.name)
        except OSError as e:
            self.logger.error("%s is damaged (%s) and could not be kept: %s",
                              self.path, why, e)
        return self._lay_seed("replacing a damaged file")


class JsonConfig:
    """The three configuration files, loaded and written as dicts."""

    def __init__(self, config_dir: str | Path | None = None):
        self.config_dir = (
            Path(config_dir) if config_dir is not None else paths.config_dir()
        )
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Named for the concrete class, so a subclass's messages are not
        # attributed to this module.
        self.logger = logging.getLogger(type(self).__module__)

        self.bench_path = self.config_dir / BENCH_FILE
        self.devices_path = self.config_dir / DEVICES_FILE
        self.setups_path = self.config_dir / SETUPS_FILE

        self._bench_file = _File(self.bench_path, DEFAULT_BENCH, self.logger)
        self._devices_file = _File(self.devices_path, DEFAULT_DEVICES, self.logger)
        self._setups_file = _File(self.setups_path, DEFAULT_SETUPS, self.logger)

        # Through the methods, not the files: a subclass overrides these to
        # read from its own storage and only falls back here.
        self.bench = self.read_bench()
        self.devices = self.read_devices()
        self.setups = self.read_setups()

    # ------------------------------------------------------------ whole files
    def read_bench(self) -> Dict[str, Any]:
        return checked(BenchConfig, self._bench_file.read(), str(self.bench_path))

    def read_devices(self) -> Dict[str, Any]:
        return checked_devices(self._devices_file.read(), str(self.devices_path))

    def read_setups(self) -> Dict[str, Any]:
        return checked(SetupsConfig, self._setups_file.read(), str(self.setups_path))

    def save_bench(self) -> bool:
        return self._bench_file.write(self.bench)

    def save_devices(self) -> bool:
        return self._devices_file.write(self.devices)

    def save_setups(self) -> bool:
        return self._setups_file.write(self.setups)

    # ---------------------------------------------------------------- devices
    def _device_list(self) -> List[Dict[str, Any]]:
        """The live device list — mutating it edits the loaded configuration."""
        return self.devices.setdefault("devices", [])

    def enabled_devices(self) -> List[Dict[str, Any]]:
        """
        Every device that should take part in a run.

        A missing ``enabled`` key counts as enabled, matching the console, the
        SQLite default and ``ConfigStore.save_devices``. An entry written
        without the key must not be silently skipped when measuring.
        """
        return [d for d in self._device_list() if d.get("enabled", True)]

    def device_named(self, name: str) -> Optional[Dict[str, Any]]:
        return next((d for d in self._device_list() if d.get("name") == name), None)

    def add_device(self, entry: Dict[str, Any]) -> bool:
        """Add a device. False if the name is taken or the entry is invalid."""
        name = entry.get("name", "")
        if self.device_named(name):
            self.logger.error("There is already a device called '%s'", name)
            return False

        validated = checked_device(entry, "a new device")
        if validated is None:
            return False

        self._device_list().append(validated)
        return self.save_devices()

    def replace_device(self, name: str, entry: Dict[str, Any]) -> bool:
        """Replace a device's configuration. False if it is not there."""
        devices = self._device_list()
        for i, existing in enumerate(devices):
            if existing.get("name") == name:
                validated = checked_device(entry, f"device '{name}'")
                if validated is None:
                    return False
                devices[i] = validated
                return self.save_devices()

        self.logger.error("No device called '%s'", name)
        return False

    def drop_device(self, name: str) -> bool:
        """Remove a device. False if it was not there."""
        devices = self._device_list()
        remaining = [d for d in devices if d.get("name") != name]
        if len(remaining) == len(devices):
            self.logger.error("No device called '%s'", name)
            return False

        # In place: callers and the SQLite subclass hold this same list object.
        devices[:] = remaining
        return self.save_devices()

    # ----------------------------------------------------------- bench sections
    def get_output_config(self) -> Dict[str, Any]:
        return self.bench.get("output", {})

    def set_output_config(self, output: Dict[str, Any]) -> bool:
        """Replace the output configuration outright."""
        self.bench["output"] = output
        return self.save_bench()

    def get_run_settings(self) -> Dict[str, Any]:
        return self.bench.get("run", {})

    def set_run_settings(self, settings: Dict[str, Any]) -> bool:
        """
        Merge into the run settings.

        A merge, unlike ``set_output_config`` above, because callers pass the
        one field they changed — replacing would drop ``interval`` every time
        the console adjusted ``window``.
        """
        self.bench.setdefault("run", {}).update(settings)
        return self.save_bench()
