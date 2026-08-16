# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A scan: every channel's value at one instant.

The bench reads channels independently — each instrument on its own cadence —
but a local file wants rows, and a row needs a moment it belongs to. A scan is
that row: the value each channel held when the assembler looked, stamped with
when it looked.

Two properties are load-bearing:

*A scan is immutable once taken.* It is handed to the sinks, kept in the
display buffer and read from the Qt thread, all without copying. A structure
that could be edited after the fact would let a late writer change a row a sink
has already written.

*A channel that could not be read still appears.* Its value is non-finite
rather than absent, so the column keeps its place in the grid and
``edge.envelope`` can mark it as untrustworthy instead of the row silently
growing shorter. See ``instruments.base`` for where that guarantee starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple


@dataclass(frozen=True, slots=True)
class Cell:
    """One channel's reading, with the unit it was reported in.

    The unit travels with the value rather than being looked up per column:
    a channel's unit is editable while a run is in progress, and a row has to
    say which unit *its* number was taken in.
    """

    value: float
    unit: str = ""


@dataclass(frozen=True, slots=True)
class Scan:
    """One timestamped row: channel name -> :class:`Cell`."""

    taken_at: datetime
    cells: Mapping[str, Cell]

    @classmethod
    def taken(cls, cells: Mapping[str, Cell],
              at: Optional[datetime] = None) -> "Scan":
        """Freeze *cells* into a scan stamped ``at`` (default: now).

        The mapping is wrapped read-only rather than trusted: the assembler
        builds it from a live snapshot dict, and a view that stayed writable
        would let the next tick mutate a row already handed downstream.
        """
        return cls(taken_at=at or datetime.now(),
                   cells=MappingProxyType(dict(cells)))

    # ------------------------------------------------------------------ reading
    @property
    def columns(self) -> Tuple[str, ...]:
        """Channel names in this scan, in the order they were assembled."""
        return tuple(self.cells)

    def __iter__(self) -> Iterator[Tuple[str, Cell]]:
        return iter(self.cells.items())

    def __len__(self) -> int:
        return len(self.cells)

    def __bool__(self) -> bool:
        return bool(self.cells)

    def cell(self, column: str) -> Optional[Cell]:
        """The reading for one channel, or None if it is not in this scan."""
        return self.cells.get(column)

    def value(self, column: str) -> Optional[float]:
        cell = self.cells.get(column)
        return cell.value if cell is not None else None

    # ------------------------------------------------------------- serialising
    def as_json(self) -> Dict[str, Any]:
        """The JSON form written by ``sinks.json``.

        Each reading keeps its own object rather than being flattened into
        ``name`` and ``name_unit`` siblings: a channel legitimately named
        ``supply_unit`` would collide with the unit of ``supply``, and a
        consumer cannot tell the two apart afterwards.
        """
        return {
            "taken_at": self.taken_at.isoformat(),
            "readings": {
                column: {"value": cell.value, "unit": cell.unit}
                for column, cell in self.cells.items()
            },
        }
