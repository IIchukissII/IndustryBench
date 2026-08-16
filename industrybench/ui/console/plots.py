# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The plot column — the middle of the console, and why it has no modes.

One card per visible channel, on screen whenever there is anything to show.
The curves are kept here and appended to, so a refresh costs what has arrived
since the last one rather than what the whole run holds. A design that redrew
from the journal every half second would get slower the longer a run went on,
which is precisely backwards for a tool people leave running overnight.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from industrybench.ui.theme import PANE_INSET, mono
from industrybench.ui.widgets.readouts import Trace, plottable, readable

logger = logging.getLogger(__name__)


class PlotStack(QWidget):
    """A scrollable column of per-channel traces."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.pal = palette
        self._cards: Dict[str, QWidget] = {}
        self._traces: Dict[str, Trace] = {}
        self._readouts: Dict[str, QLabel] = {}
        self._hidden: set = set()
        #: column -> {"t": [seconds], "y": [values]}, the running curve
        self._curves: Dict[str, Dict[str, list]] = {}
        self._last_seen = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("Bare")
        self._column = QVBoxLayout(holder)
        self._column.setContentsMargins(PANE_INSET, PANE_INSET, PANE_INSET, PANE_INSET)
        self._column.setSpacing(12)

        self._nothing = QLabel("No channels yet — add a device on the left, "
                               "then press Start.")
        self._nothing.setObjectName("Subtle")
        self._nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._column.addWidget(self._nothing)
        self._column.addStretch()

        self._scroll.setWidget(holder)
        outer.addWidget(self._scroll)

    # ------------------------------------------------------------------ shape
    def set_channels(self, columns: List[str], sources: Dict[str, dict]):
        """Create and drop cards so the column matches *columns* exactly."""
        wanted = list(dict.fromkeys(columns))
        for gone in [c for c in self._cards if c not in wanted]:
            card = self._cards.pop(gone)
            self._traces.pop(gone, None)
            self._readouts.pop(gone, None)
            self._curves.pop(gone, None)
            card.setParent(None)
            card.deleteLater()

        for index, column in enumerate(wanted):
            if column not in self._cards:
                self._column.insertWidget(
                    index, self._card(column, sources.get(column, {})))
        self._nothing.setVisible(not wanted)
        for column in wanted:
            self._apply_visibility(column)

    def _card(self, column: str, source: dict) -> QWidget:
        card = QWidget()
        card.setObjectName("Panel")
        card.setMinimumHeight(200)
        box = QVBoxLayout(card)
        box.setContentsMargins(PANE_INSET, 11, PANE_INSET, 10)
        box.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel(column)
        title.setStyleSheet("font-weight:700;font-size:14px;")
        device = QLabel(source.get("device", ""))
        device.setObjectName("Subtle")
        device.setFont(mono(10))
        value = QLabel("—")
        value.setFont(mono(15))
        value.setStyleSheet("font-weight:700;")
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        head.addWidget(title)
        head.addWidget(device)
        head.addStretch()
        head.addWidget(value)
        box.addLayout(head)

        trace = Trace(self.pal)
        box.addWidget(trace, 1)

        self._cards[column] = card
        self._traces[column] = trace
        self._readouts[column] = value
        return card

    # ------------------------------------------------------------- visibility
    def set_visible(self, column: str, visible: bool):
        if visible:
            self._hidden.discard(column)
        else:
            self._hidden.add(column)
        self._apply_visibility(column)

    def _apply_visibility(self, column: str):
        card = self._cards.get(column)
        if card is not None:
            card.setVisible(column not in self._hidden)

    # -------------------------------------------------------------- live data
    def absorb(self, scans, depth: int):
        """Fold only the scans not seen before into the running curves.

        Identity, not equality: two scans a second apart can hold identical
        numbers, and comparing by value would drop the second one.
        """
        start = 0
        if self._last_seen is not None:
            for i in range(len(scans) - 1, -1, -1):
                if scans[i] is self._last_seen:
                    start = i + 1
                    break
            else:
                # The scan we were tracking has fallen out of the journal's
                # window, so what we hold cannot be extended — start again.
                self._curves.clear()
        else:
            self._curves.clear()

        for scan in scans[start:]:
            seconds = scan.taken_at.timestamp()
            for column, cell in scan:
                if not plottable(cell.value):
                    continue
                curve = self._curves.get(column)
                if curve is None:
                    curve = self._curves[column] = {"t": [], "y": []}
                curve["t"].append(seconds)
                curve["y"].append(cell.value)

        if scans:
            self._last_seen = scans[-1]
        for curve in self._curves.values():
            if len(curve["y"]) > depth:
                del curve["t"][:-depth]
                del curve["y"][:-depth]

    def redraw(self, latest) -> Dict[str, tuple]:
        """Push the running curves to the traces.

        Returns column -> (text, trustworthy) so the channel tree can show the
        same numbers without formatting them a second time.
        """
        readouts: Dict[str, tuple] = {}
        for column, trace in self._traces.items():
            cell = latest.cell(column) if latest is not None else None
            value = cell.value if cell is not None else None
            unit = cell.unit if cell is not None else ""
            trustworthy = plottable(value)
            text = f"{readable(value)} {unit}".strip() if trustworthy else "—"
            readouts[column] = (text, trustworthy)

            label = self._readouts.get(column)
            if label is not None:
                label.setText(text)
                label.setStyleSheet(
                    "font-weight:700;" if trustworthy else
                    f"font-weight:700;color:{self.pal.get('suspect', self.pal['caution'])};")

            if column in self._hidden:
                continue                    # do not pay for what is not shown
            curve = self._curves.get(column)
            if curve:
                trace.show_points(curve["t"], curve["y"], unit)
        return readouts

    def clear(self):
        self._curves.clear()
        self._last_seen = None
        for column, trace in self._traces.items():
            trace.blank()
            self._readouts[column].setText("—")

    def fit_all(self):
        for trace in self._traces.values():
            trace.fit()

    def zoom(self, factor: float):
        for trace in self._traces.values():
            trace.zoom(factor)

    def show_band(self, on: bool):
        for trace in self._traces.values():
            trace.band(on)

    def restyle(self, palette: dict):
        self.pal = palette
        for trace in self._traces.values():
            trace.restyle(palette)
