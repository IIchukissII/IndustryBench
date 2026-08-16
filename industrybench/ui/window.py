# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console window: one surface, three panes, no modes.

The traces are on screen whenever there is anything to draw, and configuration
happens beside them rather than instead of them. That is the entire premise —
you change a range and watch what it did to the signal. A design that made
setting-up and watching mutually exclusive means adjusting an instrument
blind, which for a measurement tool is the wrong way round.

Left, the channel tree: what is on the bench and what it is reading. Middle,
the traces. Right, the inspector, editing whatever is selected. Across the top,
the run bar, which owns Start, the cadence and the delivery state.

Edits from the inspector are written straight through to the configuration
store, and a change to the device set is taken up by the running session, so a
channel can be added without stopping.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from industrybench.acquisition.runner import Runner
from industrybench.config.store import ConfigStore
from industrybench.ui.branding import APP_NAME, VERSION, logo_pixmap
from industrybench.ui.console.channels import ChannelTree
from industrybench.ui.console.inspector import QUERY_FOR, Inspector
from industrybench.ui.console.plots import PlotStack
from industrybench.ui.console.runbar import RunBar
from industrybench.ui.console.session import SessionDialog
from industrybench.ui.theme import PANE_INSET, mono, paint
from industrybench.ui.workers.link_check import LinkProbe

logger = logging.getLogger(__name__)

#: how often the console re-reads the run. Fast enough to feel live, slow
#: enough that a bench with thirty channels is not redrawing constantly.
REFRESH_MS = 500

#: bounds on how much of a trace is kept, whatever the configuration says
MIN_DEPTH, MAX_DEPTH = 10, 5000


class Console(QMainWindow):
    """The IndustryBench console."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

        self.config = ConfigStore()
        self.runner = Runner(self.config)
        self._probes = []
        self._session = None

        stored = self.config.bench.get("console", {}).get("theme", "dark")
        self.theme = stored if stored in ("light", "dark") else "dark"
        self.pal = paint(self.application(), self.theme)

        self.setWindowTitle(APP_NAME)
        self.resize(1320, 820)
        self._menus()
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)
        self._reload_channels()
        self._refresh()
        self.logger.info("Console ready (theme=%s)", self.theme)

    @staticmethod
    def application():
        from PyQt6.QtWidgets import QApplication
        return QApplication.instance()

    # ------------------------------------------------------------------ build
    def _menus(self):
        bar = self.menuBar()

        session_menu = bar.addMenu("Session")
        open_session = QAction("Open…", self)
        open_session.triggered.connect(self._open_session)
        session_menu.addAction(open_session)
        leave = QAction("Exit", self)
        leave.triggered.connect(self.close)
        session_menu.addAction(leave)

        view = bar.addMenu("View")
        for label, action in (("Fit every trace", lambda: self.plots.fit_all()),
                              ("Zoom in", lambda: self.plots.zoom(0.8)),
                              ("Zoom out", lambda: self.plots.zoom(1.25))):
            entry = QAction(label, self)
            entry.triggered.connect(action)
            view.addAction(entry)

        help_menu = bar.addMenu("Help")
        about = QAction("About", self)
        about.triggered.connect(self._about_box)
        help_menu.addAction(about)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._title_bar())

        self.runbar = RunBar(self.pal)
        self.runbar.run_toggled.connect(self._run_button)
        self.runbar.interval_changed.connect(self._set_interval)
        self.runbar.clear_requested.connect(self._clear)
        self.runbar.session_requested.connect(self._open_session)
        outer.addWidget(self.runbar)

        self.channels = ChannelTree(self.pal)
        self.channels.selected.connect(self._selection_changed)
        self.channels.visibility_changed.connect(self._visibility_changed)
        self.channels.add_device_requested.connect(self._add_device)

        self.plots = PlotStack(self.pal)

        self.inspector = Inspector(self.pal)
        self.inspector.edited.connect(self._persist)
        self.inspector.check_requested.connect(self._check_link)
        self.inspector.remove_requested.connect(self._remove)
        self.inspector.add_channel_requested.connect(self._add_channel)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.channels)
        split.addWidget(self.plots)
        split.addWidget(self.inspector)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 760, 300])
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        self.runbar.set_interval_quietly(
            float(self.config.get_run_settings().get("interval", 1.0)))

    def _title_bar(self) -> QWidget:
        strip = QWidget()
        strip.setObjectName("HeadBar")
        strip.setFixedHeight(46)
        row = QHBoxLayout(strip)
        row.setContentsMargins(PANE_INSET, 0, PANE_INSET, 0)
        row.setSpacing(10)

        self.logo = QLabel()
        self.logo.setFixedSize(24, 24)
        row.addWidget(self.logo)
        name = QLabel(APP_NAME)
        name.setObjectName("WordMark")
        row.addWidget(name)
        row.addStretch()

        self.subject = QLabel("no equipment chosen")
        self.subject.setObjectName("Subtle")
        self.subject.setFont(mono(10))
        row.addWidget(self.subject)
        row.addSpacing(12)

        # A segmented control rather than two loose buttons: SegBtn paints
        # nothing until it is checked, so without the #Seg bed behind it the
        # theme switch reads as blank space.
        segment = QWidget()
        segment.setObjectName("Toggle")
        inner = QHBoxLayout(segment)
        inner.setContentsMargins(3, 3, 3, 3)
        inner.setSpacing(2)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_buttons = {}
        for theme, label in (("light", "☀ Light"), ("dark", "☾ Dark")):
            button = QPushButton(label)
            button.setObjectName("ToggleBtn")
            button.setCheckable(True)
            button.setChecked(theme == self.theme)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, t=theme: self.use_theme(t))
            self._theme_group.addButton(button)
            inner.addWidget(button)
            self._theme_buttons[theme] = button
        row.addWidget(segment)

        self._draw_logo()
        return strip

    def _draw_logo(self):
        pixmap = logo_pixmap(self.theme, 24, self.devicePixelRatioF() or 1.0)
        if pixmap.isNull():
            self.logo.hide()
        else:
            self.logo.setPixmap(pixmap)
            self.logo.show()

    # ------------------------------------------------------------------ model
    def _devices(self):
        return self.config.devices.setdefault("devices", [])

    def _reload_channels(self):
        devices = self._devices()
        self.channels.rebuild(devices)
        columns, sources = [], {}
        for device in devices:
            for channel in device.get("channels", []) or []:
                column = channel.get("column")
                if column:
                    columns.append(column)
                    sources[column] = {"device": device.get("name", "")}
        self.plots.set_channels(columns, sources)

    def _persist(self):
        """Inspector edits are written straight through — one commit model."""
        if not self.config.save_devices():
            QMessageBox.critical(self, "Not saved",
                                 "The configuration could not be written. The "
                                 "log says why; the file on disk is unchanged.")
            return
        try:
            self.runner.reload_devices()
            self.runner.map_channels()
        except Exception:
            self.logger.exception("Could not take up the edit")
        self._reload_channels()

    # -------------------------------------------------------------- structure
    def _add_device(self):
        taken = {d.get("name", "") for d in self._devices()}
        name, n = "New device", 2
        while name in taken:
            name, n = f"New device {n}", n + 1
        self._devices().append({
            "name": name, "kind": "scpi_visa", "enabled": True,
            "address": "", "timeout_ms": 5000,
            "read_terminator": "\n", "write_terminator": "\n",
            "channels": [],
        })
        self._persist()
        self.channels.select_key(("device", name, None))

    def _add_channel(self, device):
        from industrybench.edge.identity import new_sensor_id
        taken = {c.get("column") for d in self._devices()
                 for c in d.get("channels", []) or []}
        column, n = "channel_1", 2
        while column in taken:
            column, n = f"channel_{n}", n + 1
        device.setdefault("channels", []).append({
            "measures": "DC Voltage", "query": QUERY_FOR["DC Voltage"],
            "column": column, "unit": "V", "range": "AUTO",
            "enabled": True, "sensor_id": new_sensor_id(),
        })
        self._persist()
        self.channels.select_key(("channel", device.get("name"), column))

    def _remove(self, device, channel):
        what = (f"channel '{channel.get('column')}'" if channel
                else f"device '{device.get('name')}' and its channels")
        if QMessageBox.question(self, "Remove", f"Remove {what}?") \
                != QMessageBox.StandardButton.Yes:
            return
        if channel is None:
            self.config.devices["devices"] = [
                d for d in self._devices() if d is not device]
        else:
            device["channels"] = [
                c for c in device.get("channels", []) if c is not channel]
        self._persist()

    # ---------------------------------------------------------------- actions
    def _selection_changed(self, selection):
        self.inspector.show_selection(selection)

    def _visibility_changed(self, column, visible):
        self.plots.set_visible(column, visible)

    def _run_button(self):
        if self.runner.running:
            self.runner.stop()
        elif not self.runner.start(write=self.runbar.save.isChecked()):
            QMessageBox.warning(
                self, "The run did not start",
                "No device could be opened, or the output file could not be "
                "claimed. The channel tree shows which devices are on, and the "
                "log has the reason.")
        self.runbar.set_running(self.runner.running)

    def _set_interval(self, seconds: float):
        try:
            self.config.set_run_settings({"interval": float(seconds)})
        except Exception:
            self.logger.exception("Could not save the interval")

    def _clear(self):
        try:
            self.runner.forget()
        except Exception:
            self.logger.exception("Could not clear the display")
        self.plots.clear()
        self.channels.show_nothing()

    def _open_session(self):
        if self._session is None:
            self._session = SessionDialog(self.config, self.pal, self.runner, self)
        self._session.reread()
        self._session.exec()
        self._reload_channels()

    def _check_link(self, device):
        """Ask a device to identify itself, off the GUI thread."""
        if device.get("kind") != "scpi_visa":
            self.inspector.report("Simulated device — there is no link to check.")
            return
        address = (device.get("address") or "").strip()
        if not address:
            self.inspector.report("No address set.")
            return

        self.inspector.report("checking…")
        probe = LinkProbe(address, int(device.get("timeout_ms", 5000)), parent=self)
        # Held until it reports: a QThread garbage-collected while running
        # takes the interpreter with it.
        self._probes.append(probe)

        def answered(result, keep=probe):
            self.inspector.report(
                f"{result.identity}  ({result.took_ms:.0f} ms)"
                if result.reached else result.trouble or "The link failed.")
            if keep in self._probes:
                self._probes.remove(keep)

        probe.answered.connect(answered)
        probe.start()

    # ---------------------------------------------------------------- polling
    def _refresh(self):
        try:
            self._refresh_once()
        except Exception:
            self.logger.exception("The console could not refresh")

    def _refresh_once(self):
        report = self.runner.report()
        self.runbar.set_running(self.runner.running)
        self.runbar.set_counts(report.get("scans", 0),
                               report.get("empty_beats", 0))

        outbox = self.runner.outbox.stats()
        self.runbar.set_delivery(outbox.get("pending", 0),
                                 outbox.get("forwarding", False),
                                 outbox.get("last_error", ""))

        subject = self.runner.equipment_name or ""
        self.subject.setText(f"equipment · {subject}" if subject
                             else "no equipment chosen")

        depth = self._depth()
        scans = self.runner.recent(depth)
        if not scans:
            self.channels.show_nothing()
            return
        self.plots.absorb(scans, depth)
        self.channels.show_values(self.plots.redraw(scans[-1]))

    def _depth(self) -> int:
        """How much of a trace to keep, clamped to something drawable."""
        try:
            asked = int(self.config.get_run_settings().get("window", 1000))
        except (TypeError, ValueError):
            asked = 1000
        return max(MIN_DEPTH, min(MAX_DEPTH, asked))

    # ------------------------------------------------------------------ theme
    def use_theme(self, name):
        if name == self.theme:
            return
        self.theme = name
        self.pal = paint(self.application(), name)
        if name in self._theme_buttons:
            self._theme_buttons[name].setChecked(True)
        self._draw_logo()
        for pane in (self.runbar, self.channels, self.inspector, self.plots):
            pane.restyle(self.pal)
        self._reload_channels()
        # Re-emitted through the tree so the inspector gets the real selection
        # rather than a rebuilt row that points at a replaced dict.
        self.channels.announce_selection()
        try:
            self.config.bench.setdefault("console", {})["theme"] = name
            self.config.save_bench()
        except Exception:
            self.logger.exception("Could not save the theme")

    def _about_box(self):
        box = QMessageBox(self)
        box.setWindowTitle("About")
        pixmap = logo_pixmap(self.theme, 72, self.devicePixelRatioF() or 1.0)
        if not pixmap.isNull():
            box.setIconPixmap(pixmap)
        box.setText(
            f"{APP_NAME} {VERSION}\n\n"
            "The bench instrument layer for IndustryFlow:\n"
            "set up instruments, measure, and deliver the readings.\n"
            "This bench is the gateway.\n\nBuilt with PyQt6.")
        box.exec()

    def closeEvent(self, event):
        if self.runner.running:
            answer = QMessageBox.question(
                self, "Close", "A run is in progress. Stop it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.stop()
        event.accept()
