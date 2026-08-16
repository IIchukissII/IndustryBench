# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sink contract: somewhere a scan is written locally.

A sink is deliberately thin. It is not the durable copy of a run — the outbox
is, and it is written first — so a sink that fails takes a line in the log and
nothing else. That is why there is no error ledger, no retry and no status
record here: everything a sink could report is either already in the log or
already in the outbox, and a second bookkeeping surface would only disagree
with them.

Three moments make up its life:

    sink.open()        a run is starting — claim a file
    sink.write(scan)   one row
    sink.close()       the run ended — finish the document

``open`` is called per run, not per process. Each run gets its own file, named
from the moment it started, so two runs never interleave rows in one document
and a file's name says when its contents were taken.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from industrybench import paths
from industrybench.acquisition.scan import Scan


class Sink(ABC):
    """Where a run's scans are written on this machine."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.settings: Dict[str, Any] = self.config.get("settings", {}) or {}
        self.enabled: bool = bool(self.config.get("enabled", True))
        self.logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        #: rows accepted since the last open()
        self.written = 0
        #: False until open() succeeds, and again after close()
        self.ready = False

    # ------------------------------------------------------------ the contract
    @abstractmethod
    def open(self) -> bool:
        """Claim a destination for a new run. False if it cannot be claimed."""

    @abstractmethod
    def write(self, scan: Scan) -> bool:
        """Record one scan. False if it could not be recorded."""

    @abstractmethod
    def close(self) -> bool:
        """Finish the document. False if the tail may not have reached disk."""

    # ---------------------------------------------------------------- reporting
    def failed(self, what: str, error: Exception) -> bool:
        """Log a failure and answer False, which every caller reads as 'no'.

        Returning through here rather than raising: a run must survive a full
        disk. The reading itself is already in the outbox by the time a sink is
        asked for it.
        """
        self.logger.error("%s: %s", what, error)
        return False

    def __repr__(self) -> str:
        state = "ready" if self.ready else "idle"
        return f"<{type(self).__name__} {state}, {self.written} rows>"


class FileSink(Sink):
    """A sink that writes one file per run.

    Subclasses get the path handling: a ``{timestamp}`` in the configured path
    is filled in when the run starts, the parent directory is created, and a
    relative path is anchored to the workspace rather than to whatever
    directory the app happened to be launched from.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.template: str = self.settings.get("path", "")
        self.path: Optional[Path] = None
        self.handle = None
        #: flush after every row. Off by default — Python's buffering is
        #: enough when close() is reached, and the outbox holds the copy that
        #: has to survive a hard kill.
        self.flush_each = bool(self.config.get("auto_flush", False))

    def claim_path(self) -> Optional[Path]:
        """Resolve this run's file name. None if no path is configured."""
        if not self.template:
            self.logger.error("%s has no path configured", type(self).__name__)
            return None

        stamped = self.template.replace(
            "{timestamp}", datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        path = paths.resolve(stamped)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return self.failed(f"Cannot create {path.parent}", e) or None

        self.path = path
        self.logger.info("Writing this run to %s", path)
        return path

    def close(self) -> bool:
        self.ready = False
        if self.handle is None:
            return True
        try:
            self.handle.close()
            return True
        except OSError as e:
            return self.failed(f"Cannot close {self.path}", e)
        finally:
            self.handle = None
