# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Two widgets the console draws itself: a state lamp and a live trace.

The lamp is hand-painted because it is one dot with a halo — a whole widget
class from a toolkit would be more code than the six lines of QPainter it
takes. The trace is pyqtgraph, for the opposite reason: pan, zoom, autorange,
peak downsampling and a real time axis are exactly what a plotting library is
for, and hand-rolling them gets slow and approximate long before it gets
complete.

Both take their colours from the palette dict rather than from the toolkit
default, so a theme change restyles the axes, the grid and the curve together
instead of leaving pyqtgraph's own grey behind.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QBrush

from industrybench.ui.theme import mono

# Set once, before any PlotWidget exists.
pg.setConfigOptions(antialias=True, useOpenGL=False)


class Lamp(QWidget):
    """A state dot with a soft halo around it."""

    def __init__(self, colour: QColor, diameter: int = 9, parent=None):
        super().__init__(parent)
        self._colour = colour
        self._diameter = diameter
        self.setFixedSize(diameter + 8, diameter + 8)

    def glow(self, colour: QColor):
        self._colour = colour
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = self.rect().center()
        radius = self._diameter // 2

        halo = QColor(self._colour)
        halo.setAlpha(55)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(middle, radius + 3, radius + 3)
        painter.setBrush(QBrush(self._colour))
        painter.drawEllipse(middle, radius, radius)


def plottable(value) -> bool:
    """True for a real number a trace can carry — not None, NaN, inf or text."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def readable(value) -> str:
    """Format one live value at a width the eye can track.

    Fixed-width by magnitude rather than by significant figures: a readout
    that changes length as it drifts across a power of ten makes the operator
    re-find the decimal point every second.
    """
    size = abs(value)
    if size == 0:
        return "0.000"
    if size < 0.001:
        return f"{value:.3e}"
    if size < 1:
        return f"{value:.4f}"
    if size < 100:
        return f"{value:.3f}"
    return f"{value:.2f}"


class Trace(QWidget):
    """One channel's live curve.

    Interaction is pyqtgraph's own: the wheel zooms, dragging pans, and
    right-click opens its view menu. The console's own view buttons drive the
    same view box rather than replacing it, so nothing here rebuilds a widget
    and a run in progress keeps both its data and its scroll position.

    The curve follows the newest sample until somebody pans by hand, and then
    stops — an operator who has scrolled back to look at something does not
    want the view yanked forward a second later.
    """

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.pal = palette
        self.series: list[float] = []
        #: x values, as unix seconds — the axis reads real wall-clock time
        self.times: list[float] = []
        self.unit = ""
        self._follow = True          # track the live edge until the user pans
        self._roi = None             # measurement band, when enabled
        self._roi_label = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setMinimumHeight(150)
        self.plot.setMouseEnabled(x=True, y=True)
        # peak downsampling keeps spikes visible while drawing far fewer points
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        layout.addWidget(self.plot)

        self.curve = self.plot.plot([], [])
        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._label = pg.TextItem(anchor=(0, 1))
        # A value under the crosshair is a machine value like any other, and it
        # changes with every pixel the pointer moves. Mono, so the digits hold
        # their columns instead of jittering as the cursor travels.
        self._label.setFont(mono(10))
        for item in (self._vline, self._label):
            item.setZValue(10)
            item.setVisible(False)
            self.plot.addItem(item, ignoreBounds=True)

        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                     rateLimit=60, slot=self._on_mouse)
        # panning by hand means "stop chasing the newest sample"
        self.plot.getViewBox().sigRangeChangedManually.connect(self._stop_following)

        self.restyle(palette)

    def _stop_following(self, *_):
        self._follow = False

    # -- theming --------------------------------------------------------------
    def restyle(self, palette: dict):
        self.pal = palette
        surface = palette.get("card", palette["ground"])
        self.plot.setBackground(QColor(surface))
        axis_pen = pg.mkPen(QColor(palette["rule"]), width=1)
        text_col = QColor(palette["faint"])
        for side in ("left", "bottom"):
            ax = self.plot.getAxis(side)
            ax.setPen(axis_pen)
            ax.setTextPen(text_col)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.curve.setPen(pg.mkPen(QColor(palette["curve"]), width=2))
        self._vline.setPen(pg.mkPen(text_col, width=1))
        self._label.setColor(QColor(palette["text"]))
        self.plot.setLabel("left", self.unit or "")

    # -- data -----------------------------------------------------------------
    def show_points(self, seconds, values, unit=""):
        """Draw an already-filtered, already-aligned pair of arrays.

        Filtering happens once in the plot stack, as each sample arrives, and
        the running arrays are kept there. Re-filtering the whole history on
        every repaint is what makes a refresh cost grow with the length of the
        run rather than with what changed.
        """
        self.unit = unit or ""
        self.plot.setLabel("left", self.unit)
        if len(values) < 2:
            return self.blank()

        self.series = values
        self.times = seconds
        self.curve.setData(np.asarray(seconds, dtype=float),
                           np.asarray(values, dtype=float))
        if self._follow:
            self.plot.enableAutoRange(axis="xy", enable=True)

    def blank(self):
        """Drop the curve without disturbing the view or the styling."""
        self.curve.setData([], [])
        self.series, self.times = [], []

    # -- crosshair ------------------------------------------------------------
    def _on_mouse(self, event):
        pos = event[0]
        if len(self.series) < 2 or not self.plot.sceneBoundingRect().contains(pos):
            self._vline.setVisible(False)
            self._label.setVisible(False)
            return

        point = self.plot.getViewBox().mapSceneToView(pos)
        x_data, y_data = self.curve.getData()
        if x_data is None or len(x_data) == 0:
            return
        i = int(np.clip(np.searchsorted(x_data, point.x()), 0, len(x_data) - 1))

        self._vline.setPos(float(x_data[i]))
        text = f"{readable(float(y_data[i]))} {self.unit}".strip()
        if len(self.times) == len(self.series):
            text += "\n" + datetime.fromtimestamp(float(x_data[i])).strftime("%H:%M:%S")
        self._label.setText(text)
        self._label.setPos(float(x_data[i]), float(y_data[i]))
        self._vline.setVisible(True)
        self._label.setVisible(True)

    # -- view control ---------------------------------------------------------
    def fit(self):
        """Re-fit to everything held, and resume following the live edge."""
        self._follow = True
        self.plot.enableAutoRange(axis="xy", enable=True)

    # -- measurement band -----------------------------------------------------
    def band(self, on: bool):
        """Show a draggable band that reports dx and dy across it.

        The band is a real region on the data, not a screen overlay, so it
        stays anchored to the samples it was placed on while the trace keeps
        updating underneath it.
        """
        if on and self._roi is None:
            (x_lo, x_hi), (y_lo, y_hi) = self.plot.getViewBox().viewRange()
            w, h = (x_hi - x_lo) * 0.25, (y_hi - y_lo) * 0.25
            self._roi = pg.RectROI([x_lo + w, y_lo + h], [w, h],
                                   pen=pg.mkPen(QColor(self.pal["focus"]), width=1),
                                   hoverPen=pg.mkPen(QColor(self.pal["gold"]), width=2))
            self._roi.addScaleHandle([1, 1], [0, 0])
            self._roi.addScaleHandle([0, 0], [1, 1])
            self._roi.setZValue(20)
            self.plot.addItem(self._roi)

            self._roi_label = pg.TextItem(anchor=(0, 1))
            self._roi_label.setFont(mono(10))
            self._roi_label.setColor(QColor(self.pal["text"]))
            self._roi_label.setZValue(21)
            self.plot.addItem(self._roi_label, ignoreBounds=True)

            self._roi.sigRegionChanged.connect(self._label_band)
            self._label_band()
        elif not on and self._roi is not None:
            self.plot.removeItem(self._roi)
            self.plot.removeItem(self._roi_label)
            self._roi = self._roi_label = None

    def _label_band(self, *_):
        if self._roi is None:
            return
        pos, size = self._roi.pos(), self._roi.size()
        dx, dy = abs(float(size[0])), abs(float(size[1]))
        # dx is in seconds when the x axis carries real timestamps
        dx_text = f"{dx:.3g} s" if len(self.times) == len(self.series) else f"{dx:.4g}"
        self._roi_label.setText(f"dx {dx_text}\ndy {readable(dy)} {self.unit}".strip())
        self._roi_label.setPos(float(pos[0]), float(pos[1]) + float(size[1]))

    def zoom(self, factor: float):
        """Scale the x view about its right edge (the newest sample)."""
        vb = self.plot.getViewBox()
        (x_lo, x_hi), _ = vb.viewRange()
        span = (x_hi - x_lo) * factor
        self._follow = False
        vb.setXRange(x_hi - span, x_hi, padding=0)
