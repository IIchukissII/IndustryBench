# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Station (bench) identity — this bench *is* the gateway.

IndustryFlow expects sensor data to arrive from a gateway holding a hardware- or
host-bound identity (ADR-0002). IndustryBench does not sit behind a separate
gateway box: the PC running it is the gateway. It holds the client certificate,
it owns the local queue, and it is the thing the platform authenticates. So the
``station_id`` here is the ``device_id`` of ADR-0002.

That ADR is explicit about what the role means: producers authenticate with
**mutual TLS**, and the tenant (``company_id``) is read from the verified client
certificate — never from the payload. So this module deliberately holds no
tenant and no bearer secret. It holds:

  station_id  the bench's own UUID — the ADR-0002 ``device_id``. It identifies
              the *producer*, not the subject, so it is never in the payload:
              the certificate carries it. The subject is the project's
              ``equipment_id`` (see ``acquisition.project``).
  site_id     where the bench physically stands ("lab_north").
  cert/key/ca the mTLS material presented to the ingestion edge.

Every channel additionally carries its own ``sensor_id`` UUID, because
IndustryFlow identifies a signal by sensor UUID, not by a display name.

That id is **minted once and stored** on the channel (see
``ensure_sensor_ids``), never derived from the device or column name. This is
the part that actually matters. Deriving it would be less code, but renaming a
device or a column would then mint a new sensor and orphan years of that
channel's history upstream. Names are a local display concern and must stay
free to change; the identity under them must not.

Nothing here is secret except the private key, which is referenced by path and
never copied into the configuration store.
"""

from __future__ import annotations

import logging
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_KEY = "station"

#: the key each measurement dict carries its durable identity under
SENSOR_ID_KEY = "sensor_id"


@dataclass
class StationIdentity:
    """The bench's identity and its mTLS material."""

    station_id: str                      # -> ADR-0002 device_id (from the cert)
    site_id: str = "unassigned"
    name: str = ""
    ingest_url: str = ""                 # e.g. https://ingest.example/api/v1/sensor-data
    client_cert: str = ""                # PEM path
    client_key: str = ""                 # PEM path
    ca_cert: str = ""                    # PEM path (verifies the server)
    forward_enabled: bool = False

    def stored(self) -> Dict[str, Any]:
        return {
            "station_id": self.station_id,
            "site_id": self.site_id,
            "name": self.name,
            "ingest_url": self.ingest_url,
            "client_cert": self.client_cert,
            "client_key": self.client_key,
            "ca_cert": self.ca_cert,
            "forward_enabled": self.forward_enabled,
        }

    @classmethod
    def restore(cls, d: Optional[Dict[str, Any]]) -> "StationIdentity":
        d = d or {}
        sid = str(d.get("station_id") or "").strip()
        if not _is_uuid(sid):
            sid = str(uuid.uuid4())
        return cls(
            station_id=sid,
            site_id=str(d.get("site_id") or "unassigned"),
            name=str(d.get("name") or _default_name()),
            ingest_url=str(d.get("ingest_url") or ""),
            client_cert=str(d.get("client_cert") or ""),
            client_key=str(d.get("client_key") or ""),
            ca_cert=str(d.get("ca_cert") or ""),
            forward_enabled=bool(d.get("forward_enabled", False)),
        )

    # -- readiness ---------------------------------------------------------
    def missing_for_forwarding(self) -> list[str]:
        """What still has to be configured before readings can be forwarded.

        Empty list means ready. Checked as *files on disk*, not just as set
        strings, because a path that no longer resolves is the common failure.
        """
        missing = []
        if not self.ingest_url:
            missing.append("ingest URL")
        for label, p in (("client certificate", self.client_cert),
                         ("client key", self.client_key),
                         ("CA certificate", self.ca_cert)):
            if not p:
                missing.append(label)
            elif not Path(p).is_file():
                missing.append(f"{label} (not found at {p})")
        return missing

    def can_forward(self) -> bool:
        return self.forward_enabled and not self.missing_for_forwarding()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _default_name() -> str:
    try:
        return f"bench-{socket.gethostname()}".lower()
    except Exception:
        return "bench"


def new_sensor_id() -> str:
    """Mint a fresh channel identity."""
    return str(uuid.uuid4())


def ensure_sensor_ids(devices) -> int:
    """Give every channel a durable ``sensor_id``, in place.

    Returns how many were minted, so the caller knows whether the configuration
    needs saving. Existing ids are never touched or regenerated — that is the
    whole point. One that is present but malformed is replaced, since the
    platform would reject it anyway.

    Call this after loading the devices and after any edit that adds a channel,
    then persist. A minted-but-unsaved id would change on the next launch,
    which is exactly the failure this guards against.
    """
    minted = 0
    for device in devices or []:
        for channel in device.get("channels", []) or []:
            current = str(channel.get(SENSOR_ID_KEY) or "").strip()
            if not _is_uuid(current):
                channel[SENSOR_ID_KEY] = new_sensor_id()
                minted += 1
    return minted


def load_station(config) -> StationIdentity:
    """Read the station identity from the configuration store, generating one
    on first use and persisting it so the bench keeps its id for life."""
    try:
        raw = config.bench.get(CONFIG_KEY)
    except Exception:
        raw = None

    station = StationIdentity.restore(raw)

    if not raw or raw.get("station_id") != station.station_id:
        # First run, or a missing id we have just replaced — persist it.
        try:
            config.bench[CONFIG_KEY] = station.stored()
            config.save_bench()
            logger.info("Station identity established: %s (%s)",
                        station.name, station.station_id)
        except Exception as e:
            logger.error("Could not persist the station identity: %s", e)

    return station


def save_station(config, station: StationIdentity) -> bool:
    """Write the station identity back to the configuration store."""
    try:
        config.bench[CONFIG_KEY] = station.stored()
        return bool(config.save_bench())
    except Exception as e:
        logger.error("Could not save the station identity: %s", e)
        return False
