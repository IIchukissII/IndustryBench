#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Starting the bench.

    python -m industrybench

Four things have to happen, in order, before a window can exist: the workspace
directories, the seed configuration, logging, and a repair pass over any
configuration file carrying characters a parser will refuse. Only then is there
anything for the console to read.

Nothing of substance lives here. The console is ``industrybench.ui.window`` and
the seeds are ``industrybench.config.defaults``; this module wires them
together and makes sure a failure during startup is *seen*. Before the window
exists there is nowhere on screen for a traceback to go, so anything that
escapes goes into a dialog as well as onto the terminal.
"""

import logging
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from industrybench.config.defaults import ensure_directories, seed_config_files
from industrybench.logging_setup import start_logging
from industrybench.ui.branding import APP_NAME, VERSION
from industrybench.ui.theme import load_bundled_fonts
from industrybench.ui.window import Console

#: optional backends, and what a bench loses without each. None is fatal — the
#: simulated devices need nothing installed, which is what a fresh clone runs.
OPTIONAL = (
    ("pyvisa", "SCPI instruments over VISA"),
    ("w1thermsensor", "1-Wire temperature probes"),
)


def _note_absent_backends() -> None:
    for module, what in OPTIONAL:
        try:
            __import__(module)
        except Exception:
            print(f"Note: {module} is not installed — {what} will not appear.")


def _prepare() -> None:
    """Everything that has to exist before the console is built."""
    ensure_directories()
    seeded = seed_config_files()
    start_logging()

    # Before any widget is made, so the first thing painted is already in the
    # right face. Registering after the sheet is applied leaves the window in
    # the fallback until something forces a re-layout.
    load_bundled_fonts()

    _note_absent_backends()
    if seeded:
        print(f"Wrote a starting configuration: {', '.join(seeded)}")

    # A control character in a configuration value makes json refuse the whole
    # file, so the repair pass runs before anything tries to read one. Its own
    # failure is not fatal: the store has its own recovery, and refusing to
    # start over a repair would be worse than the fault being repaired.
    try:
        from industrybench.config.sanitize import repair_config
        repair_config()
    except Exception as e:
        print(f"Note: the configuration could not be checked over: {e}")


def _show_failure(problem: Exception) -> None:
    """Put a startup failure somewhere it will actually be seen."""
    logging.getLogger(__name__).exception("The bench could not start")
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(f"{APP_NAME} did not start")
    box.setText("The bench could not start.")
    box.setInformativeText("The log under data/logs holds the full traceback.")
    box.setDetailedText(str(problem))
    box.exec()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_NAME)

    try:
        _prepare()
        console = Console()
        console.show()
        sys.exit(app.exec())

    except UnicodeEncodeError as e:
        # Almost always a device name or a unit the terminal's codepage cannot
        # encode. It gets its own message because there is a tool that fixes it.
        print(f"A character could not be encoded: {e}")
        print("That is usually a device name or a unit. Repair the "
              "configuration with:  python -m industrybench.config.sanitize")
        sys.exit(1)

    except Exception as e:
        _show_failure(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
