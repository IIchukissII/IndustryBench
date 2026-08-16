# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The channel tree — navigation, live values and plot visibility in one list.

This is the spine of the console. It answers the three questions an operator
asks continuously — what is on the bench, what is it reading, what am I looking
at — without going anywhere to ask them. Selecting a row drives the inspector.

The tick box controls whether a channel is *plotted*, never whether it is
measured. Those are different questions, and conflating them would mean
hiding a trace quietly stopped recording the channel.

Deliberately not an editing surface: edits live in the inspector, so a row can
never be half-edited while its value keeps updating underneath the cursor.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from industrybench.ui.theme import PANE_INSET, mono

logger = logging.getLogger(__name__)

ROLE = Qt.ItemDataRole.UserRole
C_NAME, C_VALUE = 0, 1


class ChannelTree(QWidget):
    """Devices and their channels, with live values."""

    #: ("device"|"channel", device dict, channel dict or None)
    selected = pyqtSignal(object)
    #: a channel's plot visibility was toggled
    visibility_changed = pyqtSignal(str, bool)
    add_device_requested = pyqtSignal()

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.pal = palette
        self._items: Dict[str, QTreeWidgetItem] = {}      # column -> row
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        cap = QLabel("CHANNELS")
        cap.setObjectName("Caption")
        cap.setContentsMargins(PANE_INSET, 14, PANE_INSET, 8)
        root.addWidget(cap)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # A fixed split, not size-to-contents: a reading like -4.958e-07 A is
        # far wider than a typical one, and letting the column claim that width
        # truncates every channel name to pay for it. The name gets the larger
        # share and long readings elide instead.
        self.tree.setColumnWidth(C_NAME, 150)
        self.tree.itemSelectionChanged.connect(self.announce_selection)
        self.tree.itemChanged.connect(self._ticked)
        root.addWidget(self.tree, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(PANE_INSET, 8, PANE_INSET, 10)
        add = QPushButton("+ Device")
        add.setProperty("quiet", True)
        add.clicked.connect(self.add_device_requested.emit)
        bar.addWidget(add)
        root.addLayout(bar)

    # ------------------------------------------------------------------ build
    def rebuild(self, devices):
        """Rebuild from the device list, keeping the selection where it still exists."""
        keep = self.selection_key()
        self._loading = True
        self.tree.clear()
        self._items.clear()

        for device in devices:
            parent = QTreeWidgetItem(self.tree)
            parent.setData(0, ROLE, ("device", device, None))
            font = parent.font(C_NAME)
            font.setBold(True)
            parent.setFont(C_NAME, font)
            parent.setText(C_NAME, device.get("name", "Unnamed"))
            parent.setForeground(C_NAME, QColor(self.pal["text"]))
            parent.setExpanded(True)

            for channel in device.get("channels", []) or []:
                column = channel.get("column") or "(unnamed)"
                child = QTreeWidgetItem(parent)
                child.setData(0, ROLE, ("channel", device, channel))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(C_NAME, Qt.CheckState.Checked)
                child.setText(C_NAME, column)
                child.setToolTip(C_NAME, "Tick to plot this channel. It is "
                                         "measured either way.")
                child.setText(C_VALUE, "—")
                child.setFont(C_VALUE, mono(10))
                child.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
                child.setForeground(C_VALUE, QColor(self.pal["faint"]))
                self._items[column] = child

        self._loading = False
        if keep:
            self.select_key(keep)
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    # ------------------------------------------------------------------- live
    def show_values(self, values: Dict[str, tuple]):
        """column -> (text, trustworthy). Touches only the value column."""
        self._loading = True
        for column, (text, ok) in values.items():
            item = self._items.get(column)
            if item is None:
                continue
            item.setText(C_VALUE, text)
            item.setForeground(C_VALUE, QColor(
                self.pal["text"] if ok else self.pal.get("suspect", self.pal["caution"])))
        self._loading = False

    def show_nothing(self):
        self._loading = True
        for item in self._items.values():
            item.setText(C_VALUE, "—")
            item.setForeground(C_VALUE, QColor(self.pal["faint"]))
        self._loading = False

    # -------------------------------------------------------------- selection
    def selection_key(self) -> Optional[tuple]:
        item = self.tree.currentItem()
        if item is None:
            return None
        kind, device, channel = item.data(0, ROLE)
        return (kind, device.get("name"), (channel or {}).get("column"))

    def select_key(self, key) -> None:
        kind, device_name, column = key
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, ROLE)[1].get("name") != device_name:
                continue
            if kind == "device":
                self.tree.setCurrentItem(top)
                return
            for c in range(top.childCount()):
                child = top.child(c)
                if (child.data(0, ROLE)[2] or {}).get("column") == column:
                    self.tree.setCurrentItem(child)
                    return
            self.tree.setCurrentItem(top)
            return

    def announce_selection(self):
        item = self.tree.currentItem()
        self.selected.emit(item.data(0, ROLE) if item is not None else None)

    def _ticked(self, item, column):
        if self._loading or column != C_NAME:
            return
        payload = item.data(0, ROLE)
        if not payload or payload[0] != "channel":
            return
        name = (payload[2] or {}).get("column") or ""
        self.visibility_changed.emit(
            name, item.checkState(C_NAME) == Qt.CheckState.Checked)

    def restyle(self, palette: dict):
        self.pal = palette
