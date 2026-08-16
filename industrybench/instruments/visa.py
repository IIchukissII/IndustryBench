# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SCPI instruments reached over VISA.

One class, three ways of getting a number out of a channel, chosen by what the
channel configuration says:

===============================  =============================================
``type`` names a temperature     the instrument does the conversion:
and ``configure`` is present     ``CONF:TEMP …`` then ``READ?``
``type`` names a temperature     the instrument reports raw EMF and
and ``configure`` is absent      ``instruments.thermocouple`` converts it here
anything else                    the channel's own query, sent as written
===============================  =============================================

The split exists because a bench does both. A meter with a probe in its
temperature terminals should be asked for degrees. The same meter with bare
thermocouple wire in its DC terminals can only report millivolts, and the
conversion has to happen somewhere — doing it here means any DVM becomes a
thermometer, at the cost of the cold junction being an assumption rather than a
measurement.

pyvisa is imported lazily and its absence is not fatal: a bench with no VISA
backend still runs its simulated devices, which is what a fresh clone has.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from industrybench.instruments.base import ChannelRead, Device
from industrybench.instruments.scpi.temperature import TempFunction

#: ``READ?`` blocks until the reading completes, and a thermocouple's cold
#: junction or an RTD settling can take seconds. The transport timeout is
#: raised to this for the duration of a temperature read, then put back.
TEMPERATURE_TIMEOUT_MS = 30_000

#: SCPI subsystem for the channel types whose range and resolution we set
_SUBSYSTEM = (("curr", ":CURR:DC"), ("volt", ":VOLT:DC"))


def _subsystem_for(measures: str) -> Optional[str]:
    """The subsystem whose range applies to what this channel measures, if any."""
    lowered = measures.lower()
    return next((path for token, path in _SUBSYSTEM if token in lowered), None)


def _is_temperature(measures: str) -> bool:
    return "temp" in measures.strip().lower()


class VisaDevice(Device):
    """A SCPI instrument on a VISA transport."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.address: str = config.get("address", "")
        self.timeout_ms: int = int(config.get("timeout_ms", 5000))
        self.read_terminator: str = config.get("read_terminator", "\n")
        self.write_terminator: str = config.get("write_terminator", "\n")
        self.session = None

    # ------------------------------------------------------------- the session
    def open(self) -> bool:
        try:
            import pyvisa
        except ImportError:
            self.logger.error(
                "%s needs pyvisa, which is not installed. "
                "Run: pip install pyvisa pyvisa-py pyusb", self.name)
            return False

        try:
            self.session = pyvisa.ResourceManager().open_resource(self.address)
            self.session.timeout = self.timeout_ms
            self.session.read_termination = self.read_terminator
            self.session.write_termination = self.write_terminator
            self._identify()
        except Exception as e:
            self.live = False
            self.session = None
            self._explain_open_failure(e)
            return False

        self.live = True
        self.logger.info("%s open at %s", self.name, self.address)
        return True

    def _identify(self) -> None:
        """Prove the link carries traffic before declaring the device open.

        ``*IDN?`` is the usual proof, but it is optional in SCPI and a few
        instruments answer it late or not at all. A device that fails it but
        answers the status register is talking to us, so that is accepted as
        the weaker proof rather than failing an instrument that works.
        """
        try:
            self.session.query("*IDN?")
        except Exception:
            self.session.write("*ESR?")
            self.session.read()

    def _explain_open_failure(self, error: Exception) -> None:
        text = str(error)
        if "VI_ERROR_RSRC_NFOUND" in text:
            self.logger.warning(
                "%s: nothing answers at %s — check the cable and the resource "
                "string", self.name, self.address)
        else:
            self.logger.error("%s: could not open %s: %s",
                              self.name, self.address, error)

    def close(self) -> bool:
        try:
            if self.session is not None:
                self.session.close()
        except Exception as e:
            self.logger.error("%s: could not release %s: %s",
                              self.name, self.address, e)
            return False
        finally:
            self.session = None
            self.live = False
        self.logger.info("%s released", self.name)
        return True

    def is_open(self) -> bool:
        return self.live and self.session is not None

    # ------------------------------------------------------------- the reading
    def read_channel(self, channel: Dict[str, Any]) -> Optional[ChannelRead]:
        if not self.is_open():
            return None

        measures = channel.get("measures", "")
        if _is_temperature(measures):
            if channel.get("configure"):
                return self._read_configured_temperature(channel)
            return self._read_thermocouple(channel)
        return self._read_query(channel, measures)

    def _read_query(self, channel: Dict[str, Any],
                    measures: str) -> Optional[ChannelRead]:
        """Send the channel's own query and parse the answer as a number."""
        query = channel.get("query", "")
        if not query:
            self.logger.error("%s: channel %s has no SCPI query",
                              self.name, self._column(channel))
            return None

        subsystem = _subsystem_for(measures)
        if subsystem:
            self._apply_range(channel, subsystem)
            self._apply_resolution(channel, subsystem)

        return self.reading(channel, float(self.session.query(query)))

    def _apply_range(self, channel: Dict[str, Any], subsystem: str) -> None:
        """Set the measuring range, or hand the choice back to the instrument.

        The console stores ``"AUTO"`` as a range, and ``:RANG`` accepts only a
        number, MIN, MAX or DEF — writing the word would be a syntax error the
        instrument answers by ignoring. Autorange has its own command, so
        "AUTO" is routed to it rather than sent as a value.
        """
        span = channel.get("range")
        if span is None:
            return
        if str(span).strip().upper() in ("AUTO", ""):
            self.session.write(f"{subsystem}:RANG:AUTO ON")
        else:
            self.session.write(f"{subsystem}:RANG {span}")

    def _apply_resolution(self, channel: Dict[str, Any], subsystem: str) -> None:
        resolution = channel.get("resolution")
        if resolution is not None:
            self.session.write(f"{subsystem}:RES {resolution}")

    # --------------------------------------------------- temperature, on-board
    def _read_configured_temperature(
        self, channel: Dict[str, Any]
    ) -> Optional[ChannelRead]:
        """Configure the temperature function, trigger it, and read degrees.

        The sequence is ``*CLS`` (so a stale error register does not colour
        this read), the configure command, ``:TRIG:SOUR IMM``, then ``READ?``.
        The trigger source matters: an instrument left in single or manual
        trigger from front-panel use answers ``READ?`` only when somebody
        presses a button, and the run would sit there until the timeout.
        """
        try:
            function = TempFunction.from_config(channel.get("configure", {}))
        except ValueError as e:
            self.logger.error("%s: %s", self.name, e)
            return None

        window = int(channel.get("timeout_ms") or TEMPERATURE_TIMEOUT_MS)
        restore, self.session.timeout = self.session.timeout, window
        command = function.command()
        try:
            self.session.write("*CLS")
            self.session.write(command)
            self.session.write(":TRIG:SOUR IMM")
            self.logger.debug("%s: %s -> READ? (waiting up to %d ms)",
                              self.name, command, window)
            return self.reading(channel, float(self.session.query("READ?").strip()))
        except ValueError:
            self.logger.error(
                "%s: READ? answered with something that is not a number. "
                "The probe is probably not connected.", self.name)
            return self.unread(channel, "non-numeric answer to READ?")
        except Exception as e:
            self.logger.error(
                "%s: temperature read failed: %s\n"
                "  sent: %s -> :TRIG:SOUR IMM -> READ?  (%d ms)\n"
                "  worth checking: the front-panel trigger is on Auto, the "
                "configured probe family matches what is wired, and the probe "
                "is in the right terminals.",
                self.name, e, command, window)
            return self.unread(channel, str(e))
        finally:
            self.session.timeout = restore

    # ------------------------------------------------- temperature, in software
    def _read_thermocouple(self, channel: Dict[str, Any]) -> Optional[ChannelRead]:
        """Read raw EMF and convert it here.

        The junction type and the assumed terminal temperature come from the
        channel; both have defaults, because a bench measuring a thermocouple
        at all is overwhelmingly measuring a type K one at room temperature.
        """
        from industrybench.instruments.thermocouple import (
            JUNCTION_TYPES, emf_to_celsius,
        )

        junction = str(channel.get("junction", "K")).upper()
        if junction not in JUNCTION_TYPES:
            self.logger.error("%s: junction type %r is not one of %s",
                              self.name, junction, JUNCTION_TYPES)
            return None

        terminals = float(channel.get("terminals_c", 25.0))
        query = channel.get("query") or "MEAS:VOLT:DC?"

        try:
            volts = float(self.session.query(query).strip())
        except ValueError:
            self.logger.error("%s: %s answered with something that is not a "
                              "number", self.name, query)
            return self.unread(channel, "non-numeric voltage")
        except Exception as e:
            self.logger.error("%s: could not read the junction voltage: %s",
                              self.name, e)
            return self.unread(channel, str(e))

        celsius = emf_to_celsius(volts, junction, terminals)
        self.logger.debug("%s: %.4f mV -> %.2f °C (type %s, terminals %.1f °C)",
                          self.name, volts * 1000, celsius, junction, terminals)
        return self.reading(channel, celsius)
