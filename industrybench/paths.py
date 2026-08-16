# SPDX-FileCopyrightText: 2026 The IndustryBench contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the app reads and writes: one workspace root, resolved once.

Every runtime file — configuration, logs, a run's own output, the outbox — lives
under a single directory chosen at startup. Modules ask for it here instead of
carrying their own ``"data/logs"``-style default, because relative defaults are
resolved against the working directory at the moment each one runs. Launch from
the repository root and you get ``./config`` and ``./data``; launch from inside
the package and you get a second, parallel set a few levels down. Both look
correct from the inside, and a bench configured through one is invisible to the
other.

The root is chosen by, in order:

1. ``$INDUSTRYBENCH_HOME``, for running against a fixed workspace regardless of
   where the launcher happens to sit.
2. The working directory — so ``cd`` into an experiment folder and run puts that
   run's data in that folder, which is the point of a bench tool.

with one correction: a working directory *inside* the installed package is taken
to mean the project containing it. Nothing useful is ever written among the
source files, and that mistake is what produces the parallel tree.

The root is never derived from ``__file__`` alone — for a pip-installed copy
that would write into ``site-packages``.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Overrides the working directory when set.
ENV_VAR = "INDUSTRYBENCH_HOME"

#: Files that mark a directory as a project checkout rather than an install.
_PROJECT_MARKERS = (".git", "pyproject.toml", "requirements.txt", "REUSE.toml")

_root: Path | None = None


def _looks_like_project(path: Path) -> bool:
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)


def _detect_root() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    cwd = Path.cwd().resolve()
    package_dir = Path(__file__).resolve().parent

    if cwd == package_dir or package_dir in cwd.parents:
        # Launched from inside the package. Its parent is the right answer only
        # when that parent is a checkout; for an installed copy it is
        # site-packages, which must not be written to.
        parent = package_dir.parent
        if _looks_like_project(parent):
            return parent

    return cwd


def workspace_root() -> Path:
    """The directory holding ``config/`` and ``data/``. Resolved once per run."""
    global _root
    if _root is None:
        _root = _detect_root()
    return _root


def set_workspace_root(path: str | Path | None) -> Path:
    """
    Pin the workspace root, or clear it so the next call re-detects.

    Detection reads the working directory, so it has to happen once and stick:
    a later ``chdir`` must not silently move where an already-running session
    writes its data.
    """
    global _root
    _root = Path(path).expanduser().resolve() if path is not None else None
    return workspace_root()


def config_dir() -> Path:
    return workspace_root() / "config"


def data_dir() -> Path:
    return workspace_root() / "data"


def log_dir() -> Path:
    return data_dir() / "logs"


def run_dir() -> Path:
    """Where a run's own file lands, unless the setup names somewhere else."""
    return data_dir() / "runs"


def outbox_db() -> Path:
    return data_dir() / "outbox.db"


def resolve(path: str | Path) -> Path:
    """
    Anchor a configured path to the workspace.

    Config files hold relative paths like ``data/runs/bench_{timestamp}.csv``.
    Those are relative *to the workspace*, not to wherever the process happens
    to be. An absolute path is a deliberate choice by whoever wrote it and is
    returned unchanged.
    """
    path = Path(path).expanduser()
    return path if path.is_absolute() else workspace_root() / path
