"""MIGRATION(expires=2026-12-01): move routine enabled switches into schedule.disabled."""
from __future__ import annotations

import logging
from pathlib import Path

from .paths import atomic_write_yaml, read_yaml

log = logging.getLogger("rsched.migrate_disabled_schedule")


def run(home: Path) -> int:
    """Convert installed routine configs once; explicit disabled always wins."""
    changed = 0
    for path in sorted(home.glob("*/routine.yaml")):
        if path.parent.name.startswith("."):
            continue
        raw = read_yaml(path)
        if not isinstance(raw, dict):
            raise TypeError(f"{path}: expected mapping")
        if "enabled" not in raw:
            continue
        enabled = raw["enabled"]
        if not isinstance(enabled, bool):
            raise TypeError(f"{path}: enabled must be boolean")
        sched = raw.setdefault("schedule", {})
        if not isinstance(sched, dict):
            raise TypeError(f"{path}: schedule must be mapping")
        sched["disabled"] = sched.get("disabled") is True or not enabled
        del raw["enabled"]
        atomic_write_yaml(path, raw)
        log.warning("disabled schedule migration: %s", path.parent.name)
        changed += 1
    return changed
