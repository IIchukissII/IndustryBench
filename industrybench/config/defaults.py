# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a fresh install gets.

The configuration database is the record of truth once it exists, but it has to
be populated from something on the very first launch. These are those seeds:
written to disk when the files are missing, read once, and never consulted
again. They are not runtime defaults — every field's fallback is declared in
``config.schema`` and applied on load.

The bench ships with three simulated devices switched on and three real-hardware
templates switched off. That is deliberate: a clone that starts, draws three
live traces and writes a file before any instrument is plugged in is one you can
evaluate in a minute. Delete them from ``DEFAULT_DEVICES`` if you would rather a
fresh install start empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from industrybench import paths
from industrybench.config.schema import DEFAULT_TEMPERATURE_UNIT

BENCH_FILE = "bench.json"
DEVICES_FILE = "devices.json"
SETUPS_FILE = "setups.json"


DEFAULT_BENCH: Dict[str, Any] = {
    "output": {
        "type": "delimited",
        "enabled": True,
        "settings": {
            "path": "data/runs/bench_{timestamp}.csv",
            "delimiter": ",",
            "header": True,
            "timestamp_column": True,
        },
    },
    "run": {
        "interval": 1.0,
        "window": 1000,
    },
    "console": {
        "theme": "dark",
    },
}


DEFAULT_DEVICES: Dict[str, Any] = {
    "devices": [
        # -- simulated: these work with nothing plugged in --------------------
        {
            "name": "Simulated probe",
            "kind": "sim_probe",
            "enabled": True,
            "channels": [
                {
                    "measures": "temperature",
                    "column": "ambient_c",
                    "unit": DEFAULT_TEMPERATURE_UNIT,
                    "enabled": True,
                }
            ],
        },
        {
            "name": "Simulated voltmeter",
            "kind": "sim_voltmeter",
            "enabled": True,
            "channels": [
                {
                    "measures": "DC Voltage",
                    "column": "supply_v",
                    "unit": "V",
                    "range": 0.1,
                    "enabled": True,
                }
            ],
        },
        {
            "name": "Simulated ammeter",
            "kind": "sim_ammeter",
            "enabled": True,
            "channels": [
                {
                    "measures": "DC Current",
                    "column": "supply_a",
                    "unit": "A",
                    "range": 0.001,
                    "enabled": True,
                }
            ],
        },
        # -- templates for real hardware, switched off ------------------------
        {
            "name": "Bench multimeter",
            "kind": "scpi_visa",
            "enabled": False,
            "address": "USB0::0xVVVV::0xPPPP::SERIAL::INSTR",
            "timeout_ms": 5000,
            "read_terminator": "\n",
            "write_terminator": "\n",
            "channels": [
                {
                    "measures": "DC Voltage",
                    "query": "MEAS:VOLT:DC?",
                    "column": "dvm_v",
                    "unit": "V",
                    "range": 3,
                    "enabled": True,
                }
            ],
        },
        {
            "name": "Bench thermometer",
            "kind": "scpi_visa",
            "enabled": False,
            "address": "TCPIP0::192.0.2.10::INSTR",
            "timeout_ms": 5000,
            "read_terminator": "\n",
            "write_terminator": "\n",
            "channels": [
                {
                    "measures": "temperature",
                    "query": "READ?",
                    "configure": {"probe_type": "TC", "sensor_type": "K"},
                    "column": "probe_c",
                    "unit": DEFAULT_TEMPERATURE_UNIT,
                    "timeout_ms": 30000,
                    "enabled": True,
                }
            ],
        },
        {
            "name": "1-Wire probe",
            "kind": "onewire_probe",
            "enabled": False,
            "model": "DS18B20",
            "channels": [
                {
                    "measures": "temperature",
                    "column": "onewire_c",
                    "unit": DEFAULT_TEMPERATURE_UNIT,
                    "enabled": True,
                }
            ],
        },
    ]
}


#: A setup is a named arrangement: which devices take part, where their scans
#: are written, how often. The seeded one names no devices — an empty list
#: means "every enabled device" — so a fresh install records the simulated
#: channels above without anybody having to choose anything first.
DEFAULT_SETUPS: Dict[str, Any] = {
    "setups": [
        {
            "name": "Whole bench",
            "notes": "Every enabled channel, one scan per second, to a CSV.",
            "devices": [],
            "output": DEFAULT_BENCH["output"],
            "run": {"interval": 1.0},
        }
    ],
    "chosen": "Whole bench",
}


#: filename -> what a fresh install gets. The one place these names are
#: spelled: the store builds its paths from them, the repair tool walks them,
#: and seeding writes them, so a fourth spelling cannot drift out of step.
SEEDS: tuple[tuple[str, Dict[str, Any]], ...] = (
    (BENCH_FILE, DEFAULT_BENCH),
    (DEVICES_FILE, DEFAULT_DEVICES),
    (SETUPS_FILE, DEFAULT_SETUPS),
)

CONFIG_FILES = tuple(name for name, _ in SEEDS)

#: directories the app writes to, under the workspace root
WORKSPACE_DIRS = ("config", "data/logs", "data/runs")


def ensure_directories(root: Path | str | None = None) -> None:
    """Create the directories the app writes to, under the workspace root."""
    root = Path(root) if root is not None else paths.workspace_root()
    for directory in WORKSPACE_DIRS:
        (root / directory).mkdir(parents=True, exist_ok=True)


def seed_config_files(config_dir: Path | str | None = None) -> list[str]:
    """Write any missing seed file. Returns the names written."""
    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for filename, payload in SEEDS:
        path = config_dir / filename
        if not path.exists():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            written.append(filename)
    return written
