# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The device contract.

A driver answers four questions — can you open, are you open, give me this
channel, let go — and this module turns those into the guarantee the rest of
the bench is built on: **reading a channel never raises and never drops a
column.**

Drivers signal trouble in whatever way is natural to them: return ``None``,
raise, or hand back a number that is not finite. All three arrive here and
leave as a :class:`ChannelRead` that still carries the channel's name and unit,
holding :data:`UNREADABLE`. Downstream, ``edge.envelope`` turns that into
``quality_code = 2``, so the platform is told the channel was asked and could
not be trusted.

The alternative — omitting the column — is indistinguishable at every later
stage from a channel nobody configured, and a series that quietly shortens is
worse than one that says where it is unreliable.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

#: what a channel holds when the device could not give a usable answer
UNREADABLE = float("nan")


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class ChannelRead:
    """One channel's answer to one request."""

    value: float
    column: str
    unit: str = ""
    #: why the value is a placeholder, when it is one
    fault: Optional[str] = None

    @property
    def usable(self) -> bool:
        """False for a read that completed without producing a real number."""
        return _numeric(self.value) and math.isfinite(self.value)

    def __str__(self) -> str:
        return f"{self.column}: {self.value} {self.unit}".rstrip()


class Device(ABC):
    """A thing on the bench, and nothing about how it talks.

    Subclasses implement the four abstract members. Walking the channel list,
    folding a fault into a reading and describing state are handled here, so
    every driver behaves identically when its hardware misbehaves.
    """

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        self.live = False
        self.enabled = self.config.get("enabled", True)
        self.channel_configs = self.config.get("channels", []) or []

    # ------------------------------------------------------------- the driver
    @abstractmethod
    def open(self) -> bool:
        """Take hold of the device. True if it can be read from afterwards."""

    @abstractmethod
    def close(self) -> bool:
        """Let go of the device. True if it was released cleanly."""

    @abstractmethod
    def is_open(self) -> bool:
        """Whether a read would currently reach the hardware."""

    @abstractmethod
    def read_channel(self, channel: Dict[str, Any]) -> Optional[ChannelRead]:
        """Read one channel. Return None — or raise — if it cannot be read."""

    # ------------------------------------------------------------ the channels
    @property
    def measures(self) -> List[str]:
        """What every channel claims to read, switched on or not.

        Derived on each call rather than cached: a live edit swaps
        ``channel_configs`` underneath a running device, and a snapshot taken
        in ``__init__`` would describe the configuration it replaced.
        """
        return [c.get("measures", "unknown") for c in self.channel_configs]

    def channels(self) -> Iterator[Dict[str, Any]]:
        """The channel configurations that are switched on."""
        for channel in self.channel_configs:
            if channel.get("enabled", True):
                yield channel

    def column_names(self) -> List[str]:
        """Output column of every live channel, named if it has no name."""
        return [self._column(c) for c in self.channels()]

    def _column(self, channel: Dict[str, Any]) -> str:
        return (channel.get("column")
                or f"{self.name}_{channel.get('measures', 'unknown')}")

    # ------------------------------------------------------------ the readings
    def read_all(self) -> List[ChannelRead]:
        """Read every live channel once. Always returns; never raises."""
        if not self.is_open():
            self.logger.error("%s is not open", self.name)
            return []
        return [self._guarded(c) for c in self.channels()]

    def _guarded(self, channel: Dict[str, Any]) -> ChannelRead:
        try:
            reading = self.read_channel(channel)
        except Exception as e:
            self.logger.error("%s: reading %s failed: %s",
                              self.name, self._column(channel), e)
            return self.unread(channel, str(e))
        if reading is None:
            return self.unread(channel, "the device returned nothing")
        return reading

    def reading(self, channel: Dict[str, Any], value: Any,
                fault: Optional[str] = None) -> ChannelRead:
        """Build a read carrying this channel's column name and unit.

        Labels come from here rather than from each driver so that a value is
        named the same way whichever driver took it, and whether or not the
        read worked. Left to the drivers, two of them end up disagreeing about
        the unit of the same missing field.
        """
        return ChannelRead(
            value=value,
            column=self._column(channel),
            unit=channel.get("unit", ""),
            fault=fault,
        )

    def unread(self, channel: Dict[str, Any], why: str) -> ChannelRead:
        """A placeholder that keeps the column; see the module docstring."""
        return self.reading(channel, UNREADABLE, why)

    # -------------------------------------------------------------- describing
    def faults(self) -> List[str]:
        """Everything wrong with this device's configuration.

        All of them, not the first: fixing a configuration one logged line per
        restart is a poor way to spend an afternoon.
        """
        found = []
        if not self.name:
            found.append("the device has no name")
        if not self.channel_configs:
            found.append("no channels are configured")
        for i, channel in enumerate(self.channel_configs):
            if not channel.get("column"):
                found.append(f"channel {i} has no column name")
            if not channel.get("measures"):
                found.append(f"channel {i} does not say what it measures")
        return found

    def usable(self) -> bool:
        """Whether this device is configured well enough to be read."""
        found = self.faults()
        for fault in found:
            self.logger.error("%s: %s", self.name or "(unnamed)", fault)
        return not found

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "open": self.live,
            "enabled": self.enabled,
            "measures": self.measures,
            "columns": self.column_names(),
        }

    def __repr__(self) -> str:
        state = "open" if self.live else "closed"
        return f"<{type(self).__name__} {self.name!r} {state}>"
