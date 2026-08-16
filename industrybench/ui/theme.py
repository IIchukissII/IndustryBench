# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Design tokens, rendered as a Qt style sheet.

Two palettes — light and dark — held as dicts, and one template filled from a
palette with ``str.format``. Qt style sheets have no equivalent of CSS custom
properties, so substitution at build time is how a token reaches a rule. Paint
the whole application with ``paint(app, "dark")``.

Tokens are named for the role they play, and where IndustryFlow has a name for
that role this uses the same word — ``rail``/``card``/``raised`` for its three
panel depths, ``gold`` for the one accent, ``live``/``caution``/``alarm`` for
the states. A bench and the platform it feeds should be describable in one
vocabulary, and a token called ``surface2`` describes nothing.

The language:

  - a graphite ground carrying exactly one accent, a warm gold
  - azure demoted to structure: focus rings and selection, never emphasis
  - state colours borrowed from the alchemical stages — live teal, citrinitas
    amber, rubedo cinnabar — and never reused as the accent
  - monospace for anything the machine produced, the sans for everything a
    person wrote

The dark palette is IndustryFlow's token set unchanged. IndustryFlow is
dark-only, so the light palette is a designed counterpart in the same language
— a warm ground under the same gold — rather than an inversion, which turns a
considered dark theme into a washed-out light one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #

#: IndustryFlow's tokens, unchanged in value.
DARK = {
    "ground":     "#090c16",   # the base, darkest layer
    "rail":       "#11151f",   # rails and the title bar, recessed
    "card":       "#161b27",   # cards, raised off the ground
    "raised":     "#1c2330",   # hover, and the bed behind a segmented control
    "well":       "#0d111b",   # inputs sit *below* the card, not level with it

    "text":       "#f2efe9",   # warm luminous white, not LED-cold
    "muted":      "#9aa0ac",
    "faint":      "#6a7382",

    "rule":       "#232c3b",
    "rule_soft":  "#1a2130",   # kept for parity with IndustryFlow's --line-soft

    "gold":       "#cfa94e",   # the one accent
    "gold_glow":  "#e6c67c",
    "gold_soft":  "rgba(207, 169, 78, 0.13)",
    "on_gold":    "#090c16",   # the ground, read back on gold

    # Brightened from IndustryFlow's --signal #4a90d9: focus has to announce
    # itself against the graphite ground. 29.6 dE from the gold it sits beside,
    # and better separated from `suspect` violet than the original was.
    "focus":      "#3d9bff",
    "focus_soft": "rgba(61, 155, 255, 0.18)",

    "live":       "#2dd4bf",
    "caution":    "#f2a63d",   # citrinitas
    "alarm":      "#e0472f",   # rubedo
    "suspect":    "#a78bd0",   # read, but not to be trusted. Violet by
                               # monitoring convention (UNKNOWN) — deliberately
                               # neither the alarm red nor a faint grey.
    "curve":      "#cfa94e",   # the measurement is the accent
}

#: The designed light counterpart: the same gold and the same stages over a
#: warm ground. The golds are darkened so they still carry text contrast.
LIGHT = {
    "ground":     "#E8E4D9",
    "rail":       "#F4F1E9",
    "card":       "#FBF9F5",
    "raised":     "#E8E4DA",
    "well":       "#FFFFFF",

    "text":       "#14181F",
    "muted":      "#565C67",
    "faint":      "#828994",

    "rule":       "#DAD5C9",
    "rule_soft":  "#E7E3D9",

    "gold":       "#9A7423",
    "gold_glow":  "#7E5D18",
    "gold_soft":  "rgba(154, 116, 35, 0.14)",
    "on_gold":    "#FFFFFF",

    "focus":      "#1f6ac4",
    "focus_soft": "rgba(31, 106, 196, 0.16)",

    "live":       "#0E9C8B",
    # caution/alarm re-stepped: the previous pair scored dE 12.8 for normal
    # vision, under the floor of 15. They appear together on device state and
    # on the platform link, so they have to be tellable apart. Now 21.4 normal,
    # 13.2 deuteranopic.
    "caution":    "#A88600",
    "alarm":      "#B3231A",
    "suspect":    "#6E52A8",
    "curve":      "#9A7423",
}

THEMES = {"light": LIGHT, "dark": DARK}


#: Horizontal breathing room between a pane's frame and what it holds.
#:
#: One number, because the eye reads the three panes as columns of a single
#: surface and reads a few pixels of disagreement between them as a fault. A
#: pane without it puts its content flush against the frame while the pane
#: beside it has room. Layouts use this directly; a tree uses TREE_INSET, for
#: the reason given there.
PANE_INSET = 14

#: The same room, measured at a tree's viewport rather than at its rows. Four
#: pixels under PANE_INSET because a row carries that much padding of its own,
#: so the text still lands on the pane's inset.
TREE_INSET = PANE_INSET - 4

#: The type ramp, in pixels. Six steps and no more: a dense instrument screen
#: gets its hierarchy from weight, colour and tracking, and every extra size is
#: one more thing that can disagree with the pane beside it.
#:
#: Whole pixels only. A half-pixel size renders differently depending on where
#: the text lands, which is how a column of captions ends up looking ragged.
TYPE = {
    "caption": 11,     # eyebrows and field captions, tracked wide
    "small": 12,       # notes, secondary lines
    "body": 14,        # the base
    "lead": 16,        # the word mark
    "head": 20,        # pane titles
    "readout": 28,     # a live value, when it is the point of the pane
}

#: monospace faces to try, best first.
#:
#: Cascadia Mono ships with Windows Terminal and modern Windows and is the best
#: of the built-ins here — it has a slashed zero and properly tabular digits.
#: Cascadia *Code* is the same face with programming ligatures, which are wrong
#: for a readout, so the Mono cut is named explicitly and first.
MONO_FACES = ("IBM Plex Mono", "JetBrains Mono", "Cascadia Mono", "Consolas",
              "SF Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New")

#: the proportional stack, as a QSS font-family list.
#: "Segoe UI Variable Text" is Windows 11 and later; "Segoe UI" is the fallback
#: that actually renders on Windows 10, and it is not a placeholder — the whole
#: stack is designed to look right on it.
SANS_STACK = ('"IBM Plex Sans", "Segoe UI Variable Text", "Segoe UI", '
              "system-ui, sans-serif")

#: Where the bundled faces live. Repo root, beside ``img/``.
FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"


def load_bundled_fonts() -> list[str]:
    """Register the bundled IBM Plex faces with Qt. Returns the families added.

    The type stack names IBM Plex first, and a stack whose first choice is
    absent is not a design — it is a lottery run on whatever each machine
    happens to have installed. Windows 10 has neither Plex nor Segoe UI
    Variable, so an unbundled build silently rendered in Segoe UI and Consolas
    and looked like a different application.

    Only the weights the sheet actually asks for are shipped: Regular,
    SemiBold and Bold for the sans, Regular for the mono. No italics — nothing
    here sets one, and Qt will synthesise if something ever does.

    Must be called after ``QApplication`` exists and before the sheet is
    applied. Missing or unreadable files are logged and skipped: the stack
    falls through to the system faces, which is a worse-looking window rather
    than no window.
    """
    loaded: list[str] = []
    if not FONT_DIR.is_dir():
        logger.warning("No bundled fonts at %s — falling back to system faces",
                       FONT_DIR)
        return loaded

    for path in sorted(FONT_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger.warning("Qt refused the font file %s", path.name)
            continue
        loaded.extend(QFontDatabase.applicationFontFamilies(font_id))

    families = sorted(set(loaded))
    if families:
        logger.info("Bundled type loaded: %s", ", ".join(families))
    return families


#: OpenType features every number on this screen should be set with.
#:
#: ``tnum`` makes the digits equal width, so a live value keeps its decimal
#: point in one place instead of shuffling sideways each time it changes.
#: ``zero`` slashes the zero. Both are the difference between a number you can
#: read at a glance and one you have to stop and parse — and neither is
#: available through a Qt style sheet, so they are set on the font here.
_FIGURE_FEATURES = ("tnum", "zero")


def _with_figures(font: QFont) -> QFont:
    """Apply tabular, slashed figures where the Qt build supports it.

    ``QFont.setFeature`` arrived in Qt 6.7. On anything older the font is
    returned unchanged: proportional digits are a cosmetic loss, and refusing
    to build a window over one would not be.
    """
    if not (hasattr(font, "setFeature") and hasattr(QFont, "Tag")):
        return font
    for tag in _FIGURE_FEATURES:
        try:
            font.setFeature(QFont.Tag(tag), 1)
        except Exception:                      # unknown tag on an odd build
            pass
    return font


def mono(size: int = 11, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """The best monospace face this platform has, at *size*.

    Monospace for machine values is not decoration: it is what keeps a column
    of readings aligned on the decimal point. The figure features are applied
    on top, because a monospaced *face* still does not guarantee slashed zeros.
    """
    available = QFontDatabase.families()
    face = next((name for name in MONO_FACES if name in available), None)

    font = QFont(face, size) if face else QFont()
    if not face:
        font.setPointSize(size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return _with_figures(font)


# --------------------------------------------------------------------------- #
# The style sheet
# --------------------------------------------------------------------------- #
#
# Only what the console actually builds. A sheet that also dresses widgets
# nobody creates cannot be changed safely later: nothing tells you whether a
# rule is load-bearing or a leftover.
#
# Object names say what a thing *is* (Panel, HeadBar, Caption); the three
# dynamic properties — primary, quiet, stop/go — say how a button should carry
# itself. Both are set by the widgets in ui/, and renaming one here without
# renaming it there silently drops the styling rather than raising.

_SHEET = """
* {{
    outline: 0;
}}

QWidget {{
    background: {ground};
    color: {text};
    font-family: {sans};
    font-size: {t_body}px;
    font-weight: 400;
}}

/* Text and controls carry no fill of their own — they sit on whatever surface
   holds them. Without this, every label paints the window colour over its own
   card and the panes come out banded. */
QLabel,
QCheckBox,
QGroupBox,
QScrollArea,
QScrollArea > QWidget > QWidget,
QWidget#Bare {{
    background: transparent;
}}


/* ---- surfaces ----------------------------------------------------------- */

QWidget#Panel {{
    background: {card};
    border: 1px solid {rule};
    border-radius: 4px;
}}

QWidget#SidePane {{
    background: {rail};
    border-left: 1px solid {rule};
}}

/* The instrument's fascia: a shallow gradient and a hard bottom edge, so the
   bar reads as the front of the machine rather than as a web header. */
QWidget#HeadBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {raised}, stop:1 {rail});
    border-bottom: 1px solid {rule};
}}

QToolTip {{
    background: {card};
    color: {text};
    border: 1px solid {rule};
    padding: 5px 8px;
}}


/* ---- type --------------------------------------------------------------- */

QLabel#WordMark {{
    color: {text};
    font-size: {t_lead}px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}

/* Tracking tightens as the size grows — the same optical correction a type
   designer would build into an optical size, done by hand because the stack
   has no optical axis to lean on. */
QLabel#PaneHead {{
    font-size: {t_head}px;
    font-weight: 600;
    letter-spacing: -0.3px;
}}

QLabel#PaneNote,
QLabel#Subtle {{
    color: {faint};
    font-size: {t_small}px;
    font-weight: 400;
}}

/* Small, wide-tracked and gold: a caption reads as a label for what follows
   rather than as a heading competing with it. */
QLabel#Caption,
QGroupBox::title {{
    color: {gold};
    font-size: {t_caption}px;
    font-weight: 700;
    letter-spacing: 1.6px;
}}


/* ---- grouping ----------------------------------------------------------- */

QGroupBox {{
    background: {card};
    border: 1px solid {rule};
    border-radius: 5px;
    margin-top: 16px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
}}

/* Flush with the frame's left edge, not inset from it. A group title is a
   caption like any other, and it sits in a column with plain #Caption labels
   that align to the pane inset — 14px of indent here is the one thing on the
   page that does not line up with the rest. */
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 0px;
    top: 2px;
}}


/* ---- buttons ------------------------------------------------------------ */

/* A button is a physical thing: lit along its top edge, seated on a border,
   and it travels when pressed. The gradient is slight — two steps of the same
   graphite — because the depth has to survive on a dark ground without
   turning into a bevel from 1998. */
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {raised}, stop:1 {card});
    color: {text};
    border: 1px solid {rule};
    border-radius: 4px;
    padding: 8px 15px;
    font-weight: 600;
}}

QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {raised}, stop:1 {raised});
    border-color: {faint};
}}

/* Pressed: the light comes off the top edge and the label moves down a pixel.
   Nothing else in the sheet reports a click, so this is the whole affordance. */
QPushButton:pressed {{
    background: {rail};
    border-color: {faint};
    padding-top: 9px;
    padding-bottom: 7px;
}}

QPushButton:disabled {{
    background: {rail};
    color: {faint};
    border-color: {rule_soft};
}}

QPushButton[primary="true"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {gold_glow}, stop:1 {gold});
    color: {on_gold};
    border-color: {gold};
}}

QPushButton[primary="true"]:hover {{
    background: {gold_glow};
    border-color: {gold_glow};
}}

QPushButton[primary="true"]:pressed {{
    background: {gold};
    border-color: {gold};
}}

QPushButton[quiet="true"] {{
    background: transparent;
    border-color: transparent;
    color: {muted};
}}

QPushButton[quiet="true"]:hover {{
    background: {raised};
    color: {text};
}}

QPushButton[stop="true"] {{
    background: {alarm};
    color: #ffffff;
    border-color: {alarm};
}}

QPushButton[go="true"] {{
    background: {live};
    color: #ffffff;
    border-color: {live};
}}

QPushButton[stop="true"]:pressed,
QPushButton[go="true"]:pressed {{
    padding-top: 9px;
    padding-bottom: 7px;
}}

/* The light/dark switch. ToggleBtn paints nothing until it is checked, so the
   #Toggle bed behind it is what makes the pair read as one control. */
QWidget#Toggle {{
    background: {ground};
    border: 1px solid {rule};
    border-radius: 5px;
}}

QPushButton#ToggleBtn {{
    background: transparent;
    border: 0;
    border-radius: 3px;
    padding: 6px 16px;
    color: {muted};
    font-weight: 600;
}}

QPushButton#ToggleBtn:hover {{
    color: {text};
}}

QPushButton#ToggleBtn:checked {{
    background: {gold};
    color: {on_gold};
}}


/* ---- fields ------------------------------------------------------------- */

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox {{
    background: {well};
    color: {text};
    border: 1px solid {rule};
    border-radius: 3px;
    padding: 6px 9px;
    selection-background-color: {focus};
    selection-color: #ffffff;
}}

/* Focus is azure and two pixels wide: it has to be unmistakable without
   borrowing the gold, which already means something else here. */
QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {{
    border: 2px solid {focus};
}}

QCheckBox:focus,
QPushButton:focus {{
    border-color: {focus};
}}

QComboBox::drop-down {{
    border: 0;
    width: 22px;
}}

QComboBox::down-arrow {{
    image: url("{chevron_down}");
    width: 10px;
    height: 7px;
}}

QComboBox QAbstractItemView {{
    background: {card};
    border: 1px solid {rule};
    selection-background-color: {gold_soft};
    selection-color: {gold_glow};
    outline: 0;
}}

/* Qt stops drawing its own spin arrows once the frame is restyled, so they
   are supplied here. Without them the buttons are blank rectangles. */
QSpinBox::up-button,
QDoubleSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::down-button {{
    background: {raised};
    border: 0;
    border-left: 1px solid {rule};
    width: 20px;
}}

QSpinBox::up-button,
QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    border-top-right-radius: 3px;
}}

QSpinBox::down-button,
QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    border-top: 1px solid {rule};
    border-bottom-right-radius: 3px;
}}

QSpinBox::up-button:hover,
QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{
    background: {rule};
}}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {{
    image: url("{chevron_up}");
    width: 10px;
    height: 7px;
}}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {{
    image: url("{chevron_down}");
    width: 10px;
    height: 7px;
}}

QCheckBox {{
    spacing: 8px;
    padding: 2px 0;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {faint};
    border-radius: 3px;
    background: {well};
}}

QCheckBox::indicator:hover {{
    border-color: {gold};
}}

QCheckBox::indicator:checked {{
    background: {gold};
    border-color: {gold};
    image: url("{check}");
}}


/* ---- lists -------------------------------------------------------------- */

QTreeWidget,
QListWidget {{
    background: {card};
    border: 1px solid {rule};
    border-radius: 4px;
    /* The inset belongs on the viewport, not on each row: a row spans two
       columns, so row padding is paid four times across the width, which a
       narrow pane cannot afford without truncating channel names. Here it is
       paid twice, at the frame, which is the only place it shows. */
    padding-left: {tree_inset}px;
    padding-right: {tree_inset}px;
}}

QTreeWidget::item,
QListWidget::item {{
    padding: 6px 4px;
    border: 0;
}}

QTreeWidget::item:selected,
QListWidget::item:selected {{
    background: {focus_soft};
    color: {text};
}}

QTreeWidget::branch {{
    background: transparent;
}}


/* ---- the session drawer's tabs ------------------------------------------ */
/*
   Tabs with volume rather than an underline. An unselected tab is a real
   object sitting back in its bed; the selected one rises to the pane's own
   surface and loses its bottom edge, so the tab and the page it opens read as
   one piece of material. A flat bar with a coloured underline cannot say that
   — it only marks which word is current, and leaves the page floating.

   The gold sits on the *top* edge of the selected tab. On the bottom it would
   be the underline again; on top it reads as the lit edge of the raised one.
*/

QTabWidget::pane {{
    background: {card};
    border: 1px solid {rule};
    border-radius: 4px;
    top: -1px;
}}

QTabBar {{
    background: transparent;
}}

QTabBar::tab {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {rail}, stop:1 {ground});
    color: {muted};
    padding: 10px 18px 9px 18px;
    margin-right: 3px;
    border: 1px solid {rule};
    border-bottom: 1px solid {rule};
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: 600;
}}

QTabBar::tab:hover {{
    background: {raised};
    color: {text};
}}

/* Rises to the pane surface, keeps a lit top edge, and drops its bottom border
   so nothing rules a line between the tab and its own page. */
QTabBar::tab:selected {{
    background: {card};
    color: {text};
    border: 1px solid {rule};
    border-top: 2px solid {gold};
    border-bottom-color: {card};
    margin-top: 0;
    padding-top: 10px;
}}

/* Unselected tabs sit a little lower, which is what makes the selected one
   look lifted rather than merely differently coloured. */
QTabBar::tab:!selected {{
    margin-top: 3px;
}}


/* ---- chrome ------------------------------------------------------------- */

QMenuBar {{
    background: {rail};
    color: {text};
}}

QMenuBar::item:selected {{
    background: {raised};
}}

QMenu {{
    background: {card};
    border: 1px solid {rule};
}}

QMenu::item:selected {{
    background: {gold_soft};
    color: {gold_glow};
}}

QSplitter::handle {{
    background: {rule};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {rule};
    border-radius: 5px;
    min-height: 28px;
}}

QScrollBar::handle:horizontal {{
    background: {rule};
    border-radius: 5px;
    min-width: 28px;
}}

QScrollBar::handle:hover {{
    background: {faint};
}}

QScrollBar::add-line,
QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

QScrollBar::add-page,
QScrollBar::sub-page {{
    background: transparent;
}}
"""


_GLYPH_DIR = Path(__file__).resolve().parents[2] / "img" / "ui"


def _glyphs(name: str) -> dict:
    """Glyph URLs for a theme. Qt style sheets want forward slashes, on
    Windows too.

    Qt draws no tick and no spin arrow of its own once a control's frame has
    been restyled, so these are supplied as images. Without them a ticked box
    renders as a bare filled square.
    """
    variant = "dark" if name == "dark" else "light"
    return {
        "check": (_GLYPH_DIR / f"check-{variant}.svg").as_posix(),
        "chevron_up": (_GLYPH_DIR / f"chevron-up-{variant}.svg").as_posix(),
        "chevron_down": (_GLYPH_DIR / f"chevron-down-{variant}.svg").as_posix(),
    }


def stylesheet(palette: dict, name: str = "dark") -> str:
    """Render the sheet against a palette.

    One substitution pass: the palette, the glyph paths, the font stack and the
    tree inset all arrive as ``str.format`` keywords together. Anything the
    sheet names and the palette lacks raises ``KeyError`` here rather than
    rendering a rule that quietly does nothing.
    """
    return _SHEET.format(**palette, **_glyphs(name),
                         sans=SANS_STACK, tree_inset=TREE_INSET,
                         **{f"t_{k}": v for k, v in TYPE.items()})


def paint(app, name: str = "dark") -> dict:
    """Style the whole application, and hand back the palette that did it.

    The palette comes back because not everything on screen is styled by the
    sheet: the traces and the hand-painted lamps take their colours from the
    same dict, and a theme where those two disagree is worse than one theme.
    """
    palette = THEMES.get(name, DARK)
    app.setStyleSheet(stylesheet(palette, name))
    return palette
