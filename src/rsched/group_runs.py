"""Group runs — the in-flight state of a sequential group fire (D53 Phase B).

Phase A (`rsched.groups`) is the group DEFINITION store. Phase B fires a group's members
back-to-back: fire a member, wait for it to reach a TERMINAL state, then — depending on its
outcome and the group's on_failure policy — fire the next, and so on. That takes CROSS-TICK
progress state (the daemon tick is 5s but a member run takes minutes), which is what this
module stores, separately from the group definition it will outlive.

A chain fires in TWO PASSES (F292): `phase` starts at "ingest" — every member in order —
and, when any member is flagged `split`, flips once to "outbound" — the split members
again, same order. `cursor` indexes into the CURRENT pass's fire list (the manager derives
it from the member records + phase), so it resets to 0 at the flip.

Ownership mirrors `rsched.triggers` / `rsched.schedule_once`: instance-level operator state
the WEB layer arms and the DAEMON advances, in a dot-dir the registry scan ignores:

    <routines_home>/.control/group-runs/<group_id>.json     (≤ one in-flight run per group)

A group-run SNAPSHOTS its members + resolved on_failure at ARM time, so editing or deleting
the group definition mid-chain never changes a run already in flight. One in-flight run per
group id (the file path is the group id) — arming a group that is already running is refused
by the caller, never coalesced into a second chain. Shape (single document, atomic-written):

    {"id": "gr-1a2b3c4d", "group_id": "grp-…", "name": "Morning jobs",
     "members": [{"slug": "weight-coach", "split": false},  # ordered member records,
                 {"slug": "news-digest", "split": true}],   # snapshot at arm
     "on_failure": "stop",                          # RESOLVED (override|default) at arm
     "phase": "ingest",                             # ingest | outbound (F292)
     "cursor": 0,                                   # index into the CURRENT pass's fire list
     "current_run": null,                           # run_id of the member in flight, or null
     "status": "pending",                           # pending | running | done | stopped
     "log": [{"slug","run_id","state","outcome","phase"}],  # per-member results as they finish
     "armed_by": "ui", "created": "…", "ended": null}

This module owns the file IO and shape only; the DAEMON manager (`daemon/group_runs.py`)
owns the advance logic, and the API layer owns validating members against the live registry.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .groups import DEFAULT_ON_FAILURE, ON_FAILURE
from .ids import now_iso
from .paths import atomic_write_json, read_json


def new_id() -> str:
    """A stable group-run handle for logs/UI (the FILE is keyed by group_id, not this)."""
    return f"gr-{uuid.uuid4().hex[:8]}"


def runs_dir(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "group-runs"


def run_file(routines_home: Path, group_id: str) -> Path:
    return runs_dir(routines_home) / f"{group_id}.json"


def read(routines_home: Path, group_id: str) -> dict | None:
    """The in-flight run for a group, or None. A corrupt file reads as None."""
    rec = read_json(run_file(routines_home, group_id))
    return rec if isinstance(rec, dict) else None


def save(routines_home: Path, rec: dict) -> None:
    atomic_write_json(run_file(routines_home, str(rec["group_id"])), rec)


def remove(routines_home: Path, group_id: str) -> bool:
    """Delete the in-flight file (chain done/stopped/cancelled). Idempotent."""
    path = run_file(routines_home, group_id)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def in_flight(routines_home: Path) -> list[dict]:
    """Every in-flight group-run on disk (unordered). Corrupt files are skipped."""
    d = runs_dir(routines_home)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.json")):
        rec = read_json(path)
        if isinstance(rec, dict) and rec.get("group_id"):
            out.append(rec)
    return out


def arm(routines_home: Path, group: dict, *, default_on_failure: str,
        armed_by: str = "ui") -> dict | None:
    """Arm a sequential fire of `group` (a record from rsched.groups). Resolves the
    on_failure policy NOW (the group's override, else the instance default) and snapshots
    the member list, so later edits to the group never change this run. Returns the new
    in-flight record, or None if the group is ALREADY in flight (the caller decides how to
    surface that — a group fires as ONE chain at a time).
    """
    group_id = str(group.get("id") or "")
    if not group_id:
        raise ValueError("group has no id")
    if run_file(routines_home, group_id).exists():
        return None  # one in-flight chain per group
    override = group.get("on_failure")
    resolved = override if override in ON_FAILURE else (
        default_on_failure if default_on_failure in ON_FAILURE else DEFAULT_ON_FAILURE)
    rec = {
        "id": new_id(),
        "group_id": group_id,
        "name": str(group.get("name") or ""),
        "members": [dict(m) for m in group.get("members") or []],
        "on_failure": resolved,
        "phase": "ingest",
        "cursor": 0,
        "current_run": None,
        "status": "pending",
        "log": [],
        "armed_by": armed_by,
        "created": now_iso(),
        "ended": None,
    }
    save(routines_home, rec)
    return rec
