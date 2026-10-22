"""One-shot boot migration: a SCAFFOLD-WRITTEN `max_total_turns: -1` becomes `0` (derive).

MIGRATION(expires=2026-12-31).

F564/D155 gave every run a cumulative cap across its resume windows — `max_total_turns`, derived
as 3x the window when the key says DERIVE (`0`). It shipped INERT on 28 of 33 routines: the
scaffold had written an explicit `max_total_turns: -1` into every config it ever made, an
explicit value always wins (`engine/turnwindow.total_turn_cap`), and `-1` means deliberately
unlimited. So the four runs whose unbounded resumes motivated the whole feature stayed unbounded.

The operator's answer, verbatim (2026-10-22, via R2782/R2790): *"A migration rewrites a
scaffold-written -1 to 0 on all 28 existing configs (recommended — the only option that delivers
half B to the measured routines)"*.

WHAT MUST NOT BE REWRITTEN, and why this reads git rather than the file. `-1` is also a
legitimate operator choice, which `turnwindow` deliberately honours; once rewritten that intent
is unrecoverable from the file. The discriminator is the word *scaffold* in his answer: a line
written by the scaffolding and never touched since has exactly ONE commit in its routine's own
history that changes it. Measured across all 28 on 2026-10-22 with `git log -G` (NOT `-S`, which
counts occurrences and would miss a later change of the VALUE): every one had exactly one such
commit, so nobody has ever revised that line by hand, and the set his answer names is exactly the
set this rewrites. A routine whose history shows a second touch, or whose history cannot be read,
is LEFT ALONE and recorded — an inert backstop on one routine costs less than silently removing
an intended one.

A routine with `max_turns: -1` gains nothing (3x unlimited is not a number), so it is skipped and
named: rewriting it would be a no-op the record would wrongly count as delivered.

Runs once at daemon boot, before anything loads a routine, and records what it did in
`.control/migrations/total-turns.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import yaml

from . import libgit
from .engine.turnwindow import DERIVE
from .ids import now_iso
from .paths import atomic_write_json, atomic_write_yaml, read_yaml

log = logging.getLogger("rsched.migrate_total_turns")

RECORD = Path(".control") / "migrations" / "total-turns.json"

#: The value the scaffold wrote, which makes the run-spanning cap inert.
INERT = -1


def run_migration(server) -> dict:
    """Migrate this instance once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists() or not home.is_dir():
        return {}
    record: dict = {"started": now_iso(), "migrated": {}, "kept": {}, "failed": {}}
    # The two extra homes are read with getattr, not as attributes: a boot migration is handed
    # whatever `load_server_config` returns, and the fresh-container boot test hands it a
    # four-attribute stand-in — so a hard `server.conversations_home` makes the WHOLE DAEMON BOOT
    # die on an AttributeError in a migration that had nothing to migrate. (Measured: it reddened
    # gate 1792680349875750345 on tests/test_bootstrap.py, both parameters. `recover_orphans`
    # learned the same lesson from the scheduler's runner double.) An absent home is an absent
    # home: nothing to walk.
    extra = [(label, getattr(server, f"{label}_home", None))
             for label in ("conversations", "background")]
    for label, root in [("routines", home), *[(a, b) for a, b in extra if b]]:
        for path in sorted(Path(root).glob("*/routine.yaml")) if Path(root).is_dir() else []:
            if path.parent.name.startswith("."):
                continue                        # clarify workspaces and strays stay untouched
            key = f"{label}/{path.parent.name}"
            try:
                _migrate_one(path, key, record, home)
            except (OSError, yaml.YAMLError) as exc:
                record["failed"][key] = f"{type(exc).__name__}: {exc}"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    if record["migrated"] or record["failed"]:
        log.warning("max_total_turns: %d config(s) now derive the run-spanning cap, "
                    "%d kept as-is, %d failed", len(record["migrated"]), len(record["kept"]),
                    len(record["failed"]))
    return record


def _migrate_one(path: Path, key: str, record: dict, home: Path) -> None:
    """Rewrite one file's inert `-1`, or record why it was left alone."""
    raw = read_yaml(path, {})
    budgets = raw.get("budgets") if isinstance(raw, dict) else None
    if not isinstance(budgets, dict) or budgets.get("max_total_turns") != INERT:
        return                                  # nothing inert here: not this migration's business
    if int(budgets.get("max_turns", 0)) < 0:
        record["kept"][key] = "max_turns is unlimited, so the derived cap would be too"
        return
    if (why := _not_scaffold_written(path)) is not None:
        record["kept"][key] = why
        return
    budgets["max_total_turns"] = DERIVE
    atomic_write_yaml(path, raw)
    record["migrated"][key] = int(budgets.get("max_turns", 0)) * 3
    landed = libgit.commit(path.parent,
                           "budgets: max_total_turns derives from max_turns (was an inert -1)",
                           routines_home=home, paths=["routine.yaml"])
    if landed.failed:    # rewritten on disk, but not in its history: a person must look
        record["failed"][key] = f"migrated, but {landed.describe()}"


def _not_scaffold_written(path: Path) -> str | None:
    """None when this line was written once and never touched — else why it is left alone.

    `-G` matches any commit whose diff TOUCHES a line with the key, which `-S` does not: `-S`
    counts occurrences, so an operator who changed `-1` to `-1`-with-a-comment, or a value from
    `900` to `-1`, leaves no `-S` trace at all. Getting this backwards would rewrite exactly the
    deliberate choices the operator's own wording protects.
    """
    try:
        proc = subprocess.run(["git", "-C", str(path.parent), "log", "--format=%h",
                               "-G", "max_total_turns", "--", "routine.yaml"],
                              capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"its history could not be read ({type(exc).__name__}), so the -1 stands"
    if proc.returncode != 0:
        return "its history could not be read (not a git repo?), so the -1 stands"
    touches = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(touches) != 1:
        return (f"{len(touches)} commits touch that line, so the -1 may be a deliberate "
                "uncapping rather than the scaffold's default")
    return None
