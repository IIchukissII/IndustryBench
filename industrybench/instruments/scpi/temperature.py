# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building a valid ``CONFigure:TEMPerature``.

    CONFigure:TEMPerature [{<probe>|DEF}
                          [,{<subtype>|DEF}
                          [,1
                          [,{<resolution>|MIN|MAX|DEF}]]]]

The reason this is a module and not an f-string at the call site is that the
probe family and its subtype are not independent. ``85`` is a legal RTD alpha
and a meaningless thermocouple letter — and an instrument handed the wrong
pairing does not object. It configures something else and answers with a
plausible number, which is the worst possible failure for a measurement tool:
wrong, quiet, and indistinguishable from right. So the pairing is checked here,
before anything reaches the wire, and a mismatch raises.

============================  ==============================  =========
probe family                  subtype                         default
============================  ==============================  =========
FRTD, RTD                     85, 89, 91, 92                  85
FTHermistor, THERmistor       2252, 3000, 5000, 10000, 30000  5000
TC                            B, E, J, K, N, R, S, T          B
ANY                           none — the subtype is omitted
============================  ==============================  =========

The third parameter is the literal ``1`` the syntax requires. Resolution is
accepted and passed through: many meters ignore the value, but the command has
to be syntactically complete for it to be accepted at all.

    >>> temperature_command()
    'CONF:TEMP FRTD,85,1,DEF'
    >>> temperature_command("TC", "T")
    'CONF:TEMP TC,T,1,DEF'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

#: asks for "whatever this parameter's default is"
DEFAULT = "DEF"
#: resolution keywords the instrument accepts in place of a number
RESOLUTION_WORDS = frozenset({"MIN", "MAX", DEFAULT})


@dataclass(frozen=True)
class _Family:
    """One probe family: how it is spelled on the wire, and what it accepts."""

    spelling: str
    subtypes: FrozenSet[str]
    fallback: Optional[str]
    #: thermocouple subtypes are letters, accepted in any case; the rest are
    #: numeric strings, where case does not arise
    any_case: bool = False
    #: how this family is named in an error message
    described: str = ""

    def subtype(self, asked) -> Optional[str]:
        if not self.subtypes:                       # ANY carries no subtype
            return None
        if asked is None or str(asked).upper() == DEFAULT:
            return self.fallback

        given = str(asked)
        candidate = given.upper() if self.any_case else given
        if candidate not in self.subtypes:
            raise ValueError(
                f"{given!r} is not a subtype of {self.described}. "
                f"Use one of {sorted(self.subtypes)}, or {DEFAULT}."
            )
        return candidate


_RTD_SUBTYPES = frozenset({"85", "89", "91", "92"})
_THERMISTOR_SUBTYPES = frozenset({"2252", "3000", "5000", "10000", "30000"})

#: every accepted spelling of a probe family, upper-cased, -> the family
FAMILIES: Dict[str, _Family] = {
    "FRTD": _Family("FRTD", _RTD_SUBTYPES, "85", described="the FRTD family"),
    "RTD": _Family("RTD", _RTD_SUBTYPES, "85", described="the RTD family"),
    "FTHERMISTOR": _Family("FTHermistor", _THERMISTOR_SUBTYPES, "5000",
                           described="the FTHermistor family"),
    "THERMISTOR": _Family("THERmistor", _THERMISTOR_SUBTYPES, "5000",
                          described="the THERmistor family"),
    "TC": _Family("TC", frozenset({"B", "E", "J", "K", "N", "R", "S", "T"}),
                  "B", any_case=True, described="a thermocouple"),
    "ANY": _Family("ANY", frozenset(), None, described="the ANY family"),
}

DEFAULT_FAMILY = "FRTD"

#: the spellings the console offers, in the order it offers them
PROBE_CHOICES = ("FRTD", "RTD", "FTHermistor", "THERmistor", "TC", "ANY")


def subtypes_of(probe: str) -> list:
    """The subtypes a probe family accepts, sorted, for the console to offer."""
    family = FAMILIES.get(str(probe).upper())
    return sorted(family.subtypes) if family else []


def _family_of(asked) -> _Family:
    if asked is None or str(asked).upper() == DEFAULT:
        return FAMILIES[DEFAULT_FAMILY]
    family = FAMILIES.get(str(asked).upper())
    if family is None:
        raise ValueError(
            f"{asked!r} is not a probe family. "
            f"Use one of {sorted(FAMILIES)}, or {DEFAULT}."
        )
    return family


def _resolution(asked) -> str:
    if asked is None:
        return DEFAULT

    word = str(asked).upper()
    if word in RESOLUTION_WORDS:
        return word
    try:
        float(asked)
    except (ValueError, TypeError):
        raise ValueError(
            f"{asked!r} is not a resolution. Use a number, MIN, MAX or DEF."
        ) from None
    return str(asked)


@dataclass(frozen=True)
class TempFunction:
    """A probe/subtype/resolution triple known to be legal together.

    Built through :meth:`from_config`, which is where validation happens, so an
    instance always renders to a command the instrument accepts.
    """

    probe: str                     # canonical wire spelling
    subtype: Optional[str]         # None for ANY
    resolution: str

    @classmethod
    def from_config(cls, raw: Optional[dict]) -> "TempFunction":
        """Validate a channel's ``configure`` block.

        Every key is optional; ``None`` or ``DEF`` asks for the default.
        Raises ValueError on a pairing the instrument would misread.
        """
        raw = raw or {}
        family = _family_of(raw.get("probe_type"))
        return cls(
            probe=family.spelling,
            subtype=family.subtype(raw.get("sensor_type")),
            resolution=_resolution(raw.get("resolution")),
        )

    def _arguments(self) -> str:
        parts = [self.probe]
        if self.subtype is not None:
            parts.append(self.subtype)
        parts += ["1", self.resolution]
        return ",".join(parts)

    def command(self) -> str:
        """``CONF:TEMP …`` — switches the function; read after it with ``READ?``."""
        return f"CONF:TEMP {self._arguments()}"

    def one_shot(self) -> str:
        """``MEAS:TEMP? …`` — configure, trigger and read in one blocking query.

        Avoids the gap in configure-then-read, where a slow probe can outlast
        the timeout of a ``READ?`` sent as a separate transaction.
        """
        return f"MEAS:TEMP? {self._arguments()}"

    def __str__(self) -> str:
        return self.command()


def temperature_command(probe: Optional[str] = None,
                        subtype: Optional[str] = None,
                        resolution: Optional[str] = None) -> str:
    """Validate the three parameters and render the ``CONF:TEMP`` command.

    >>> temperature_command("THERmistor", "2252")
    'CONF:TEMP THERmistor,2252,1,DEF'
    """
    return TempFunction.from_config({
        "probe_type": probe,
        "sensor_type": subtype,
        "resolution": resolution,
    }).command()
