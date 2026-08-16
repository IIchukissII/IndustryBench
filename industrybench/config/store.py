# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration held in SQLite, mirrored to JSON.

Extends ``JsonConfig`` and keeps its surface: the loaded configuration lives in
``bench``, ``devices`` and ``setups`` as plain dicts the console edits in place.
What differs is where they persist. ``config/industrybench.db`` is the record
of truth, and the JSON files are written alongside it as a readable copy
somebody can open in an editor or commit to a repository.

Why both: SQLite gives a write that either happened or did not, which matters
when the console saves on every keystroke-completed edit. JSON gives a file a
person can read, diff and mail to a colleague. Neither alone is enough, and the
mirror is cheap because these files are small.

On first open, whatever the base class read from JSON is written into the empty
tables. After that the database leads and the files follow.

Schema
------
=========  =================================================================
bench      one row per top-level key of ``bench.json``
devices    one row per device, the whole entry as a JSON blob
setups     one row per saved arrangement, the whole entry as a JSON blob
projects   one row per project, the whole entry as a JSON blob
=========  =================================================================

``devices.kind`` and ``devices.enabled`` duplicate fields inside the blob.
Nothing here reads them back — they exist so the table is legible from the
``sqlite3`` shell when a bench has to be diagnosed without the app.
"""

from __future__ import annotations

import functools
import json
import logging
import sqlite3
import threading
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from industrybench import paths
from industrybench.config.json_store import JsonConfig
from industrybench.config.schema import SetupsConfig, checked, checked_device, checked_devices

T = TypeVar("T")

#: Faults that mean "storage, or the data in it", as opposed to a defect in
#: this module. These are logged and turned into a failed return; anything else
#: keeps its traceback so it can be found and fixed.
_STORAGE_FAULTS = (
    sqlite3.Error, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError,
)

#: The columns each table must have for this code to work. Checked at open —
#: see ``ConfigStore._check_shape``. Only what is read or written is listed, so
#: a column added later does not by itself condemn a database.
EXPECTED_COLUMNS = {
    "bench": {"key", "value", "written_at"},
    "devices": {"name", "kind", "enabled", "entry", "written_at"},
    "setups": {"name", "chosen", "entry", "written_at"},
    "projects": {"name", "entry", "written_at"},
}


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(text: str) -> Any:
    return json.loads(text)


class StoreClosed(RuntimeError):
    """Raised when a closed store is used again.

    Kept apart from a storage fault because it is not one: the database is
    fine and the caller has a bug. It travels out past ``surviving`` rather
    than being logged and turned into an empty result, which would leave the
    caller seeing "no devices" with no reason why.
    """


def surviving(default: T) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Report a storage failure and answer ``default`` instead of raising.

    Every operation below wants this, and spelling the same except clauses out
    at each one buries the work they wrap. A configuration error must not take
    down a run in progress, so the failure is logged and the caller gets a
    falsy result, which is the contract the console expects.
    """
    def decorate(method: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(method)
        def wrapper(self: "ConfigStore", *args: Any, **kwargs: Any) -> T:
            try:
                return method(self, *args, **kwargs)
            except StoreClosed:
                raise
            except _STORAGE_FAULTS as e:
                self.logger.error("%s failed: %s", method.__name__, e)
            except Exception:
                self.logger.exception("Unexpected failure in %s()", method.__name__)
            # Copied, so a caller mutating the result cannot alter what the
            # next failure hands back.
            return copy(default) if isinstance(default, (list, dict, set)) else default
        return wrapper
    return decorate


class ConfigStore(JsonConfig):
    """Configuration persisted in SQLite, with the JSON files kept in step."""

    DB_FILENAME = "industrybench.db"

    def __init__(self, config_dir: str | Path | None = None):
        self._dir = (
            Path(config_dir) if config_dir is not None else paths.config_dir()
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(type(self).__module__)

        # Re-entrant: the public operations lock, and they call each other.
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._db_path = self._dir / self.DB_FILENAME

        self._connect()
        self._define_tables()
        self._check_shape()

        # Fills the three dicts through the read_* methods below, which query
        # this database and fall back to the JSON files.
        super().__init__(self._dir)

        self._fill_empty_tables()

    # ------------------------------------------------------------------- open
    def _connect(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def _db(self) -> sqlite3.Connection:
        """
        The open connection.

        Named rather than reached for directly so that using a closed store
        says so, instead of raising ``AttributeError`` on None somewhere inside
        a query and being logged as an unexplained fault.
        """
        if self._conn is None:
            raise StoreClosed(f"the configuration store for {self._db_path} is closed")
        return self._conn

    def _define_tables(self) -> None:
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS bench (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS devices (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT UNIQUE NOT NULL,
                    kind       TEXT NOT NULL,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    entry      TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setups (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT UNIQUE NOT NULL,
                    chosen     INTEGER NOT NULL DEFAULT 0,
                    entry      TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT UNIQUE NOT NULL,
                    entry      TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );
            """)

    def _check_shape(self) -> None:
        """
        Make sure the tables are the ones this code writes to.

        ``CREATE TABLE IF NOT EXISTS`` creates a missing table and says nothing
        about one that exists with different columns. A database written by an
        older version therefore opens cleanly and then fails on the first
        query — "no such column" per operation, forever, with the app looking
        healthy in every other respect.

        So the columns are compared here, at open, and a database that does not
        match is set aside under a timestamped name and replaced. That loses no
        data: it is set aside, not deleted, and the JSON files beside it hold
        the same configuration in a form anything can read.
        """
        wrong = []
        for table, expected in EXPECTED_COLUMNS.items():
            rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()
            found = {row["name"] for row in rows}
            if not expected <= found:
                wrong.append(f"{table} (missing {', '.join(sorted(expected - found))})")

        if not wrong:
            return

        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        aside = self._db_path.with_name(f"{self._db_path.name}.old_{stamp}")
        self.logger.warning(
            "%s was written by a different version — %s. Keeping it as %s and "
            "starting a new one from the JSON files.",
            self._db_path.name, "; ".join(wrong), aside.name)

        self.close()
        try:
            # The sidecars belong to the file being moved; leaving them behind
            # would attach a stale write-ahead log to the fresh database.
            for suffix in ("", "-wal", "-shm"):
                source = self._db_path.with_name(self._db_path.name + suffix)
                if source.exists():
                    source.replace(aside.with_name(aside.name + suffix))
        except OSError as e:
            self.logger.error("Could not set %s aside: %s", self._db_path.name, e)

        self._connect()
        self._define_tables()

    # ------------------------------------------------------------------------
    # Statements. None of these open a transaction — callers group them.
    # ------------------------------------------------------------------------

    def _write_setting(self, key: str, value: Any) -> None:
        self._db.execute(
            """
            INSERT INTO bench (key, value, written_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                           written_at=excluded.written_at
            """,
            (key, _to_json(value), _stamp()),
        )

    def _write_device(self, entry: Dict[str, Any]) -> None:
        self._db.execute(
            """
            INSERT INTO devices (name, kind, enabled, entry, written_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,
                                            enabled=excluded.enabled,
                                            entry=excluded.entry,
                                            written_at=excluded.written_at
            """,
            (
                entry.get("name", ""),
                entry.get("kind", ""),
                1 if entry.get("enabled", True) else 0,
                _to_json(entry),
                _stamp(),
            ),
        )

    def _write_setup(self, entry: Dict[str, Any], chosen: bool = False) -> None:
        self._db.execute(
            """
            INSERT INTO setups (name, chosen, entry, written_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET chosen=excluded.chosen,
                                            entry=excluded.entry,
                                            written_at=excluded.written_at
            """,
            (entry.get("name", ""), int(chosen), _to_json(entry), _stamp()),
        )

    def _write_project(self, entry: Dict[str, Any]) -> None:
        self._db.execute(
            """
            INSERT INTO projects (name, entry, written_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET entry=excluded.entry,
                                            written_at=excluded.written_at
            """,
            (entry.get("name", ""), _to_json(entry), _stamp()),
        )

    def _entries(self, table: str) -> List[Dict[str, Any]]:
        """Every row of a blob table, in insertion order."""
        rows = self._db.execute(f"SELECT entry FROM {table} ORDER BY id").fetchall()
        return [_from_json(row["entry"]) for row in rows]

    def _named(self, table: str) -> set:
        return {row["name"] for row in self._db.execute(f"SELECT name FROM {table}")}

    def _population(self, table: str) -> int:
        return self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _erase(self, table: str, names: set) -> None:
        self._db.executemany(
            f"DELETE FROM {table} WHERE name=?", [(n,) for n in names])

    def _chosen_setup(self) -> str:
        row = self._db.execute(
            "SELECT name FROM setups WHERE chosen=1 ORDER BY id LIMIT 1").fetchone()
        return row["name"] if row else ""

    # ------------------------------------------------------------------------
    # Reading. Called by JsonConfig.__init__, before the dicts exist.
    # ------------------------------------------------------------------------

    def read_bench(self) -> Dict[str, Any]:
        """From SQLite; from the JSON file while the table is still empty."""
        rows = self._db.execute("SELECT key, value FROM bench").fetchall()
        if not rows:
            # Nothing stored yet. The base class reads the files, and
            # _fill_empty_tables() writes the result here once __init__ ends.
            return super().read_bench()
        return {row["key"]: _from_json(row["value"]) for row in rows}

    def read_devices(self) -> Dict[str, Any]:
        entries = self._entries("devices")
        if not entries:
            return super().read_devices()
        # Validated coming out of SQLite as well as out of JSON: rows may have
        # been written by an older schema, or by hand through the sqlite3
        # shell, and readers downstream rely on the defaults being there.
        return checked_devices({"devices": entries}, str(self._db_path))

    def read_setups(self) -> Dict[str, Any]:
        entries = self._entries("setups")
        if not entries:
            return super().read_setups()
        return checked(
            SetupsConfig,
            {"setups": entries, "chosen": self._chosen_setup()},
            str(self._db_path),
        )

    def _reload_devices(self) -> None:
        """Refresh the in-memory device list from the database, in place.

        In place, because callers and the base class hold this list object.
        """
        self.devices.setdefault("devices", [])[:] = self._entries("devices")

    # ------------------------------------------------------------------------
    # Writing. One transaction per operation, then the JSON mirror.
    # ------------------------------------------------------------------------

    @surviving(False)
    def save_bench(self) -> bool:
        with self._lock, self._db:
            for key, value in self.bench.items():
                self._write_setting(key, value)
        super().save_bench()
        return True

    @surviving(False)
    def save_devices(self) -> bool:
        with self._lock, self._db:
            # Delete and re-insert inside one transaction. Split across
            # several, an interruption could leave rows removed and their
            # replacements never written.
            wanted = self.devices.get("devices", [])
            self._erase("devices",
                        self._named("devices") - {d.get("name") for d in wanted})
            for entry in wanted:
                self._write_device(entry)
        super().save_devices()
        return True

    @surviving(False)
    def save_setups(self) -> bool:
        with self._lock, self._db:
            chosen = self.setups.get("chosen", "")
            wanted = self.setups.get("setups", [])
            self._erase("setups",
                        self._named("setups") - {s.get("name") for s in wanted})
            for entry in wanted:
                self._write_setup(entry, entry.get("name") == chosen)
        super().save_setups()
        return True

    # ---------------------------------------------------------------- devices
    @surviving(False)
    def add_device(self, entry: Dict[str, Any]) -> bool:
        """Add a device. False if the name is taken or the entry is invalid."""
        name = entry.get("name", "")
        if self.device_named(name):
            self.logger.error("There is already a device called '%s'", name)
            return False

        # Validated here as well as in the base class: this override does not
        # call up to it, so without this an entry from the console would reach
        # the database with its defaults unfilled.
        validated = checked_device(entry, "a new device")
        if validated is None:
            return False

        with self._lock, self._db:
            self._write_device(validated)
        self._reload_devices()
        super().save_devices()
        return True

    @surviving(False)
    def replace_device(self, name: str, entry: Dict[str, Any]) -> bool:
        """Replace a device's configuration. False if it is not there."""
        if not self.device_named(name):
            self.logger.error("No device called '%s'", name)
            return False

        validated = checked_device(entry, f"device '{name}'")
        if validated is None:
            return False

        with self._lock, self._db:
            # A rename would otherwise leave the old row behind, since the
            # name is the key.
            if validated.get("name", name) != name:
                self._erase("devices", {name})
            self._write_device(validated)
        self._reload_devices()
        super().save_devices()
        return True

    @surviving(False)
    def drop_device(self, name: str) -> bool:
        """Remove a device. False if it was not there."""
        if not self.device_named(name):
            self.logger.error("No device called '%s'", name)
            return False

        with self._lock, self._db:
            self._erase("devices", {name})
        self._reload_devices()
        super().save_devices()
        return True

    # --------------------------------------------------------------- projects
    @surviving(False)
    def put_project(self, entry: Dict[str, Any]) -> bool:
        with self._lock, self._db:
            self._write_project(entry)
        return True

    @surviving(None)
    def read_project(self, name: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT entry FROM projects WHERE name=?", (name,)).fetchone()
        return _from_json(row["entry"]) if row else None

    @surviving(False)
    def drop_project(self, name: str) -> bool:
        """Remove a project. True even if it was not there."""
        with self._lock, self._db:
            self._erase("projects", {name})
        return True

    @surviving([])
    def all_projects(self) -> List[Dict[str, Any]]:
        return self._entries("projects")

    # ------------------------------------------------------------- first fill
    def _fill_empty_tables(self) -> None:
        """
        Move configuration out of the JSON files into empty tables.

        Table by table rather than all-or-nothing: a fill interrupted partway
        leaves some tables populated and others not, and the next launch should
        finish the job instead of deciding the database is full and leaving the
        rest behind.
        """
        empty = {t: self._population(t) == 0
                 for t in ("bench", "devices", "setups")}
        if not any(empty.values()):
            return

        if not all(empty.values()):
            self.logger.warning(
                "The configuration database is partly populated (%s). A "
                "previous fill may have been interrupted; filling the rest.",
                ", ".join(f"{t}={'empty' if e else 'filled'}"
                          for t, e in empty.items()))

        self.logger.info("Populating %s", self._db_path.name)
        with self._lock, self._db:
            if empty["bench"]:
                for key, value in self.bench.items():
                    self._write_setting(key, value)
            if empty["devices"]:
                for entry in self.devices.get("devices", []):
                    self._write_device(entry)
            if empty["setups"]:
                chosen = self.setups.get("chosen", "")
                for entry in self.setups.get("setups", []):
                    self._write_setup(entry, entry.get("name") == chosen)

    # -------------------------------------------------------------- lifecycle
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        """Close the database. Safe to call more than once."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "ConfigStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()
