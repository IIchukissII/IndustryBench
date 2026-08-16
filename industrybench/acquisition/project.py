# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Projects: what is under test, and which devices are watching it.

A project exists for one reason — to answer *what are these readings about*.
The instrument is the means, never the subject: the same multimeter serves a
pump motor this week and a battery pack next, and readings from the two must
not land on one identity upstream. So ``equipment_id`` belongs here, on the
project, and not on the device.

That id is minted once and then fixed, exactly like a channel's ``sensor_id``
(see ``edge.identity``). It is the platform's handle on everything this project
has ever recorded, so regenerating it would orphan the history rather than
rename it. A stored id that is not a UUID is replaced, because the platform
would reject it anyway, but a valid one is never touched.

A run with no project still records locally and still queues nothing: without a
subject there is nothing to attribute a reading to, and a reading attributed to
the wrong equipment is worse than one that never arrives.

Projects live in the configuration database. This module is the shape of one
and the cache in front of the store — persistence itself is ``config.store``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _uuid_or_new(value: Any) -> str:
    """Keep a stored UUID, replace anything that is not one."""
    text = str(value or "").strip()
    try:
        uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid4())
    return text


def _when(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return datetime.now()


@dataclass
class Project:
    """One subject under test, and the devices trained on it."""

    name: str = "New project"
    notes: str = ""
    #: names of the devices taking part; empty means every enabled one
    devices: List[str] = field(default_factory=list)
    #: the platform's handle on this subject — minted once, then fixed
    equipment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    equipment_name: str = ""
    started: datetime = field(default_factory=datetime.now)
    touched: datetime = field(default_factory=datetime.now)

    def stored(self) -> Dict[str, Any]:
        """The form written to the configuration database."""
        return {
            "name": self.name,
            "notes": self.notes,
            "devices": list(self.devices),
            "equipment_id": self.equipment_id,
            "equipment_name": self.equipment_name,
            "started": self.started.isoformat(),
            "touched": self.touched.isoformat(),
        }

    @classmethod
    def restore(cls, raw: Dict[str, Any]) -> "Project":
        raw = raw or {}
        return cls(
            name=raw.get("name") or "Unnamed project",
            notes=raw.get("notes", ""),
            devices=list(raw.get("devices") or []),
            equipment_id=_uuid_or_new(raw.get("equipment_id")),
            equipment_name=raw.get("equipment_name", ""),
            started=_when(raw.get("started")),
            touched=_when(raw.get("touched")),
        )

    @property
    def subject(self) -> str:
        """What to call the equipment on screen."""
        return self.equipment_name or self.name


class ProjectShelf:
    """The saved projects, and which one the next run is about."""

    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self._by_name: Dict[str, Project] = {}
        self._current: Optional[Project] = None
        self._load()

    def _load(self) -> None:
        try:
            for raw in self.config.all_projects():
                project = Project.restore(raw)
                self._by_name[project.name] = project
        except Exception:
            # A project that will not load must not stop a bench from starting;
            # the run simply has no subject and forwards nothing.
            self.logger.exception("Could not read the saved projects")

    # ------------------------------------------------------------------ browsing
    def names(self) -> List[str]:
        return list(self._by_name)

    def get(self, name: str) -> Optional[Project]:
        """One project by name, from the cache or from the store."""
        if name in self._by_name:
            return self._by_name[name]

        raw = self.config.read_project(name)
        if not raw:
            return None
        project = Project.restore(raw)
        self._by_name[name] = project
        return project

    # -------------------------------------------------------------------- edits
    def create(self, name: str, notes: str = "") -> Project:
        """Start a new project. Raises ValueError if the name is taken."""
        name = name.strip()
        if not name:
            raise ValueError("A project needs a name")
        if name in self._by_name:
            raise ValueError(f"There is already a project called {name!r}")

        project = Project(name=name, notes=notes)
        self._by_name[name] = project
        self.save(project)
        self.logger.info("Project %r created for equipment %s",
                         name, project.equipment_id)
        return project

    def save(self, project: Project) -> bool:
        project.touched = datetime.now()
        try:
            return bool(self.config.put_project(project.stored()))
        except Exception:
            self.logger.exception("Could not save project %r", project.name)
            return False

    def drop(self, name: str) -> bool:
        """Forget a project. The readings it produced are already upstream."""
        self._by_name.pop(name, None)
        if self._current is not None and self._current.name == name:
            self._current = None
        try:
            return bool(self.config.drop_project(name))
        except Exception:
            self.logger.exception("Could not delete project %r", name)
            return False

    # ------------------------------------------------------------------- current
    def choose(self, name: str) -> bool:
        project = self.get(name)
        if project is None:
            return False
        self._current = project
        self.logger.info("Measuring project %r", name)
        return True

    def current(self) -> Optional[Project]:
        """The project this run is about.

        With exactly one project saved and none chosen, that one is it: a bench
        with a single project has no ambiguity to resolve, and making the
        operator pick it every session buys nothing.
        """
        if self._current is not None:
            return self._current
        if len(self._by_name) == 1:
            return next(iter(self._by_name.values()))
        return None

    # -------------------------------------------------------------------- devices
    def columns_of(self, name: str) -> List[str]:
        """Every output column a project's devices would produce."""
        project = self.get(name)
        if project is None:
            return []

        taking_part = set(project.devices)
        columns = []
        for device in self.config.devices.get("devices", []):
            if device.get("name") not in taking_part or not device.get("enabled", True):
                continue
            for channel in device.get("channels", []) or []:
                if channel.get("enabled", True) and channel.get("column"):
                    columns.append(channel["column"])
        return columns
