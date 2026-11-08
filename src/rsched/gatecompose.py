"""The NEXT FIRE'S GATE — the checks a run writes for the fire after it (docs/run-gates.md).

A routine's own gate (`run_gate.checks` in routine.yaml) is written once, for every fire, by
someone who cannot know what the routine will be doing next week. Measured over the 118 gated
fires on 2026-11-08 it skipped ONE: the checks that were asked answered "work" on every fire
because each watched a level no run drained — a request queue waiting on air dates, models
with no provider to test them on, a phase value the gate's idle list did not know. The run
that just did the work is the one party that knows what would make the NEXT fire worth having,
so it writes that down: `state/next-gate.json`, an ordinary state file —

    {"why": "<one line: what the next run would have to do>",
     "checks": [{"kind": "mail", ...}, {"kind": "dates", ...}]}

— in the same check vocabulary (`gatekit.KINDS`). For the next scheduled fire those checks
REPLACE the routine's: a fire is skipped only when every check answers "no work", so adding
specific checks beside general ones could only make skips rarer. Deleting the file (or an
empty `checks` list) hands the next fire back to the routine's own gate.

The operator switches the whole mechanism off per routine with `run_gate.run_written: false`:
the routine's own checks are then asked on every fire, and no run is asked to write any.

Three things a run cannot change, because a run-written check is a model's guess about where
its next work comes from, and a guess that forgets one source would lose that work silently —
the one failure a gate must not have:

- the BUILT-IN reasons (inbox freight, an answer, a store note, a job's own clock, a run that
  did not finish ok, a changed recipe) are asked before any check, whoever wrote it;
- the routine's own `max_quiet` is the FLOOR: it is always asked, a run may SHORTEN it and
  never lengthen it, and a routine whose gate carries none takes no run-written gate at all;
- `script` is not a run's to name: `scripts/admit.py` is the routine's own predicate.

Anything wrong with the file — it does not parse, a check fails `gatekit.validate`, a path lies
outside the routine's roots, a secret is not granted (the last two judged by the daemon at
preparation, which alone holds the grants) — falls back to the routine's gate and RECORDS why
in that fire's `gate.json` (`source.rejected`), which the next run's digest shows it. It never
fails the fire: the routine's own gate is a sound answer to the same question.

Pure and stdlib-plus-gatekit: imported by the gate's preparation child (`daemon/gate_prepare`),
by the engine's job ledger (`checks_in_force`) and by the digest block (`engine/gate_digest`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import gatekit

#: Where a run writes the next fire's checks, relative to the routine's directory.
FILE = "state/next-gate.json"
#: A gate is a short list of specific questions; one that needs more is a recipe's job.
MAX_CHECKS = 12
WHY_MAX = 300


def floor_days(checks: list[dict]) -> int | None:
    """The routine's own backstop — the fewest `max_quiet` days its gate allows, or None."""
    days = [c["days"] for c in checks if c.get("kind") == "max_quiet"
            and isinstance(c.get("days"), int) and not isinstance(c.get("days"), bool)]
    return min(days) if days else None


def read(routine_dir: Path) -> tuple[dict | None, str]:
    """`(doc, problem)` — `(None, "")` when no run-written gate stands (no file, or an empty
    `checks` list, which is how a run hands the next fire back to the routine's gate).
    """
    path = Path(routine_dir) / FILE
    if not path.is_file():
        return None, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"{FILE} does not parse: {exc}"
    if not isinstance(raw, dict) or not isinstance(raw.get("checks"), list):
        return None, f"{FILE} must be an object with a `checks` list"
    if not raw["checks"]:
        return None, ""
    return raw, ""


def judge(routine_checks: list[dict], doc: dict) -> tuple[list[dict], str]:
    """`(the checks the next fire asks, "")`, or `([], why the routine's gate applies)`."""
    floor = floor_days(routine_checks)
    if floor is None:
        return [], ("the routine's own gate carries no max_quiet backstop, and a run-written "
                    "gate needs one (only the operator sets it)")
    checks = doc["checks"]
    if len(checks) > MAX_CHECKS:
        return [], f"{len(checks)} checks — a run-written gate holds at most {MAX_CHECKS}"
    if not all(isinstance(c, dict) for c in checks):
        return [], "every entry of `checks` must be an object"
    if any(c.get("kind") == "script" for c in checks):
        return [], "`script` is the routine's own predicate (scripts/admit.py), not a run's"
    days = floor
    for c in checks:
        d = c.get("days")
        if c.get("kind") != "max_quiet" or not isinstance(d, int) or isinstance(d, bool):
            continue
        if d > floor:
            return [], (f"max_quiet {d} days is longer than the routine's own {floor} — a run "
                        "may shorten the backstop, never lengthen it")
        days = min(days, d)
    effective = [*(c for c in checks if c.get("kind") != "max_quiet"),
                 {"kind": "max_quiet", "id": "backstop", "days": days}]
    if problems := gatekit.validate(effective):
        return [], "; ".join(p.replace("run_gate.checks", "checks") for p in problems)[:600]
    return effective, ""


def compose(cfg) -> tuple[list[dict], dict]:
    """`(checks, source)` for this fire: the last run's own gate when it stands, else the
    routine's. `source` is what the fire's `gate.json` records — `{"kind": "routine"}`,
    plus `rejected` when a run-written gate was there and could not be used, or
    `{"kind": "run", "why", "written", "checks"}`.
    """
    routine = list(cfg.run_gate.checks)
    if not cfg.run_gate.run_written:
        return routine, {"kind": "routine"}
    doc, problem = read(cfg.dir)
    if doc is not None:
        checks, problem = judge(routine, doc)
        if not problem:
            return checks, {"kind": "run", "why": str(doc.get("why") or "")[:WHY_MAX],
                            "written": _mtime(Path(cfg.dir) / FILE), "checks": checks}
    return routine, ({"kind": "routine", "rejected": problem} if problem
                     else {"kind": "routine"})


def checks_in_force(routine_checks: list[dict], gate: dict | None) -> list[dict]:
    """The checks a fire actually asked, read back from its `gate.json` — what a job ledger
    maps check ids to jobs with (engine/jobops.py). A run-written gate records its own list;
    anything else asked the routine's.
    """
    source = gate.get("source") if isinstance(gate, dict) else None
    if isinstance(source, dict) and source.get("kind") == "run" \
            and isinstance(source.get("checks"), list):
        return [c for c in source["checks"] if isinstance(c, dict)]
    return list(routine_checks)


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
    except OSError:
        return ""
