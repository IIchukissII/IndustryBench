# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The run bar — everything about the run itself, on one line, always reachable.

Run control and view control are different kinds of thing, so they are kept
apart: the run owns this bar, and the view controls sit with the plots they
act on. Start, cadence, saving and the counters all belong to the run and stay
on one line, always reachable.

Delivery status lives here because it is a property of the run: an operator
needs to see that readings are leaving the bench without going looking.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from industrybench.ui.theme import mono
from industrybench.ui.widgets.readouts import Lamp
from industrybench.ui.theme import PANE_INSET

logger = logging.getLogger(__name__)


class RunBar(QWidget):
    """Start/stop, cadence, live counters and delivery state."""

    run_toggled = pyqtSignal()
    interval_changed = pyqtSignal(float)
    save_toggled = pyqtSignal(bool)
    clear_requested = pyqtSignal()
    session_requested = pyqtSignal()

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.pal = palette
        self.setObjectName("HeadBar")
        self.setFixedHeight(52)

        row = QHBoxLayout(self)
        row.setContentsMargins(PANE_INSET, 0, PANE_INSET, 0)
        row.setSpacing(12)

        self.run = QPushButton("▶  Start")
        self.run.setMinimumWidth(112)
        self.run.setMinimumHeight(32)
        self.run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run.clicked.connect(self.run_toggled.emit)
        row.addWidget(self.run)

        row.addWidget(self._label("every"))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 3600.0)
        self.interval.setDecimals(1)
        self.interval.setSingleStep(0.1)
        self.interval.setSuffix(" s")
        self.interval.setFixedWidth(88)
        self.interval.setToolTip("Takes effect on the next cycle, even mid-run")
        self.interval.valueChanged.connect(self.interval_changed.emit)
        row.addWidget(self.interval)

        self.save = QCheckBox("Save to file")
        self.save.setToolTip("Off = preview: live traces only, nothing written")
        self.save.toggled.connect(self.save_toggled.emit)
        row.addWidget(self.save)

        row.addSpacing(8)
        self.counts = QLabel("—")
        self.counts.setFont(mono(11))
        self.counts.setToolTip("scans recorded this run, and beats with no "
                               "reading to record")
        row.addWidget(self.counts)

        row.addStretch()

        self.delivery_lamp = Lamp(QColor(self.pal["faint"]), 8)
        row.addWidget(self.delivery_lamp)
        self.delivery = QLabel("not delivering")
        self.delivery.setObjectName("Subtle")
        self.delivery.setFont(mono(10))
        row.addWidget(self.delivery)

        row.addSpacing(10)
        clear = QPushButton("Clear")
        clear.setProperty("quiet", True)
        clear.setToolTip("Reset the traces. Saved files and queued readings are untouched.")
        clear.clicked.connect(self.clear_requested.emit)
        row.addWidget(clear)

        session = QPushButton("Session…")
        session.setProperty("quiet", True)
        session.setToolTip("Equipment under test, output files, and the platform link")
        session.clicked.connect(self.session_requested.emit)
        row.addWidget(session)

    def _label(self, text) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Subtle")
        return lab

    # ------------------------------------------------------------------ state
    def set_running(self, running: bool):
        self.run.setText("■  Stop" if running else "▶  Start")
        self.run.setProperty("stop", running)
        self.run.setProperty("go", not running)
        self.run.style().unpolish(self.run)
        self.run.style().polish(self.run)
        self.save.setEnabled(not running)

    def set_counts(self, scans: int, missed: int):
        """Scans recorded this run, and beats that produced nothing."""
        self.counts.setText(f"{scans:,} scans · {missed:,} idle")
        self.counts.setStyleSheet(
            f"color:{self.pal['alarm']};" if missed else f"color:{self.pal['muted']};")

    def set_delivery(self, pending: int, forwarding: bool, error: str = ""):
        if error:
            self.delivery_lamp.glow(QColor(self.pal["alarm"]))
            self.delivery.setText(f"delivery failing · {pending:,} queued")
        elif forwarding:
            self.delivery_lamp.glow(QColor(self.pal["live"]))
            self.delivery.setText(f"delivering · {pending:,} queued")
        elif pending:
            self.delivery_lamp.glow(QColor(self.pal["faint"]))
            self.delivery.setText(f"{pending:,} queued locally")
        else:
            self.delivery_lamp.glow(QColor(self.pal["faint"]))
            self.delivery.setText("local only")

    def set_interval_quietly(self, seconds: float):
        self.interval.blockSignals(True)
        self.interval.setValue(float(seconds))
        self.interval.blockSignals(False)

    def restyle(self, palette: dict):
        self.pal = palette
