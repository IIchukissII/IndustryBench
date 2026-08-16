# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JSON Lines, one object per scan.

One document per run would have to be rewritten from the top every time it
grew, because a JSON array is only valid once it is closed — cost proportional
to the run so far, paid on every row. JSON Lines has no such closing bracket:
each scan is one object on one line, appended and forgotten.

What that buys, beyond the append, is that every row carries its own channels.
Unlike the delimited sink there is no grid to fix and no latecomer to turn
away: a channel added mid-run simply appears in the objects written after it,
and a consumer reading line by line sees exactly when that happened.

A run interrupted mid-write leaves a truncated last line and nothing else
damaged — every complete line before it still parses on its own.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from industrybench.acquisition.scan import Scan
from industrybench.sinks.base import FileSink


class JsonSink(FileSink):
    """One JSON object per line, appended as the run goes."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        #: JSON Lines is one object per line by definition, so indentation is
        #: not offered here — a pretty-printed object would span lines and stop
        #: the file being readable a line at a time.
        self.ensure_ascii: bool = bool(self.settings.get("ascii_only", False))

    def open(self) -> bool:
        if self.claim_path() is None:
            return False
        try:
            self.handle = open(self.path, "w", encoding="utf-8")
        except OSError as e:
            return self.failed(f"Cannot open {self.path}", e)

        self.written = 0
        self.ready = True
        return True

    def write(self, scan: Scan) -> bool:
        if not self.ready:
            return False
        try:
            line = json.dumps(scan.as_json(), ensure_ascii=self.ensure_ascii)
        except (TypeError, ValueError) as e:
            # A scan that cannot be encoded is one row, not the run. Report it
            # and keep the file open for the rows that follow.
            return self.failed("Cannot encode a scan as JSON", e)

        try:
            self.handle.write(line + "\n")
            if self.flush_each:
                self.handle.flush()
        except OSError as e:
            return self.failed(f"Cannot write to {self.path}", e)

        self.written += 1
        return True
