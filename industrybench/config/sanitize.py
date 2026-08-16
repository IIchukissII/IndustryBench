#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Config repair: strip characters that genuinely break things.

The rule is deliberately narrow: remove C0/C1 control characters and nothing
else. Stripping all non-ASCII is the tempting reading of "repair", and it
destroys the data instead — ``°C`` becomes ``C``, ``µV`` becomes ``V``, ``Ω``
vanishes, every umlaut in an instrument name is replaced. Those are the units
and names this application exists to record. Console encoding, the fault that
invites the mistake, is handled at the stream in
``industrybench.logging_setup``.

A
control character in a config value is never intentional, and it does break log
lines, terminals and parsers — ``json.loads`` rejects raw ones outright, which
is why files carrying them have to be read with ``strict=False``. Everything a
bench actually uses is preserved byte for byte.

Encoding is *not* this module's job. Files are decoded through the same path the
store uses, so a config written by a pre-UTF-8 build is understood rather than
mangled; the store rewrites it as UTF-8 on its next save.

Run it directly to repair the files in place:

    python -m industrybench.config.sanitize [config-dir]
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from industrybench import paths
from industrybench.config.defaults import CONFIG_FILES
from industrybench.config.json_store import decode_config, write_atomically

logger = logging.getLogger(__name__)

# C0 (except tab/newline/carriage return) and C1. Nothing legible lives here.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_control(text: str) -> str:
    """Remove control characters from a string, leaving all printable text."""
    return _CONTROL.sub("", text) if isinstance(text, str) else text


def clean(value: Any) -> Any:
    """Recursively clean a decoded-JSON structure. Keys are cleaned too."""
    if isinstance(value, dict):
        return {strip_control(k): clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return strip_control(value)


def _read(path: Path) -> Optional[Any]:
    """Parse a config file, tolerating the control characters we are here for."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.error("Cannot read %s: %s", path, e)
        return None

    text = decode_config(raw)
    if text is None:
        logger.error("Cannot decode %s in UTF-8 or the local codepage", path)
        return None

    try:
        # strict=False: the standard parser refuses raw control characters, so
        # without it this tool cannot read the very files it exists to repair.
        return json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        logger.error("Cannot parse %s: %s", path, e)
        return None


def repair_file(path: str | Path) -> bool:
    """
    Repair one file in place. True only if it needed repairing.

    A clean file is left byte for byte alone — no rewrite, no backup —
    otherwise every launch would rotate another timestamped copy into
    ``config/``.
    """
    path = Path(path)
    if not path.exists():
        return False

    original = _read(path)
    if original is None:
        return False

    repaired = clean(original)
    if repaired == original:
        return False

    # Copy rather than rename: the original stays in place until the repaired
    # version has been written over it atomically. Renaming first would leave
    # no config file at all if the write then failed.
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    # Appended, not substituted: `.with_suffix()` would drop the `.json` and
    # leave a salvaged file that no longer looks like config.
    backup = path.with_name(f"{path.name}.backup_{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError as e:
        logger.error("Cannot back up %s: %s", path, e)
        return False

    if not write_atomically(path, repaired, logger):
        return False

    logger.warning("Repaired %s — original kept as %s", path, backup.name)
    return True


def repair_config(config_dir: str | Path | None = None) -> bool:
    """Repair every config file that needs it. True if any were changed."""
    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    changed = sum(repair_file(config_dir / name) for name in CONFIG_FILES)
    if changed:
        logger.warning("Repaired %d configuration file(s).", changed)
    return bool(changed)


def main() -> int:
    # Run as a tool, so say what happened on the console. Imported by the app,
    # the module logs instead — startup output may have nowhere to go.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.config_dir()
    if not directory.is_dir():
        print(f"No such directory: {directory}", file=sys.stderr)
        return 1
    if not repair_config(directory):
        print("Nothing to repair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
