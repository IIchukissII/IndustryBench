# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The session drawer — what holds for a whole run rather than for one channel.

What is under test, where the file goes, and whether readings leave the bench.
These are set once at the start of a session and then left alone, so they do
not belong on the main surface competing for space with the traces. They keep
their own Apply for the reason given in ``ui.session.pages``: a project switch
changes what every later reading is attributed to.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QVBoxLayout

from industrybench.ui.session.pages import OutputPage, PlatformPage, ProjectPage

logger = logging.getLogger(__name__)


class SessionDialog(QDialog):
    """Equipment, output and platform, opened from the run bar."""

    def __init__(self, config, palette, runner=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session")
        self.setMinimumSize(720, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pages = [
            ("Equipment", ProjectPage(config, palette)),
            ("Output file", OutputPage(config, palette)),
            ("Platform", PlatformPage(config, palette, runner)),
        ]
        tabs = QTabWidget()
        for label, page in self.pages:
            tabs.addTab(page, label)
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def reread(self):
        """Re-read every page. One that raises must not close the drawer."""
        for label, page in self.pages:
            try:
                page.reread()
            except Exception:
                logger.exception("The %s page could not refresh", label)
