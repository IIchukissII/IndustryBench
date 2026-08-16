# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the log goes.

Two destinations with different jobs: a rotating UTF-8 file that keeps
everything, and the terminal, which shows only what somebody needs to act on
now. A bench runs unattended for hours, so the file is the record and the
terminal is the alarm.

The terminal is the awkward half. On Windows it is often still a legacy
codepage, and one "°C" or "Ω" in a log line raises UnicodeEncodeError from
inside the logging machinery itself — which logging then reports as a handler
error, so a single degree sign becomes noise on every subsequent message. The
fix belongs at the stream, once: make it replace what it cannot encode, rather
than sanitising every string that might one day reach a log line. The file
keeps the characters intact.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

from industrybench import paths

#: third-party loggers that are chatty at INFO and say nothing we need
NOISY = ("pyvisa", "pyvisa_py", "pyqtgraph", "matplotlib", "PIL")

MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5


def _console_handler(level: int) -> logging.StreamHandler:
    """A stderr handler that cannot be killed by a character it can't encode."""
    stream = sys.stderr
    try:
        # Python 3.7+: make the stream itself lossy rather than raising.
        stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        # A stream that cannot be reconfigured (pytest capture, a pipe wrapper)
        # is left alone; the file handler still has the full-fidelity record.
        pass

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    return handler


def _file_handler(log_dir: Path, level: int) -> logging.Handler | None:
    """A rotating UTF-8 file handler, or None if the path is not writable."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"industrybench_{datetime.now():%Y%m%d}.log"
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"))
        return handler
    except OSError as e:
        print(f"Warning: file logging disabled ({e})", file=sys.stderr)
        return None


def start_logging(level: int = logging.INFO,
                  console_level: int = logging.WARNING,
                  log_dir: str | Path | None = None) -> Path | None:
    """Configure root logging. Safe to call more than once.

    Returns the path of the log file, or None if only the terminal is active.
    """
    log_dir = Path(log_dir) if log_dir is not None else paths.log_dir()
    root = logging.getLogger()
    for handler in root.handlers[:]:      # replace, never stack duplicates
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)

    file_handler = _file_handler(Path(log_dir), level)
    if file_handler is not None:
        root.addHandler(file_handler)
    root.addHandler(_console_handler(console_level))

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    path = getattr(file_handler, "baseFilename", None)
    logging.getLogger(__name__).info("Logging to %s", path or "console only")
    return Path(path) if path else None
