# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Devices with nothing behind them.

A fresh clone has to be worth running before any hardware arrives, so the seed
configuration ships three of these. They are not stubs returning a constant: a
flat line hides everything a live view exists to show — drift, noise, the way a
range scales a signal — so each one synthesises a slow multi-harmonic drift
with gaussian noise on top, at an amplitude that suits what it pretends to
measure.

The signal is a function of wall-clock time rather than of a call counter.
Readers run at their own cadence, and a slow channel must not produce a
slower-looking waveform than a fast one sampling the same shape.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from industrybench.config.schema import DEFAULT_TEMPERATURE_UNIT
from industrybench.instruments.base import ChannelRead, Device


@dataclass(frozen=True)
class Signal:
    """A drifting waveform: offset + summed harmonics + gaussian noise."""

    offset: float
    #: seconds of wall clock per radian of the fundamental
    period: float
    #: (amplitude, frequency multiple) per harmonic
    harmonics: Tuple[Tuple[float, float], ...]
    noise: float

    def at(self, seconds: float) -> float:
        phase = seconds / self.period
        swing = sum(amplitude * math.sin(phase * multiple)
                    for amplitude, multiple in self.harmonics)
        return self.offset + swing + random.gauss(0, self.noise)


class SimulatedDevice(Device):
    """Common behaviour: opening always works, and a read is the class's
    signal sampled now, shaped for the channel that asked."""

    #: what this device pretends to observe
    SIGNAL: Signal
    #: unit for a channel that does not name one
    UNIT = ""
    #: what ``range`` means when a channel omits it; None means no scaling
    FULL_SCALE: Optional[float] = None

    def open(self) -> bool:
        self.live = True
        self.logger.info("%s open (simulated)", self.name)
        return True

    def close(self) -> bool:
        self.live = False
        self.logger.info("%s released", self.name)
        return True

    def is_open(self) -> bool:
        return self.live

    def read_channel(self, channel: Dict[str, Any]) -> Optional[ChannelRead]:
        if not self.is_open():
            return None
        value, unit = self._shape(self.SIGNAL.at(time.time()), channel)
        # Built directly rather than through `reading`: a simulated device
        # converts its own value, so it reports the unit it actually produced
        # instead of the one the channel asked for. The column name still comes
        # from the shared rule.
        return ChannelRead(value=value, column=self._column(channel), unit=unit)

    # ---------------------------------------------------------------- shaping
    def _shape(self, raw: float, channel: Dict[str, Any]) -> Tuple[float, str]:
        """Turn the raw waveform into what this channel asked for."""
        return self._ranged(raw, channel), channel.get("unit", self.UNIT)

    def _ranged(self, raw: float, channel: Dict[str, Any]) -> float:
        """Apply the channel's range as a gain, the way a real range would.

        A non-numeric range — ``"AUTO"``, which is what the console stores by
        default — means the instrument chooses, so nothing is scaled.
        """
        if self.FULL_SCALE is None:
            return raw
        span = channel.get("range", self.FULL_SCALE)
        if isinstance(span, bool) or not isinstance(span, (int, float)):
            return raw
        return raw * span


class SimulatedProbe(SimulatedDevice):
    """A room that warms and cools over an afternoon."""

    SIGNAL = Signal(offset=20.0, period=1000.0,
                    harmonics=((2.0, 1.0), (1.0, 10.0)), noise=0.1)
    UNIT = DEFAULT_TEMPERATURE_UNIT

    #: Celsius -> whatever the channel asked for
    SCALES = {
        "fahrenheit": lambda c: c * 9 / 5 + 32,
        "kelvin": lambda c: c + 273.15,
    }

    def _shape(self, raw: float, channel: Dict[str, Any]) -> Tuple[float, str]:
        # Reported exactly as configured, matched case-insensitively — see the
        # note in instruments.onewire: the unit reaches a file header.
        unit = str(channel.get("unit", self.UNIT))
        convert = self.SCALES.get(unit.strip().lower())
        return (convert(raw) if convert else raw), unit


class SimulatedVoltmeter(SimulatedDevice):
    """A volt-scale signal, ranged like a DVM channel."""

    SIGNAL = Signal(offset=0.0, period=100.0,
                    harmonics=((0.5, 1.0), (0.2, 5.0)), noise=0.001)
    UNIT = "V"
    FULL_SCALE = 1.0


class SimulatedAmmeter(SimulatedDevice):
    """A milliamp-scale signal, on the 1 mA range unless told otherwise."""

    SIGNAL = Signal(offset=0.0, period=50.0,
                    harmonics=((0.0005, 1.0), (0.0002, 3.0)), noise=0.000001)
    UNIT = "A"
    FULL_SCALE = 0.001
