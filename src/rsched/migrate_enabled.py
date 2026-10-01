"""One-shot boot migration onto ONE off switch — MIGRATION(expires=2026-11-15).

A routine was switched off two ways: the top-level `enabled: false`, and `schedule.disabled:
true`, which the loader folded into the first and gave the final word. The console wrote the
second (its pause toggle, the "Disabled" cadence, goal retirement — all through the routine
PATCH), creation, patterns and the conversation and background homes wrote the first, and every
reader — the fire table, catch-up, lanes, the runner, the console's own `enabled` — read the
folded field. So a file said one thing in one key and the opposite in the other, and which won
was a loader detail.

The canonical form is the one those readers read: `enabled` (absent = on). This folds each
`schedule.disabled` into it without changing what any routine does — `disabled: true` was off
whatever `enabled` said, so it becomes `enabled: false`; anything else was decided by `enabled`,
which stays as it is (written out as `enabled: true` when the file had none). Every
`routine.yaml` directly under the routine, conversation and background homes is walked, each
change committed in its own repo.

Runs once at daemon boot, before anything loads a routine, and records what it did in
`.control/migrations/enabled.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import libgit
from .ids import now_iso
from .paths import atomic_write_json, atomic_write_yaml, read_yaml

log = logging.getLogger("rsched.migrate_enabled")

RECORD = Path(".control") / "migrations" / "enabled.json"


def run_migration(server) -> dict:
    """Migrate this instance once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists() or not home.is_dir():
        return {}
    record: dict = {"started": now_iso(), "migrated": {}, "failed": {}}
    for label, root in (("routines", home), ("conversations", server.conversations_home),
                        ("background", server.background_home)):
        for path in sorted(Path(root).glob("*/routine.yaml")) if Path(root).is_dir() else []:
            if path.parent.name.startswith("."):
                continue                        # clarify workspaces and strays stay untouched
            key = f"{label}/{path.parent.name}"
            try:
                switch = _fold(path)
            except (OSError, yaml.YAMLError) as exc:
                record["failed"][key] = f"{type(exc).__name__}: {exc}"
                continue
            if switch is _UNTOUCHED:
                continue
            record["migrated"][key] = switch
            landed = libgit.commit(path.parent, "off switch: schedule.disabled folded into "
                                                f"enabled: {switch}",
                                   routines_home=home, paths=["routine.yaml"])
            if landed.failed:   # converted on disk, but not in its history: a person must look
                record["failed"][key] = f"migrated, but {landed.describe()}"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    if record["migrated"] or record["failed"]:
        log.warning("enabled: folded schedule.disabled in %d routine.yaml file(s), %d failed",
                    len(record["migrated"]), len(record["failed"]))
    return record


#: `_fold`'s answer for a file with nothing to fold — distinct from every value `enabled` holds.
_UNTOUCHED = object()


def _fold(path: Path) -> object:
    """Fold one file's `schedule.disabled` into `enabled` and write it back. Returns the value
    `enabled` now holds, or `_UNTOUCHED` when the file had nothing to fold.
    """
    raw = read_yaml(path, {})
    schedule = raw.get("schedule") if isinstance(raw, dict) else None
    if not isinstance(schedule, dict) or "disabled" not in schedule:
        return _UNTOUCHED
    if schedule.pop("disabled") is True:     # the loader's rule: only a literal true was off
        raw["enabled"] = False
    else:
        raw.setdefault("enabled", True)
    atomic_write_yaml(path, raw)
    return raw["enabled"]
