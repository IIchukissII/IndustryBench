# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shape of the configuration, declared once.

Configuration arrives as JSON that somebody may have edited by hand. The models
below are the single declaration of what each field means and what it falls
back to. Loading runs the raw dict through them and hands back a plain dict
with every default filled in, so a reader asks for ``channel["unit"]`` instead
of guessing at it.

Declaring a default exactly once is the whole point. Left to each reader, one
driver reaches for ``channel.get("unit", "°C")``, another for
``.get("unit", "")``, a third for ``.get("unit", "celsius")`` — three spellings
of one missing value, and the unit recorded for a channel then depends on which
driver happened to read it.

Two rules govern how strict this is, and they pull against each other:

*Never lose a bench to a schema.* A configuration that fails validation is
still the only record of how somebody wired their instruments. Nothing here
overwrites or discards a file: a bad entry is dropped from the working set for
that session and reported, and the file on disk is left exactly as found.

*Only close a set that is genuinely closed.* A device's ``kind`` is dispatched
by exact match in ``acquisition.runner``, five values, so it is an enum and a
typo is caught at load. A channel's ``measures`` is a label the driver
interprets loosely — ``"temp"`` and ``"temperature"`` both route to the
temperature path — so it stays a free string. Making it an enum would reject
configurations that work.

Unknown keys survive rather than being stripped: drivers read fields specific
to their hardware, and a schema that silently deleted them would break the
instruments it exists to protect.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


#: The unit a temperature channel gets when its configuration does not name
#: one. The symbol, not the word: the store is UTF-8, and this value travels
#: unchanged into file headers and the platform envelope.
DEFAULT_TEMPERATURE_UNIT = "°C"


class DeviceKind(StrEnum):
    """
    The devices ``acquisition.runner`` knows how to build.

    Closed deliberately: the runner dispatches on exact equality, so a value
    outside this set builds nothing at all. Better to say so at load, naming
    the device, than to fail later with an empty bench and no explanation.
    Adding a driver means editing ``runner.DRIVERS`` and this enum together.
    """

    SCPI_VISA = "scpi_visa"
    ONEWIRE = "onewire_probe"
    SIM_PROBE = "sim_probe"
    SIM_VOLTMETER = "sim_voltmeter"
    SIM_AMMETER = "sim_ammeter"


class OutputKind(StrEnum):
    """The file formats ``acquisition.runner`` can build a sink for."""

    DELIMITED = "delimited"
    JSONL = "jsonl"


class _Model(BaseModel):
    """Base for every model: keep unknown keys, coerce what is close."""

    model_config = ConfigDict(
        extra="allow",              # hardware-specific fields must round-trip
        populate_by_name=True,
        str_strip_whitespace=True,  # a trailing space is not a different name
        validate_assignment=False,  # the console edits these live; validate at the edge
    )


def measures_temperature(measures: str) -> bool:
    """
    Whether a channel label names a temperature reading.

    Substring rather than equality, matching how ``instruments.visa``
    dispatches: both ``"temperature"`` and ``"temp"`` take the temperature
    path there, and the unit assigned here has to agree with the driver that
    will do the reading.
    """
    return "temp" in measures.strip().lower()


class Channel(_Model):
    """One channel on one device — a single column in the output."""

    #: what this channel reads: DC Voltage, Temperature, Resistance, …
    measures: str = ""
    #: the output column header. Free to rename; identity is `sensor_id`.
    column: str = ""
    unit: str = ""
    enabled: bool = True

    #: SCPI query, for devices that take one
    query: Optional[str] = None
    #: measuring range in the channel's own unit; None means the device's own
    range: Optional[float] = None
    resolution: Optional[float] = None
    #: durable identity upstream, minted on first run and then fixed
    sensor_id: Optional[str] = None

    @model_validator(mode="after")
    def _unit_for_temperature(self) -> "Channel":
        """
        Give a temperature channel its unit when the configuration omits one.

        Only temperature has a defensible default. A bare number from a
        voltmeter could be volts or millivolts, and inventing one would put a
        wrong unit in a file header and in the platform envelope — so those
        stay empty, which reads as "not stated" rather than as a wrong answer.
        """
        if not self.unit and measures_temperature(self.measures):
            self.unit = DEFAULT_TEMPERATURE_UNIT
        return self


class DeviceConfig(_Model):
    """One instrument — real or simulated — and the channels read from it."""

    name: str
    kind: DeviceKind
    enabled: bool = True
    channels: List[Channel] = Field(default_factory=list)

    # -- VISA transport. Ignored by other kinds, kept so a device can change
    #    kind without losing how it was wired.
    address: str = ""
    timeout_ms: int = 5000
    read_terminator: str = "\n"
    write_terminator: str = "\n"

    #: 1-Wire probe model, e.g. DS18B20
    model: Optional[str] = None


class OutputConfig(_Model):
    """Where a run's scans are written, and how that format is tuned."""

    type: OutputKind = OutputKind.DELIMITED
    enabled: bool = True
    settings: Dict[str, Any] = Field(default_factory=dict)


class RunSettings(_Model):
    """How often the assembler emits a scan, and how many are kept."""

    interval: float = 1.0
    #: scans held for the console; also caps how much of a trace is drawn
    window: int = 1000


class ConsoleSettings(_Model):
    theme: str = "dark"


class BenchConfig(_Model):
    """``bench.json`` — everything that is not a device or a setup."""

    output: OutputConfig = Field(default_factory=OutputConfig)
    run: RunSettings = Field(default_factory=RunSettings)
    console: ConsoleSettings = Field(default_factory=ConsoleSettings)


class DevicesConfig(_Model):
    """``devices.json`` — the bench itself."""

    devices: List[DeviceConfig] = Field(default_factory=list)


class Setup(_Model):
    """A named arrangement: which devices, written where, how often."""

    name: str
    notes: str = ""
    #: device names taking part. Empty means every enabled device.
    devices: List[str] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)
    run: RunSettings = Field(default_factory=RunSettings)


class SetupsConfig(_Model):
    """``setups.json`` — the saved arrangements and which one is chosen."""

    setups: List[Setup] = Field(default_factory=list)
    chosen: str = ""


# ---------------------------------------------------------------------------
# Applying a model to raw configuration
# ---------------------------------------------------------------------------

M = TypeVar("M", bound=BaseModel)


def _plain(model: BaseModel) -> Dict[str, Any]:
    """
    Back to a plain dict, with defaults filled in.

    ``mode="json"`` so enum members come out as the strings the rest of the app
    compares against and the store writes; a bare dump would leave
    ``DeviceKind.SCPI_VISA`` where ``"scpi_visa"`` is expected.

    ``exclude_none`` keeps the saved files readable. Every optional field here
    means "not set" when it is None — no query, autorange, no id yet — and
    ``.get()`` answers None for an absent key just as it does for a null one.
    Writing them out would add a dozen ``null``s per device that say nothing.
    """
    return model.model_dump(mode="json", exclude_none=True)


def checked(model: Type[M], raw: Any, source: str) -> Dict[str, Any]:
    """
    Validate ``raw`` against ``model``, or hand it back untouched.

    Returning the original rather than raising is the point: this runs while
    the app is starting, and a configuration the schema cannot read is still
    the operator's bench. Refusing to start helps nobody; starting with a loud
    log and whatever could be understood does.
    """
    try:
        return _plain(model.model_validate(raw))
    except ValidationError as e:
        logger.error(
            "%s does not match the expected shape; using it unvalidated.\n%s",
            source, _explain(e),
        )
        return raw if isinstance(raw, dict) else {}


def checked_device(raw: Any, source: str) -> Optional[Dict[str, Any]]:
    """
    Validate one device entry, or None if it does not hold together.

    Used when an entry arrives from somewhere other than a file — the console
    building a new device, say. Without it the in-memory list would hold a mix
    of validated entries and whatever the caller passed, and no reader could
    rely on a field being present.
    """
    try:
        return _plain(DeviceConfig.model_validate(raw))
    except ValidationError as e:
        logger.error("Rejected %s:\n%s", source, _explain(e))
        return None


def checked_devices(raw: Any, source: str) -> Dict[str, Any]:
    """
    Validate the device list one entry at a time.

    Per entry, because the whole-file rule is too blunt here: a single mistyped
    ``kind`` would otherwise leave every other device unvalidated. A bad entry
    is dropped for this session and named in the log; the file is not touched,
    so correcting the typo restores it.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("devices"), list):
        return checked(DevicesConfig, raw, source)

    kept: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw["devices"]):
        try:
            kept.append(_plain(DeviceConfig.model_validate(entry)))
        except ValidationError as e:
            logger.error(
                "Ignoring device %s in %s — it will not be measured until the "
                "configuration is corrected.\n%s",
                _name_of(entry, index), source, _explain(e),
            )

    rest = {k: v for k, v in raw.items() if k != "devices"}
    return {**rest, "devices": kept}


def _name_of(entry: Any, index: int) -> str:
    if isinstance(entry, dict) and entry.get("name"):
        return f"'{entry['name']}' (#{index})"
    return f"#{index}"


def _explain(error: ValidationError) -> str:
    """One indented line per problem: where, what was wrong, what arrived."""
    lines = []
    for detail in error.errors():
        where = ".".join(str(p) for p in detail["loc"]) or "(root)"
        lines.append(f"    {where}: {detail['msg']} (received {detail['input']!r})")
    return "\n".join(lines)
