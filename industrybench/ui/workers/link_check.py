# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Check the link" — open an address, ask ``*IDN?``, report what came back.

The question behind the button is *is this address the instrument I think it
is*, and the answer is the identification string plus how long it took to
arrive. Both are reported. A slow round trip on a link that works is the usual
early sign of a bus problem that would otherwise turn up much later as a run
that fails intermittently.

It runs on its own thread because opening a VISA resource blocks — on an
address with nothing behind it, for the whole timeout. A window frozen during a
routine "is it plugged in?" check is how an interface teaches people not to
press the button.

Nothing raises out of here. Every outcome, including "pyvisa is not installed",
comes back as a :class:`LinkResult` through ``answered``.

    probe = LinkProbe(address, timeout_ms)
    probe.answered.connect(my_slot)
    probe.start()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkResult:
    """What the attempt found out."""

    reached: bool
    address: str
    #: the ``*IDN?`` reply, when there was one
    identity: Optional[str]
    #: wall clock from open to reply, or to failure
    took_ms: float
    #: what went wrong, in plain words, or None
    trouble: Optional[str]


def _in_plain_words(error: str, address: str, timeout_ms: int) -> str:
    """Turn the two VISA errors with an obvious cause into advice.

    Everything else is passed through as the driver wrote it: a specific
    message from the layer that failed is more use than a guess at what it
    meant.
    """
    if "VI_ERROR_RSRC_NFOUND" in error:
        return (f"Nothing answers at {address!r}.\n"
                "Check the instrument is connected and the address is right.")
    if "VI_ERROR_TMO" in error or "Timeout" in error:
        return (f"No answer within {timeout_ms} ms.\n"
                "Check the instrument is powered on, or allow it longer.")
    return error


class LinkProbe(QThread):
    """One connection attempt, off the GUI thread.

    Emits ``answered(LinkResult)`` exactly once, whether it worked or not.
    """

    answered = pyqtSignal(LinkResult)

    def __init__(self, address: str, timeout_ms: int = 5000, parent=None):
        super().__init__(parent)
        self.address = address
        self.timeout_ms = timeout_ms

    def run(self) -> None:
        self.answered.emit(self.attempt())

    def attempt(self) -> LinkResult:
        """The attempt itself: synchronous, and safe to call from a test."""
        address = self.address.strip()
        if not address:
            return self._failed(address, 0.0, "No address given.")

        try:
            import pyvisa
        except ImportError:
            return self._failed(
                address, 0.0,
                "pyvisa is not installed. Run: pip install pyvisa pyvisa-py pyusb")

        session = None
        started = time.perf_counter()
        try:
            session = pyvisa.ResourceManager().open_resource(address)
            session.timeout = self.timeout_ms
            session.read_termination = "\n"
            session.write_termination = "\n"
            identity = session.query("*IDN?").strip()
            return LinkResult(reached=True, address=address, identity=identity,
                              took_ms=self._elapsed(started), trouble=None)
        except Exception as e:
            return self._failed(
                address, self._elapsed(started),
                _in_plain_words(str(e), address, self.timeout_ms))
        finally:
            self._release(session, address)

    @staticmethod
    def _elapsed(started: float) -> float:
        return (time.perf_counter() - started) * 1000

    @staticmethod
    def _failed(address: str, took_ms: float, trouble: str) -> LinkResult:
        return LinkResult(reached=False, address=address, identity=None,
                          took_ms=took_ms, trouble=trouble)

    @staticmethod
    def _release(session, address: str):
        """Hand the resource back. The verdict is already decided, so a failure
        to close is noted rather than allowed to replace it."""
        if session is None:
            return
        try:
            session.close()
        except Exception as e:
            logger.debug("Releasing %s after the check: %s", address, e)
