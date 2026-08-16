# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Thermocouple EMF -> degrees Celsius, in software.

A DVM with bare thermocouple wire in its terminals measures one thing: the EMF
the pair of dissimilar metals develops, in volts. Two steps turn that into a
temperature.

**The terminals count too.** That EMF is produced by the *difference* between
the hot junction and the terminal block the wires land on, so 0 V means "the
same as the terminals", not 0 °C. The EMF the terminals would themselves
produce is added back before anything is converted. Their temperature is taken
from the channel configuration — an assumption, and the main reason a probe
read by the instrument's own temperature function is more accurate than one
converted here.

**Going backwards.** NIST ITS-90 publishes temperature -> EMF; the inverse
polynomials (Monograph 175, Table B) go the other way, in millivolts, over a
few spans per junction type. The coefficients below are that published table.
They are reference data, not a choice made here, and the only correct thing to
do with them is copy them accurately.

Junction types K, J, T and E are covered, which is what a bench DVM with bare
wire usually sees. Beyond a type's tabulated span the answer is pinned to the
nearest end rather than extrapolated: a polynomial fitted over 0–20 mV says
nothing believable at 200 mV, and a wildly wrong number reads as a real
measurement, where a pinned one reads as a rail.

    from industrybench.instruments.thermocouple import emf_to_celsius
    emf_to_celsius(0.001053, "T", 21.5)   # -> 46.866 °C
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Tuple


class _Span(NamedTuple):
    """One tabulated stretch of the inverse polynomial, in millivolts."""

    low_mv: float
    high_mv: float
    coefficients: Tuple[float, ...]     # d0..dn, ascending powers


# --------------------------------------------------------------------------- #
# NIST ITS-90 inverse coefficients: mV -> °C   (Monograph 175, Table B)
# --------------------------------------------------------------------------- #

_INVERSE: Dict[str, Tuple[_Span, ...]] = {

    "K": (
        _Span(-5.891, 0.000, (
            0.0000000E+00,  2.5173462E+01, -1.1662878E+00,
           -1.0833638E+00, -8.9773540E-01, -3.7342377E-01,
           -8.6632643E-02, -1.0450598E-02, -5.1920577E-04,
        )),
        _Span(0.000, 20.644, (
            0.000000E+00,  2.508355E+01,  7.860106E-02,
           -2.503131E-01,  8.315270E-02, -1.228034E-02,
            9.804036E-04, -4.413030E-05,  1.057734E-06,
           -1.052755E-08,
        )),
        _Span(20.644, 54.886, (
           -1.318058E+02,  4.830222E+01, -1.646031E+00,
            5.464731E-02, -9.650715E-04,  8.802193E-06,
           -3.110810E-08,
        )),
    ),

    "J": (
        _Span(-8.095, 0.000, (
            0.000000E+00,  1.952826E+01, -1.228618E+00,
           -1.075217E+00, -5.908693E-01, -1.725671E-01,
           -2.813151E-02, -2.396337E-03, -8.382332E-05,
        )),
        _Span(0.000, 42.919, (
            0.000000E+00,  1.978425E+01, -2.001204E-01,
            1.036969E-02, -2.549687E-04,  3.585153E-06,
           -5.344285E-08,  5.099890E-10,
        )),
        _Span(42.919, 69.553, (
           -3.113581E+03,  3.005383E+02, -9.947732E+00,
            1.702766E-01, -1.430334E-03,  4.738860E-06,
        )),
    ),

    "T": (
        _Span(-5.603, 0.000, (
            0.0000000E+00,  2.5949192E+01, -2.1316967E-01,
            7.9018692E-01,  4.2527777E-01,  1.3304473E-01,
            2.0241446E-02,  1.2668171E-03,
        )),
        _Span(0.000, 20.872, (
            0.000000E+00,  2.592800E+01, -7.602961E-01,
            4.637791E-02, -2.165394E-03,  6.048144E-05,
           -7.293422E-07,
        )),
    ),

    "E": (
        _Span(-8.825, 0.000, (
            0.0000000E+00,  1.6977288E+01, -4.3514970E-01,
           -1.5859697E-01, -9.2502871E-02, -2.6084314E-02,
           -4.1360199E-03, -3.4034030E-04, -1.1564890E-05,
        )),
        _Span(0.000, 76.373, (
            0.0000000E+00,  1.7057035E+01, -2.3301759E-01,
            6.5435585E-03, -7.3562749E-05, -1.7896001E-06,
            8.4036165E-08, -1.3735879E-09,  1.0629823E-11,
           -3.2447087E-14,
        )),
    ),
}


# Cold junction: EMF at 25 °C plus a linear sensitivity. Good to about ±0.3 °C
# over 0–100 °C, which is where a terminal block lives; the full forward
# polynomial would be more code for less than the DVM's own error.
#   type -> (mV at 25 °C, mV per °C)
_COLD_JUNCTION: Dict[str, Tuple[float, float]] = {
    "K": (1.0003, 0.0410),
    "J": (1.2773, 0.0519),
    "T": (0.9922, 0.0408),
    "E": (1.4949, 0.0632),
}


#: junction types this module can invert
JUNCTION_TYPES = list(_INVERSE)


def _terminal_mv(junction: str, terminals_c: float) -> float:
    """The EMF the terminal block itself contributes, in millivolts."""
    at_25, per_degree = _COLD_JUNCTION[junction]
    return at_25 + per_degree * (terminals_c - 25.0)


def _polynomial(coefficients: Tuple[float, ...], x: float) -> float:
    """Horner's method: n multiplications instead of n powers."""
    total = 0.0
    for coefficient in reversed(coefficients):
        total = total * x + coefficient
    return total


def emf_to_celsius(volts: float, junction: str = "K",
                   terminals_c: float = 25.0) -> float:
    """Convert one thermocouple reading to degrees Celsius.

    Parameters
    ----------
    volts       what the DVM reported, in volts
    junction    "K", "J", "T" or "E"
    terminals_c temperature of the terminal block the wires land on
    """
    key = str(junction).upper()
    spans = _INVERSE.get(key)
    if spans is None:
        raise ValueError(
            f"No inverse table for junction type {junction!r}. "
            f"Available: {JUNCTION_TYPES}")

    total_mv = volts * 1000.0 + _terminal_mv(key, terminals_c)
    # Pinned before lookup, not after: a reading outside the table is reported
    # as the end of the tabulated range rather than as an extrapolated fiction.
    total_mv = min(max(total_mv, spans[0].low_mv), spans[-1].high_mv)

    for span in spans:
        if span.low_mv <= total_mv <= span.high_mv:
            return _polynomial(span.coefficients, total_mv)

    # Unreachable while a type's spans stay contiguous, which the NIST table
    # guarantees. A gap introduced by a bad edit must not return silence.
    raise ValueError(f"{total_mv:.4f} mV falls in a gap in the {key} table")
