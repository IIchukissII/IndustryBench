# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The inspector — settings for whatever is selected, and nothing else.

Edits take effect when a field loses focus. There is no Apply button on this
surface, deliberately: an editing model you cannot see is worse than a slightly
eager one, and the honest complaint about a screen with three different commit
rules is that nobody can tell what has been saved. One rule is easy to trust.

A channel's identity is shown and never editable. It is the platform's handle
on that channel's entire history, so it is here to be read and copied, not
changed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from industrybench.ui.theme import PANE_INSET, mono

logger = logging.getLogger(__name__)

#: what the console offers, and the ``kind`` each choice writes
DEVICE_KINDS = [
    ("SCPI instrument (VISA)", "scpi_visa"),
    ("1-Wire probe", "onewire_probe"),
    ("Simulated probe", "sim_probe"),
    ("Simulated voltmeter", "sim_voltmeter"),
    ("Simulated ammeter", "sim_ammeter"),
]

#: the channel types offered, with the query and unit each implies.
#: A channel may hold anything — this is the shortcut, not the constraint.
CHANNEL_PRESETS = [
    ("DC Voltage", "MEAS:VOLT:DC?", "V"),
    ("DC Current", "MEAS:CURR:DC?", "A"),
    ("AC Voltage", "MEAS:VOLT:AC?", "V"),
    ("AC Current", "MEAS:CURR:AC?", "A"),
    ("Resistance", "MEAS:RES?", "Ω"),
    ("Temperature", "MEAS:VOLT:DC?", "°C"),
    ("Anything else", "READ?", ""),
]
CHANNEL_KINDS = [name for name, _, _ in CHANNEL_PRESETS]
QUERY_FOR = {name: query for name, query, _ in CHANNEL_PRESETS}
UNIT_FOR = {name: unit for name, _, unit in CHANNEL_PRESETS}

#: kinds that talk over VISA and therefore need an address
NEEDS_ADDRESS = {"scpi_visa"}


class Inspector(QWidget):
    """Settings for the current selection, and nothing else."""

    edited = pyqtSignal()                       # the model changed; persist it
    check_requested = pyqtSignal(object)        # device dict
    remove_requested = pyqtSignal(object, object)   # device, channel or None
    add_channel_requested = pyqtSignal(object)      # device

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.pal = palette
        self.setObjectName("SidePane")
        self._selection = None
        self._link_report = ""

        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(PANE_INSET, 14, PANE_INSET, 14)
        self._body.setSpacing(10)
        self.show_selection(None)

    # ------------------------------------------------------------------ build
    def show_selection(self, selection):
        self._selection = selection
        self._empty()
        if selection is None:
            self._note("Select a device or a channel to edit it.")
            return
        kind, device, channel = selection
        if kind == "device":
            self._device_form(device)
        else:
            self._channel_form(device, channel)

    def _empty(self):
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                self._empty_layout(item.layout())

    def _empty_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._empty_layout(item.layout())

    def _note(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("Subtle")
        self._body.addWidget(label)
        self._body.addStretch()

    def _heading(self, title, sub):
        head = QLabel(title)
        head.setObjectName("PaneHead")
        head.setWordWrap(True)
        self._body.addWidget(head)
        note = QLabel(sub)
        note.setObjectName("Subtle")
        self._body.addWidget(note)

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("Caption")
        self._body.addWidget(label)

    # ----------------------------------------------------------------- device
    def _device_form(self, device: Dict[str, Any]):
        self._heading(device.get("name", "Device"), "device")

        self._caption("NAME")
        name = QLineEdit(device.get("name", ""))
        name.editingFinished.connect(
            lambda: self._put(device, "name",
                              name.text().strip() or device.get("name", "")))
        self._body.addWidget(name)

        self._caption("KIND")
        kinds = QComboBox()
        for label, value in DEVICE_KINDS:
            kinds.addItem(label, value)
        kinds.setCurrentIndex(max(0, kinds.findData(device.get("kind", "scpi_visa"))))
        kinds.currentIndexChanged.connect(
            lambda: (self._put(device, "kind", kinds.currentData()),
                     self.show_selection(self._selection)))
        self._body.addWidget(kinds)

        if device.get("kind", "scpi_visa") in NEEDS_ADDRESS:
            self._visa_fields(device)

        row = QHBoxLayout()
        add = QPushButton("+ Channel")
        add.setProperty("quiet", True)
        add.clicked.connect(lambda: self.add_channel_requested.emit(device))
        remove = QPushButton("Remove")
        remove.setProperty("stop", True)
        remove.clicked.connect(lambda: self.remove_requested.emit(device, None))
        row.addWidget(add)
        row.addWidget(remove)
        self._body.addLayout(row)
        self._body.addStretch()

    def _visa_fields(self, device: Dict[str, Any]):
        self._caption("ADDRESS")
        address = QLineEdit(device.get("address", ""))
        address.setFont(mono(10))
        address.setPlaceholderText("USB0::0xVVVV::0xPPPP::SERIAL::INSTR")
        address.editingFinished.connect(
            lambda: self._put(device, "address", address.text().strip()))
        self._body.addWidget(address)

        self._caption("HOW LONG TO WAIT (ms)")
        timeout = QSpinBox()
        timeout.setRange(1000, 60000)
        timeout.setSingleStep(500)
        timeout.setValue(int(device.get("timeout_ms", 5000)))
        timeout.valueChanged.connect(lambda v: self._put(device, "timeout_ms", v))
        self._body.addWidget(timeout)

        check = QPushButton("Check the link")
        check.clicked.connect(lambda: self.check_requested.emit(device))
        self._body.addWidget(check)

        if self._link_report:
            note = QLabel(self._link_report)
            note.setWordWrap(True)
            note.setFont(mono(9))
            self._body.addWidget(note)

    # ---------------------------------------------------------------- channel
    def _channel_form(self, device: Dict[str, Any], channel: Dict[str, Any]):
        self._heading(channel.get("column") or "(unnamed)",
                      f"channel on {device.get('name', '')}")

        self._caption("MEASURES")
        measures = QComboBox()
        measures.addItems(CHANNEL_KINDS)
        current = channel.get("measures", "Anything else")
        if current not in CHANNEL_KINDS:
            measures.addItem(current)
        measures.setCurrentText(current)
        measures.currentTextChanged.connect(
            lambda text: self._change_measures(channel, text))
        self._body.addWidget(measures)

        self._caption("COLUMN")
        column = QLineEdit(channel.get("column", ""))
        column.setFont(mono(10))
        column.setToolTip("The output column header. Safe to rename — the "
                          "channel keeps its identity.")
        column.editingFinished.connect(
            lambda: self._put(channel, "column", column.text().strip()))
        self._body.addWidget(column)

        self._caption("UNIT")
        unit = QLineEdit(channel.get("unit", ""))
        unit.setFont(mono(10))
        unit.editingFinished.connect(
            lambda: self._put(channel, "unit", unit.text().strip()))
        self._body.addWidget(unit)

        self._caption("RANGE")
        span = QLineEdit(str(channel.get("range", "AUTO")))
        span.setFont(mono(10))
        span.setToolTip("AUTO, or a number to pin the range")
        span.editingFinished.connect(
            lambda: self._put(channel, "range", span.text().strip() or "AUTO"))
        self._body.addWidget(span)

        if channel.get("sensor_id"):
            self._caption("IDENTITY")
            sensor_id = QLineEdit(str(channel["sensor_id"]))
            sensor_id.setReadOnly(True)
            sensor_id.setFont(mono(9))
            sensor_id.setToolTip("This channel's identity on the platform.\n"
                                 "Fixed for the life of the channel.")
            self._body.addWidget(sensor_id)

        remove = QPushButton("Remove channel")
        remove.setProperty("stop", True)
        remove.clicked.connect(lambda: self.remove_requested.emit(device, channel))
        self._body.addWidget(remove)
        self._body.addStretch()

    # ------------------------------------------------------------------ edits
    def _put(self, target: Dict[str, Any], key: str, value):
        if target.get(key) == value:
            return
        target[key] = value
        self.edited.emit()

    def _change_measures(self, channel: Dict[str, Any], measures: str):
        """Follow a channel type change through to its query and its unit.

        The unit follows only while it is still the previous type's default.
        Somebody who typed ``mV`` meant it, and having that quietly reset to
        ``V`` because they corrected the channel type would be worse than
        leaving a unit that needs one more edit.
        """
        was_default = UNIT_FOR.get(channel.get("measures", ""))
        channel["measures"] = measures

        query = QUERY_FOR.get(measures)
        if query:
            channel["query"] = query
            if not channel.get("unit") or channel.get("unit") == was_default:
                channel["unit"] = UNIT_FOR.get(measures, "")

        self.edited.emit()
        self.show_selection(self._selection)

    def report(self, text: str):
        """Show the result of a link check under the button that asked for it."""
        self._link_report = text
        if self._selection and self._selection[0] == "device":
            self.show_selection(self._selection)

    def restyle(self, palette: dict):
        self.pal = palette
