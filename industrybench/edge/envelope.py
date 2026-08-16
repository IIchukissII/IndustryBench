# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The reading envelope — the fixed message schema.

Locally, columns may come and go as measurements are added; SQLite tolerates
that. A shared stream does not: a topic whose shape changes per producer is a
problem for every consumer downstream. So everything that leaves this bench is
flattened into one fixed record, one row per channel per tick.

The field set is not invented here — it is IndustryFlow's ingestion contract
(``services/ingestion_service/schemas.py``, ``SensorDataInput``):

    timestamp     when the reading was taken
    sensor_id     UUID of the channel      <- stored on the measurement
    equipment_id  UUID of the equipment    <- the project's equipment under test
    site_id       where the bench stands   <- StationIdentity.site_id
    value         the number
    unit          max 20 chars, per the platform's schema
    quality_code  0 = good, 1 = uncertain, 2 = bad

``company_id`` is deliberately absent: ADR-0002 requires the tenant to come from
the verified client certificate, never from the payload. The bench's own
``station_id`` is absent for the same reason — it is the *device* identity, which
the certificate already carries.

Note what ``equipment_id`` is not: it is not the multimeter. Equipment is the
thing being observed, and the instrument is only the means of observing it, so
the id belongs to the project's device under test. One multimeter can serve
different equipment in different projects.

**Quality is where a failed read belongs.** A NaN is not "no data" — the
instrument was asked and it answered, but the answer cannot be trusted. Dropping
the row would silently shorten the series; sending NaN as a value would poison
every downstream average. Sending the last good value with ``quality_code = 2``
keeps the series dense and lets consumers decide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

QUALITY_GOOD = 0
QUALITY_UNCERTAIN = 1
QUALITY_BAD = 2

QUALITY_LABEL = {
    QUALITY_GOOD: "good",
    QUALITY_UNCERTAIN: "uncertain",
    QUALITY_BAD: "bad",
}

UNIT_MAX_LEN = 20          # SensorDataInput.unit: Field(None, max_length=20)
SITE_MAX_LEN = 100         # SensorDataInput.site_id: max_length=100


def quality_for(value: Any) -> int:
    """Classify a raw reading.

    A non-finite or non-numeric result means the read completed but produced
    nothing trustworthy — that is QUALITY_BAD, not a missing sample.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return QUALITY_BAD
    return QUALITY_GOOD if math.isfinite(value) else QUALITY_BAD


@dataclass
class Reading:
    """One channel, one instant — the unit of everything that leaves the bench."""

    timestamp: datetime
    sensor_id: str
    equipment_id: str
    site_id: str
    value: float
    unit: Optional[str] = None
    quality_code: int = QUALITY_GOOD

    # Local-only context. Never sent: the platform identifies a signal by UUID,
    # but an operator staring at the outbox needs to know which knob to turn.
    instrument: str = ""
    channel: str = ""

    @property
    def is_trustworthy(self) -> bool:
        return self.quality_code == QUALITY_GOOD

    def to_payload(self) -> Dict[str, Any]:
        """The exact JSON body IndustryFlow's ingestion endpoint accepts.

        A non-finite value is replaced with 0.0: JSON has no NaN, and the
        quality code already carries the truth. Emitting bare NaN would produce
        invalid JSON that the endpoint rejects outright, losing the fact that a
        read was even attempted.
        """
        value = self.value
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not math.isfinite(value):
            value = 0.0

        payload = {
            "timestamp": _iso_utc(self.timestamp),
            "sensor_id": self.sensor_id,
            "equipment_id": self.equipment_id,
            "site_id": self.site_id[:SITE_MAX_LEN],
            "value": float(value),
            "quality_code": int(self.quality_code),
        }
        if self.unit:
            payload["unit"] = self.unit[:UNIT_MAX_LEN]
        return payload


def _iso_utc(ts: datetime) -> str:
    """RFC 3339 in UTC. A naive timestamp is assumed to be local time, which is
    what datetime.now() gives us, and converted rather than mislabelled."""
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def readings_from_scan(scan, station, channel_map: Dict[str, Dict[str, str]],
                       equipment_id: str):
    """Flatten one scan into a Reading per channel.

    *channel_map* maps column name -> {"device": ..., "sensor_id": ...}; the
    runner builds it once per run, so this stays a pure transformation.

    A column with no stored sensor id is skipped rather than given an
    improvised one: a reading sent under an id that will not survive the next
    restart corrupts that channel's history upstream, which is worse than the
    gap. The runner names such columns when it builds the map.
    """
    out = []
    for column, cell in scan:
        known = channel_map.get(column)
        if not known or not known.get("sensor_id"):
            continue
        out.append(Reading(
            timestamp=scan.taken_at,
            sensor_id=known["sensor_id"],
            equipment_id=equipment_id,
            site_id=station.site_id,
            value=cell.value if isinstance(cell.value, (int, float)) else float("nan"),
            unit=cell.unit or None,
            quality_code=quality_for(cell.value),
            instrument=known.get("device", ""),
            channel=column,
        ))
    return out
