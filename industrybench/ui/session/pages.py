# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The session pages: what is under test, where the file goes, and the link.

These are the facts that hold for a whole run rather than for one channel, and
unlike a channel edit — which the inspector applies the moment you leave the
field — each of these keeps an explicit Apply. Changing which project a run
belongs to changes what every reading after it is attributed to, and that is
worth one deliberate click rather than a keystroke.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from industrybench.ui.theme import mono

logger = logging.getLogger(__name__)

#: the output formats the bench can write. Deliberately short: a bench keeps a
#: plain local file, and the time-series store is IndustryFlow's job, reached
#: through the outbox. A local database sink would only duplicate it.
OUTPUT_KINDS = [("Delimited text (.csv)", "delimited"),
                ("JSON Lines (.jsonl)", "jsonl")]


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    return label


def _title(title: str, sub: str) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setContentsMargins(18, 15, 18, 8)
    box.setSpacing(3)
    heading = QLabel(title)
    heading.setObjectName("PaneHead")
    note = QLabel(sub)
    note.setObjectName("PaneNote")
    note.setWordWrap(True)
    box.addWidget(heading)
    box.addWidget(note)
    return box


class SessionPage(QWidget):
    """Shared scaffold: a heading, a body, and an Apply footer."""

    def __init__(self, config, title, sub, parent=None):
        super().__init__(parent)
        self.config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(_title(title, sub))

        self.body = QVBoxLayout()
        self.body.setContentsMargins(18, 6, 18, 6)
        self.body.setSpacing(14)
        outer.addLayout(self.body, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(18, 10, 18, 12)
        self.note = QLabel("")
        self.note.setObjectName("Subtle")
        self.apply_button = QPushButton("Apply")
        self.apply_button.setProperty("primary", True)
        self.apply_button.clicked.connect(self.apply)
        footer.addWidget(self.note)
        footer.addStretch()
        footer.addWidget(self.apply_button)
        outer.addLayout(footer)

    def say(self, ok=True, message="Saved"):
        self.note.setText(message if ok else "Could not save")

    def apply(self):
        raise NotImplementedError

    def reread(self):
        """Re-read anything that may have changed elsewhere. Optional."""


# --------------------------------------------------------------------------- #
# Where a run is written
# --------------------------------------------------------------------------- #

class OutputPage(SessionPage):
    """The one local file a run writes."""

    def __init__(self, config, palette, parent=None):
        super().__init__(config,
                         "Output file",
                         "Where this bench writes its own copy. Readings reach "
                         "the platform through the outbox either way.", parent)
        current = self.config.get_output_config()

        box = QGroupBox("FILE")
        form = QFormLayout(box)

        self.kind = QComboBox()
        for label, value in OUTPUT_KINDS:
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(
            max(0, self.kind.findData(current.get("type", "delimited"))))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Format", self.kind)

        settings = current.get("settings", {})
        self.path = QLineEdit(settings.get("path", ""))
        self.path.setFont(mono(11))
        form.addRow("Path", self.path)

        self.delimiter = QLineEdit(settings.get("delimiter", ","))
        self.delimiter.setMaxLength(1)
        form.addRow("Delimiter", self.delimiter)

        self.header = QCheckBox("Write a header row")
        self.header.setChecked(bool(settings.get("header", True)))
        form.addRow("", self.header)

        self.body.addWidget(box)
        self.body.addStretch()
        self._kind_changed()

    def _kind_changed(self, *_):
        """Only the delimited format has a delimiter and a header to set."""
        delimited = self.kind.currentData() == "delimited"
        self.delimiter.setEnabled(delimited)
        self.header.setEnabled(delimited)
        suffix = "csv" if delimited else "jsonl"
        self.path.setPlaceholderText(f"data/runs/bench_{{timestamp}}.{suffix}")

    def apply(self):
        settings = {"path": self.path.text().strip()}
        if self.kind.currentData() == "delimited":
            settings["delimiter"] = self.delimiter.text() or ","
            settings["header"] = self.header.isChecked()
            settings["timestamp_column"] = True

        ok = self.config.set_output_config({
            "type": self.kind.currentData(),
            "enabled": True,
            "settings": settings,
        })
        self.say(ok)


# --------------------------------------------------------------------------- #
# What is under test
# --------------------------------------------------------------------------- #

class ProjectPage(SessionPage):
    """The subject of the measurement, and which devices watch it."""

    def __init__(self, config, palette, parent=None):
        super().__init__(config,
                         "Equipment",
                         "What these readings are about. Without a project a "
                         "run records locally but forwards nothing.", parent)
        from industrybench.acquisition.project import ProjectShelf
        self.shelf = ProjectShelf(config)

        picker = QHBoxLayout()
        self.chooser = QComboBox()
        self.chooser.addItems(self.shelf.names())
        self.chooser.currentTextChanged.connect(self._show)
        new = QPushButton("New…")
        new.setProperty("quiet", True)
        new.clicked.connect(self._new)
        delete = QPushButton("Delete")
        delete.setProperty("stop", True)
        delete.clicked.connect(self._delete)
        picker.addWidget(QLabel("Project"))
        picker.addWidget(self.chooser, 1)
        picker.addWidget(new)
        picker.addWidget(delete)
        self.body.addLayout(picker)

        self.body.addWidget(_caption("EQUIPMENT UNDER TEST"))
        self.equipment = QLineEdit()
        self.equipment.setPlaceholderText("what is being measured")
        self.body.addWidget(self.equipment)

        self.body.addWidget(_caption("NOTES"))
        self.notes = QLineEdit()
        self.body.addWidget(self.notes)

        taking_part = QGroupBox("DEVICES IN THIS PROJECT")
        box = QVBoxLayout(taking_part)
        self.devices = QListWidget()
        self.devices.setMaximumHeight(180)
        box.addWidget(self.devices)
        self.body.addWidget(taking_part)

        self.body.addWidget(_caption("COLUMNS THIS PROJECT PRODUCES"))
        self.columns = QLabel("—")
        self.columns.setFont(mono(11))
        self.columns.setWordWrap(True)
        self.columns.setObjectName("Subtle")
        self.body.addWidget(self.columns)
        self.body.addStretch()

        self.devices.itemChanged.connect(lambda *_: self._recount_columns())
        if self.chooser.count():
            self._show(self.chooser.currentText())
        else:
            self._list_devices([])

    # -- contents ----------------------------------------------------------
    def reread(self):
        """Re-sync the device list, keeping edits that have not been applied."""
        chosen = self.chooser.currentText()
        if chosen:
            self._show(chosen, keep_edits=True)
        else:
            self._list_devices([])

    def _list_devices(self, ticked):
        self.devices.blockSignals(True)
        self.devices.clear()
        for device in self.config.devices.get("devices", []):
            name = device.get("name", "")
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in ticked
                               else Qt.CheckState.Unchecked)
            self.devices.addItem(item)
        self.devices.blockSignals(False)

    def _ticked(self):
        return [self.devices.item(i).text()
                for i in range(self.devices.count())
                if self.devices.item(i).checkState() == Qt.CheckState.Checked]

    def _recount_columns(self):
        ticked = set(self._ticked())
        columns = []
        for device in self.config.devices.get("devices", []):
            if device.get("name") not in ticked or not device.get("enabled", True):
                continue
            for channel in device.get("channels", []) or []:
                if channel.get("enabled", True) and channel.get("column"):
                    columns.append(channel["column"])
        self.columns.setText(", ".join(columns) if columns else "—")

    def _show(self, name, keep_edits=False):
        """Load *name* into the fields.

        With *keep_edits* the stored values are not pulled back in: the fields
        already hold something the operator typed and has not applied, and only
        the device list needs re-syncing.
        """
        if not name:
            return
        project = self.shelf.get(name)
        if project is None:
            return
        if keep_edits and self.devices.count():
            ticked = self._ticked()
        else:
            self.equipment.setText(project.equipment_name)
            self.notes.setText(project.notes)
            ticked = project.devices
        self._list_devices(ticked)
        self._recount_columns()

    # -- actions -----------------------------------------------------------
    def _new(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New project", "Call it:")
        if not (ok and name.strip()):
            return
        try:
            self.shelf.create(name.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Cannot create that", str(e))
            return
        self.chooser.addItem(name.strip())
        self.chooser.setCurrentText(name.strip())

    def _delete(self):
        name = self.chooser.currentText()
        if not name:
            return
        if QMessageBox.question(self, "Forget this project",
                                f"Delete {name!r}? Readings already sent are "
                                "unaffected.") != QMessageBox.StandardButton.Yes:
            return
        self.shelf.drop(name)
        self.chooser.removeItem(self.chooser.currentIndex())

    def apply(self):
        name = self.chooser.currentText()
        if not name:
            QMessageBox.information(self, "No project",
                                    "Create a project first — it is what the "
                                    "readings are attributed to.")
            return
        project = self.shelf.get(name)
        if project is None:
            return
        project.equipment_name = self.equipment.text().strip()
        project.notes = self.notes.text().strip()
        project.devices = self._ticked()
        saved = self.shelf.save(project)
        if saved:
            self.shelf.choose(name)
        self.say(saved)


# --------------------------------------------------------------------------- #
# The link to the platform
# --------------------------------------------------------------------------- #

class PlatformPage(SessionPage):
    """Station identity, the mTLS material, and the state of the queue.

    This bench is the gateway (ADR-0002), so everything the platform needs in
    order to trust it lives here. None of it is required to run: an
    unconfigured bench queues readings it never sends, and turning the link on
    later delivers them.
    """

    def __init__(self, config, palette, runner=None, parent=None):
        super().__init__(config,
                         "Platform",
                         "This bench is the gateway: it holds the certificate "
                         "IndustryFlow authenticates, and queues readings until "
                         "they can be delivered.", parent)
        self.pal = palette
        self.runner = runner

        from industrybench.edge.identity import load_station
        self.station = load_station(config)

        identity = QGroupBox("STATION")
        form = QFormLayout(identity)
        self.bench_name = QLineEdit(self.station.name)
        form.addRow("Bench name", self.bench_name)
        self.site = QLineEdit(self.station.site_id)
        self.site.setToolTip("Where this bench stands — sent as site_id")
        form.addRow("Site", self.site)
        station_id = QLineEdit(self.station.station_id)
        station_id.setReadOnly(True)
        station_id.setFont(mono(10))
        station_id.setToolTip("The gateway's device id. Generated once, never changes.")
        form.addRow("Station id", station_id)
        self.body.addWidget(identity)

        tls = QGroupBox("MUTUAL TLS")
        tls_form = QFormLayout(tls)
        self.url = QLineEdit(self.station.ingest_url)
        self.url.setPlaceholderText("https://ingest.example.com/api/v1/sensor-data")
        self.url.setFont(mono(10))
        tls_form.addRow("Ingest URL", self.url)
        self.cert = QLineEdit(self.station.client_cert)
        self.cert.setPlaceholderText("client.crt")
        self.cert.setFont(mono(10))
        tls_form.addRow("Client certificate", self.cert)
        self.key = QLineEdit(self.station.client_key)
        self.key.setPlaceholderText("client.key — never leaves this machine")
        self.key.setFont(mono(10))
        tls_form.addRow("Client key", self.key)
        self.ca = QLineEdit(self.station.ca_cert)
        self.ca.setPlaceholderText("ca.crt")
        self.ca.setFont(mono(10))
        tls_form.addRow("CA certificate", self.ca)
        self.forwarding = QCheckBox("Deliver queued readings to IndustryFlow")
        self.forwarding.setChecked(self.station.forward_enabled)
        tls_form.addRow("", self.forwarding)
        self.body.addWidget(tls)

        queue = QGroupBox("OUTBOX")
        queue_form = QFormLayout(queue)
        self.depth = QLabel("—")
        self.depth.setFont(mono(11))
        queue_form.addRow("Queued / total", self.depth)
        self.link = QLabel("—")
        self.link.setWordWrap(True)
        queue_form.addRow("Link", self.link)
        self.body.addWidget(queue)
        self.body.addStretch()

        self.reread()

    # -- status ------------------------------------------------------------
    def reread(self):
        """Re-read the queue and say plainly what is still missing, if anything."""
        missing = self.station.missing_for_forwarding()
        if not self.station.forward_enabled:
            self._link("Delivery is off — readings queue here and stay here.",
                       self.pal["faint"])
        elif missing:
            self._link("Not ready: " + ", ".join(missing), self.pal["caution"])
        else:
            self._link(f"Ready — delivering to {self.station.ingest_url}",
                       self.pal["live"])

        outbox = getattr(self.runner, "outbox", None)
        if outbox is None:
            self.depth.setText("—")
            return

        state = outbox.stats()
        text = f"{state['pending']:,} pending / {state['total']:,} total"
        if state.get("suspect"):
            text += f"   ({state['suspect']:,} suspect)"
        self.depth.setText(text)
        if state.get("last_error"):
            self._link(f"The last attempt failed: {state['last_error']}",
                       self.pal["alarm"])

    def _link(self, text: str, colour: str):
        self.link.setText(text)
        self.link.setStyleSheet(f"color:{colour};")

    # -- persist -----------------------------------------------------------
    def apply(self):
        from industrybench.edge.identity import save_station
        self.station.name = self.bench_name.text().strip() or self.station.name
        self.station.site_id = self.site.text().strip() or "unassigned"
        self.station.ingest_url = self.url.text().strip()
        self.station.client_cert = self.cert.text().strip()
        self.station.client_key = self.key.text().strip()
        self.station.ca_cert = self.ca.text().strip()
        self.station.forward_enabled = self.forwarding.isChecked()

        self.say(save_station(self.config, self.station))

        # Applied to the running forwarder straight away, so the result shows
        # here rather than after a restart.
        outbox = getattr(self.runner, "outbox", None)
        if outbox is not None:
            if self.runner is not None:
                self.runner.station = self.station
            if self.station.can_forward():
                outbox.start_forwarding(self.station)
            else:
                outbox.stop_forwarding()
        self.reread()
