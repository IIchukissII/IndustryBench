# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Store-and-forward outbox.

A run must never stop, slow down, or lose data because the network is down. So
the ordering is strictly: **write locally first, forward later**. The measurement
loop's only contact with the network is an ``append()`` that touches SQLite and
returns; a background worker drains the queue when — and only when — the platform
is reachable.

That makes the local SQLite file a queue rather than merely an output. Rows are
kept after a successful send (they are also the local record of the run) and are
marked with ``sent_at``; ``prune()`` trims acknowledged rows once the file grows
past a bound, so an offline bench degrades by filling a disk slowly rather than
by dropping readings immediately.

Delivery is at-least-once. The endpoint may therefore see a row twice — after a
send whose response was lost, for example. That is deliberate: IndustryFlow's
Spark layer already does idempotent writes (ADR-0006), and duplicating a reading
is recoverable there, whereas dropping one is not recoverable anywhere.

Transport is mTLS via the standard library (``ssl`` + ``urllib``), so the bench
gains no new dependency to run offline.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from industrybench import paths
from industrybench.edge.envelope import Reading

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    sensor_id     TEXT    NOT NULL,
    equipment_id  TEXT    NOT NULL,
    site_id       TEXT    NOT NULL,
    value         REAL    NOT NULL,
    unit          TEXT,
    quality_code  INTEGER NOT NULL DEFAULT 0,
    instrument    TEXT,
    channel       TEXT,
    payload       TEXT    NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    sent_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox(sent_at, id);
"""


class Outbox:
    """A durable local queue of readings, drained to IndustryFlow when possible."""

    #: how many readings go in one HTTP round trip
    BATCH = 100
    #: retry backoff bounds, seconds
    MIN_BACKOFF, MAX_BACKOFF = 2.0, 300.0
    #: keep at most this many acknowledged rows
    KEEP_SENT = 50_000

    def __init__(self, db_path: str | Path | None = None, timeout: float = 10.0):
        self.db_path = Path(db_path) if db_path is not None else paths.outbox_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._conn:
            self._conn.executescript(_SCHEMA)

        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._station = None
        self.last_error: str = ""
        self.last_sent_at: Optional[datetime] = None

    # ------------------------------------------------------------------ write
    def append(self, readings: Sequence[Reading]) -> int:
        """Persist readings. Never raises to the caller: a failure to queue must
        not take down the measurement loop that produced the data."""
        if not readings:
            return 0
        rows = [(
            r.timestamp.isoformat(), r.sensor_id, r.equipment_id, r.site_id,
            _finite(r.value), r.unit, int(r.quality_code), r.instrument, r.channel,
            json.dumps(r.to_payload()),
        ) for r in readings]
        try:
            with self._lock, self._conn:
                self._conn.executemany(
                    "INSERT INTO outbox (timestamp, sensor_id, equipment_id, site_id,"
                    " value, unit, quality_code, instrument, channel, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            self._wake.set()
            return len(rows)
        except sqlite3.Error as e:
            logger.error("Outbox append failed: %s", e)
            return 0
        except Exception:
            logger.exception("Outbox append failed unexpectedly")
            return 0

    # ----------------------------------------------------------------- status
    def stats(self) -> dict:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS total,"
                    " SUM(sent_at IS NULL) AS pending,"
                    " SUM(quality_code != 0) AS suspect FROM outbox")
                row = cur.fetchone()
            return {
                "total": row["total"] or 0,
                "pending": row["pending"] or 0,
                "suspect": row["suspect"] or 0,
                "last_error": self.last_error,
                "last_sent_at": self.last_sent_at,
                "forwarding": self.is_running(),
            }
        except Exception as e:
            return {"total": 0, "pending": 0, "suspect": 0,
                    "last_error": str(e), "last_sent_at": None, "forwarding": False}

    def is_running(self) -> bool:
        return bool(self._worker and self._worker.is_alive())

    # ---------------------------------------------------------------- forward
    def start_forwarding(self, station) -> bool:
        """Begin draining to *station*'s ingest URL. Safe to call repeatedly."""
        if not station.can_forward():
            logger.info("Outbox: forwarding not started — %s",
                        ", ".join(station.missing_for_forwarding()) or "disabled")
            return False
        if self.is_running():
            self._station = station
            return True
        self._station = station
        self._stop.clear()
        self._worker = threading.Thread(target=self._drain_loop,
                                        name="outbox-forwarder", daemon=True)
        self._worker.start()
        logger.info("Outbox: forwarding to %s", station.ingest_url)
        return True

    def stop_forwarding(self, timeout: float = 5.0):
        self._stop.set()
        self._wake.set()
        if self._worker:
            self._worker.join(timeout=timeout)
        self._worker = None

    def _drain_loop(self):
        backoff = self.MIN_BACKOFF
        while not self._stop.is_set():
            # Bound before the try: if _pending() itself raises, the handler
            # must not reference an unassigned name. That would raise inside
            # the handler, escape this loop, and kill the forwarder thread for
            # the rest of the run — losing delivery silently.
            batch = []
            try:
                batch = self._pending(self.BATCH)
                if not batch:
                    self._wake.wait(timeout=5.0)
                    self._wake.clear()
                    continue

                self._send([json.loads(r["payload"]) for r in batch])
                self._mark_sent([r["id"] for r in batch])
                self.last_error = ""
                self.last_sent_at = datetime.now(timezone.utc)
                backoff = self.MIN_BACKOFF
            except Exception as e:
                self.last_error = str(e)
                self._note_failure([r["id"] for r in batch], str(e))
                # An unreachable endpoint is ordinary and stays a one-liner. Once
                # the backoff has grown, the cause is no longer plausibly the
                # network, so carry the traceback — otherwise a bug in _send
                # retries forever as "forward failed" with nothing to debug.
                if backoff >= self.MAX_BACKOFF / 4:
                    logger.exception("Outbox: forwarding still failing")
                else:
                    logger.warning("Outbox: forward failed (%s); retrying in %.0fs",
                                   e, backoff)
                self._stop.wait(timeout=backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)
            except BaseException:
                # The thread is going down (interpreter shutdown, KeyboardInterrupt
                # delivered here). Say so — a forwarder that stops without a trace
                # looks identical to one that has nothing to send.
                logger.exception("Outbox forwarder stopping on a fatal error")
                raise

    def _pending(self, limit: int) -> List[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(
                "SELECT id, payload FROM outbox WHERE sent_at IS NULL"
                " ORDER BY id LIMIT ?", (limit,)))

    def _mark_sent(self, ids: Sequence[int]):
        if not ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.executemany("UPDATE outbox SET sent_at=? WHERE id=?",
                                   [(now, i) for i in ids])

    def _note_failure(self, ids: Sequence[int], error: str):
        if not ids:
            return
        try:
            with self._lock, self._conn:
                self._conn.executemany(
                    "UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?",
                    [(error[:500], i) for i in ids])
        except sqlite3.Error as e:
            # This runs inside the failure handler. Raising here would replace
            # the transport error with a bookkeeping one, so it stays a note.
            logger.debug("Could not record delivery failure: %s", e)

    def _send(self, payloads: List[dict]):
        """POST one batch under mTLS. Raises on any non-2xx or transport error,
        which leaves the rows pending for the next attempt."""
        station = self._station
        if station is None:
            raise RuntimeError("no station configured")

        import ssl
        ctx = ssl.create_default_context(cafile=station.ca_cert or None)
        ctx.load_cert_chain(certfile=station.client_cert, keyfile=station.client_key)

        body = json.dumps(payloads if len(payloads) > 1 else payloads[0]).encode()
        req = urllib.request.Request(
            station.ingest_url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                if not 200 <= resp.status < 300:
                    raise RuntimeError(f"HTTP {resp.status}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"unreachable: {e.reason}") from e

    # ---------------------------------------------------------------- upkeep
    def prune(self, keep_sent: Optional[int] = None) -> int:
        """Drop the oldest acknowledged rows, keeping the most recent ones.
        Pending rows are never pruned — they have not been delivered."""
        keep = self.KEEP_SENT if keep_sent is None else keep_sent
        try:
            with self._lock, self._conn:
                cur = self._conn.execute(
                    "DELETE FROM outbox WHERE sent_at IS NOT NULL AND id NOT IN ("
                    " SELECT id FROM outbox WHERE sent_at IS NOT NULL"
                    " ORDER BY id DESC LIMIT ?)", (keep,))
                return cur.rowcount or 0
        except Exception as e:
            logger.error("Outbox prune failed: %s", e)
            return 0

    def close(self):
        self.stop_forwarding()
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error as e:
            logger.debug("Outbox close: %s", e)


def _finite(v) -> float:
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0.0
    return float(v)
