# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Application mark.

The same SVG the README shows, rendered into the window chrome so the app and
the repo carry one identity. There are two files — a light-ground and a
dark-ground variant — and the palette in use decides which one is correct;
rendering the dark mark on a light title bar is what makes an app look assembled
from parts.

Rendered at the device pixel ratio rather than scaled up from a fixed bitmap, so
the mark stays sharp on a HiDPI display.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

logger = logging.getLogger(__name__)

APP_NAME = "IndustryBench"
VERSION = "0.1.0"
#: Currently unreferenced — kept as the one place the strapline is written down,
#: so the window, the README and anything added later cannot drift apart.
TAGLINE = "Set up the bench, measure, keep the readings"

# industrybench/ui/branding.py -> repo root is three levels up
_IMG_DIR = Path(__file__).resolve().parents[2] / "img"
_CACHE: dict[tuple, QPixmap] = {}


def logo_path(theme: str = "dark", *, compact: bool = False) -> Path:
    """The artwork that belongs on this theme's ground.

    Two files, deliberately: the full logo carries a graticule and channel
    leads, which turn to mush below about 64px. `compact` selects the mark —
    the same idea reduced to the world circle, the settling trace and the
    reading — drawn at weights that survive a 24px title bar.

    Neither file carries a background. The artwork differs between variants
    only in the structural greys; the gold is the same in both, because it is
    the accent rather than a contrast device.
    """
    variant = "dark" if theme == "dark" else "light"
    stem = "industrybench-mark" if compact else "industrybench-logo"
    return _IMG_DIR / f"{stem}-{variant}.svg"


def logo_pixmap(theme: str = "dark", size: int = 26, dpr: float = 1.0,
                *, compact: bool | None = None) -> QPixmap:
    """Render the mark at *size* logical pixels. Returns a null pixmap if the
    file or the SVG module is unavailable — chrome should degrade to text, never
    prevent the window from opening."""
    if compact is None:
        compact = size < 64          # chrome gets the mark, dialogs the logo
    key = (theme, size, round(dpr, 2), compact)
    if key in _CACHE:
        return _CACHE[key]

    pixmap = QPixmap()
    try:
        from PyQt6.QtSvg import QSvgRenderer

        path = logo_path(theme, compact=compact)
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise ValueError(f"unreadable SVG: {path}")

        px = max(1, int(round(size * dpr)))
        image = QImage(QSize(px, px), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()

        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
    except Exception as e:
        logger.warning("Could not render the application mark: %s", e)

    _CACHE[key] = pixmap
    return pixmap
