# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Delimited text, one row per scan.

A delimited file is a fixed grid: the header is written once and every row
after it is read by position. Scans are not fixed — a channel can be added
while a run is going, and the assembler emits whatever the readers currently
hold — so this sink pins the grid to the first scan of the run and keeps every
later row in that shape.

The alternative, writing each row's own columns in whatever order they arrive,
produces a file whose header stops describing its rows the moment the
instrument set changes: numbers slide under the wrong heading with nothing in
the file to show it happened. So a channel that appears mid-run is named in the
log and left out of this file. Nothing is lost by that — the reading is already
queued for the platform, which has no grid to keep — and the next run's file
picks the column up.
"""

from __future__ import annotations

import csv
from typing import Any, Dict, List, Optional, Set

from industrybench.acquisition.scan import Scan
from industrybench.sinks.base import FileSink

TIME_COLUMN = "taken_at"


class CsvSink(FileSink):
    """One delimited file per run, one line per scan."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.delimiter: str = self.settings.get("delimiter", ",")
        self.with_header: bool = bool(self.settings.get("header", True))
        self.with_time: bool = bool(self.settings.get("timestamp_column", True))

        self._rows = None
        #: the grid, fixed by the first scan of the run
        self._grid: Optional[List[str]] = None
        #: channels that arrived too late for the grid, reported once each
        self._latecomers: Set[str] = set()

    # -------------------------------------------------------------------- run
    def open(self) -> bool:
        if self.claim_path() is None:
            return False
        try:
            self.handle = open(self.path, "w", newline="", encoding="utf-8")
        except OSError as e:
            return self.failed(f"Cannot open {self.path}", e)

        self._rows = csv.writer(self.handle, delimiter=self.delimiter)
        self._grid = None
        self._latecomers.clear()
        self.written = 0
        self.ready = True
        return True

    def close(self) -> bool:
        self._rows = None
        return super().close()

    # ------------------------------------------------------------------- rows
    def write(self, scan: Scan) -> bool:
        if not self.ready:
            return False
        try:
            if self._grid is None:
                self._fix_grid(scan)
            self._rows.writerow(self._line(scan))
            if self.flush_each:
                self.handle.flush()
        except (OSError, ValueError) as e:
            return self.failed(f"Cannot write to {self.path}", e)

        self.written += 1
        return True

    def _fix_grid(self, first: Scan) -> None:
        """Take the column layout from the first scan and write the header.

        Sorted, not in assembly order: the assembler's order follows whichever
        reader happened to report first, so two runs of the same bench would
        otherwise produce files whose columns differ by a race.
        """
        self._grid = sorted(first.columns)
        if self.with_header:
            head = ([TIME_COLUMN] if self.with_time else []) + self._grid
            self._rows.writerow(head)

    def _line(self, scan: Scan) -> list:
        line = [scan.taken_at.isoformat()] if self.with_time else []
        for column in self._grid:
            cell = scan.cell(column)
            # A channel absent from this scan leaves its position empty rather
            # than shifting the ones after it.
            line.append("" if cell is None else cell.value)
        self._note_latecomers(scan)
        return line

    def _note_latecomers(self, scan: Scan) -> None:
        arrived = [c for c in scan.columns
                   if c not in self._grid and c not in self._latecomers]
        if not arrived:
            return
        self._latecomers.update(arrived)
        self.logger.warning(
            "%s: %s appeared after this file's header was fixed and will not be "
            "in it. The readings are queued for the platform; start a new run "
            "to record them here too.",
            self.path, ", ".join(sorted(arrived)))
