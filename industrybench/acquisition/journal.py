# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The local journal: what the run keeps on this machine.

Two jobs, both local, both losable:

*The window.* A bounded ring of the most recent scans, which the console reads
on its refresh timer. Bounded because a run left going overnight would
otherwise grow a list until the process died of it, and nothing draws a trace
from an hour ago anyway.

*The file.* Each scan is copied to whichever sinks the session configured.

Neither is the durable copy. A reading reaches ``edge.outbox`` the moment its
instrument produces it — before assembly, before any of this — so a sink that
fails, a buffer that evicts, or a preview run that writes nothing at all costs
the platform nothing. That ordering is the reason this module is allowed to be
as forgiving as it is: it reports a failed write and carries on, because the
alternative would be stopping a run over a file.

The journal buffers in every mode, including preview. The buffer is what the
plots are drawn from, and a preview run that filled no buffer would be a run
you could not watch — which is the only thing preview is for.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Dict, List, Optional, Tuple

from industrybench.acquisition.scan import Scan
from industrybench.sinks.base import Sink

#: scans held for the display, about three hours at one per second
WINDOW = 10_000


class Journal:
    """The recent scans, and the sinks each new one is copied to."""

    def __init__(self, window: int = WINDOW):
        self.logger = logging.getLogger(__name__)
        self.sinks: Dict[str, Sink] = {}

        self._lock = threading.Lock()
        self._window: deque = deque(maxlen=window)
        #: total ever added, never reset — `since` is a cursor into this count
        self._count = 0

        self.kept = 0
        self.write_failures = 0

    # ------------------------------------------------------------------- sinks
    def attach(self, name: str, sink: Sink) -> None:
        """Add a destination. An existing name is replaced and closed."""
        previous = self.sinks.get(name)
        if previous is not None:
            previous.close()
        self.sinks[name] = sink
        self.logger.info("Output '%s' is %s", name, type(sink).__name__)

    def open_sinks(self) -> bool:
        """Claim a file for each enabled sink. False if any could not open.

        Every sink is tried even after one fails, so the log names all of them
        at once instead of one per attempted run.
        """
        opened = True
        for name, sink in self.sinks.items():
            if not sink.enabled:
                continue
            if not sink.open():
                self.logger.error("Output '%s' could not be opened", name)
                opened = False
        return opened

    def close_sinks(self) -> bool:
        """Finish every document. False if any tail may be missing."""
        closed = True
        for name, sink in self.sinks.items():
            if not sink.close():
                self.logger.error("Output '%s' did not close cleanly", name)
                closed = False
        return closed

    # -------------------------------------------------------------------- rows
    def keep(self, scan: Scan, to_file: bool = True) -> bool:
        """Buffer a scan, and copy it to the sinks unless this is a preview.

        Returns whether it reached every enabled sink. The buffer is updated
        either way — a row that could not be written is still a row that was
        measured, and the operator should see it on the trace while they work
        out why the file is not growing.
        """
        if not scan:
            return False

        with self._lock:
            self._window.append(scan)
            self._count += 1
            self.kept += 1

        if not to_file:
            return True

        written = True
        for name, sink in self.sinks.items():
            if not (sink.enabled and sink.ready):
                continue
            try:
                if not sink.write(scan):
                    written = False
            except Exception:
                self.logger.exception("Output '%s' raised while writing", name)
                written = False

        if not written:
            self.write_failures += 1
        return written

    # ------------------------------------------------------------------ window
    def recent(self, count: Optional[int] = None) -> List[Scan]:
        """The last *count* scans, or the whole window."""
        with self._lock:
            held = list(self._window)
        if count is None or count >= len(held):
            return held
        return held[-count:]

    def since(self, cursor: int) -> Tuple[List[Scan], int]:
        """Scans added after *cursor*, and the cursor to pass in next time.

        Pass 0 to start. If the caller has fallen further behind than the
        window is deep the whole window comes back, so a slow consumer resyncs
        rather than silently skipping the scans that were evicted meanwhile.
        """
        with self._lock:
            now = self._count
            held = list(self._window)

        fresh = now - cursor
        if fresh <= 0:
            return [], now
        if fresh >= len(held):
            return held, now
        return held[-fresh:], now

    def forget(self) -> None:
        """Drop the window. Files and queued readings are untouched."""
        with self._lock:
            self._window.clear()
        self.logger.info("Display window cleared")

    def counters(self) -> Dict[str, int]:
        with self._lock:
            held = len(self._window)
        return {
            "kept": self.kept,
            "held": held,
            "write_failures": self.write_failures,
            "sinks": len(self.sinks),
        }
