# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
1-Wire probes (DS18B20 and relatives), through ``w1thermsensor``.

Linux only, and in practice a Raspberry Pi: the library reads the kernel's
1-Wire bus through ``/sys/bus/w1``, which no other platform provides. The
import is therefore deferred and its failure is expected rather than
exceptional — a Windows bench simply has no 1-Wire devices, and says so once.

The probe reports Celsius and nothing else, so the channel's unit is a request
for conversion rather than a label. It is matched case-insensitively but
reported back exactly as configured: the unit ends up in a file header and in
the platform envelope, where a helpfully-lowercased ``°c`` is wrong on sight.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from industrybench.config.schema import DEFAULT_TEMPERATURE_UNIT
from industrybench.instruments.base import ChannelRead, Device

#: what the probe's Celsius has to become for a channel asking for something
#: else. Anything not named here is taken as a label for Celsius itself.
FROM_CELSIUS = {
    "fahrenheit": lambda c: c * 9 / 5 + 32,
    "f": lambda c: c * 9 / 5 + 32,
    "kelvin": lambda c: c + 273.15,
    "k": lambda c: c + 273.15,
}


class OneWireProbe(Device):
    """A 1-Wire temperature probe on the local bus."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.model: str = config.get("model", "DS18B20")
        self.probe = None

    def open(self) -> bool:
        try:
            from w1thermsensor import W1ThermSensor
        except ImportError:
            self.logger.error(
                "%s needs w1thermsensor, which is Linux-only and not installed",
                self.name)
            return False
        except Exception as e:
            self.logger.error("%s: w1thermsensor will not load: %s", self.name, e)
            return False

        try:
            self.probe = W1ThermSensor()
        except Exception as e:
            self.live = False
            self.logger.error("%s: no 1-Wire probe on the bus: %s", self.name, e)
            return False

        self.live = True
        self.logger.info("%s open (%s)", self.name, self.model)
        return True

    def close(self) -> bool:
        # Nothing to release: the library reads a sysfs file per call and holds
        # no handle of its own.
        self.probe = None
        self.live = False
        self.logger.info("%s released", self.name)
        return True

    def is_open(self) -> bool:
        return self.live and self.probe is not None

    def read_channel(self, channel: Dict[str, Any]) -> Optional[ChannelRead]:
        if not self.is_open():
            return None

        celsius = self.probe.get_temperature()
        unit = str(channel.get("unit", DEFAULT_TEMPERATURE_UNIT))
        convert = FROM_CELSIUS.get(unit.strip().lower())
        return self.reading(channel, convert(celsius) if convert else celsius)
