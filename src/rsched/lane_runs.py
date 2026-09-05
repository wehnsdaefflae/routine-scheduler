"""Lane runs — the in-flight state of a sequential lane fire (D53 Phase B).

Phase A (`rsched.lanes`) is the lane DEFINITION store. Phase B fires a lane's members
back-to-back: fire a member, wait for it to reach a TERMINAL state, then — depending on its
outcome and the lane's on_failure policy — fire the next. That takes CROSS-TICK progress
state (the daemon tick is 5s but a member run takes minutes), which is what this module
stores, separately from the lane definition it will outlive.

A chain fires each member ONCE, in order; `cursor` indexes into that fire list. A flow with
an inbound and an outbound end BRACKETS the lane (D90): inbound-router member first,
outbound-sender member last.

Ownership mirrors `rsched.triggers` / `rsched.schedule_once`: instance-level operator state
the WEB layer arms and the DAEMON advances, in a dot-dir the registry scan ignores:

    <routines_home>/.control/lane-runs/<lane_id>.json      (≤ one in-flight run per lane)

A lane-run SNAPSHOTS its members + resolved on_failure at ARM time, so editing or deleting
the lane definition mid-chain never changes a run already in flight. One in-flight run per
lane id (the file path IS the lane id) — arming a lane that is already running is refused
by the caller, never coalesced into a second chain. A lane id is OPAQUE: a lane keeps the
id it was created with, so match it against the lane store rather than reading a prefix off
it. Shape (single document, atomic-written):

    {"id": "lr-1a2b3c4d", "lane_id": "…",  # the lane's own id, whatever it is
     "name": "Morning jobs",
     "members": [{"slug": "weight-coach"},                 # ordered member records,
                 {"slug": "news-digest"}],                  # snapshot at arm
     "on_failure": "stop",                          # RESOLVED (override|default) at arm
     "cursor": 0,                                   # index into the members fire list
     "current_run": null,                           # run_id of the member in flight, or null
     "status": "pending",                           # pending | running | done | stopped
     "log": [{"slug","run_id","state","outcome"}],  # per-member results as they finish
     "armed_by": "ui", "created": "…", "ended": null}

These records are EPHEMERAL — one exists only while its chain is in flight and the daemon
drains every run before it restarts — so the directory holds nothing worth carrying across a
release. The domain STORE directory is the opposite case: routines address those paths in
their own memory, so it must never move (`domains.STORES_DIRNAME`).

This module owns the file IO and shape, the DAEMON manager (`daemon/lane_runs.py`) the
advance logic, and the API layer the validation of members against the live registry.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .ids import now_iso
from .lanes import DEFAULT_ON_FAILURE, ON_FAILURE
from .paths import atomic_write_json, read_json


def new_id() -> str:
    """A stable lane-run handle for logs/UI (the FILE is keyed by the lane id, not this)."""
    return f"lr-{uuid.uuid4().hex[:8]}"


def runs_dir(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "lane-runs"


def run_file(routines_home: Path, lane_id: str) -> Path:
    return runs_dir(routines_home) / f"{lane_id}.json"


def read(routines_home: Path, lane_id: str) -> dict | None:
    """The in-flight run for a lane, or None. A corrupt file reads as None."""
    rec = read_json(run_file(routines_home, lane_id))
    return rec if isinstance(rec, dict) else None


def save(routines_home: Path, rec: dict) -> None:
    atomic_write_json(run_file(routines_home, str(rec["lane_id"])), rec)


def remove(routines_home: Path, lane_id: str) -> bool:
    """Delete the in-flight file (chain done/stopped/cancelled). Idempotent."""
    path = run_file(routines_home, lane_id)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def in_flight(routines_home: Path) -> list[dict]:
    """Every in-flight lane-run on disk (unordered). Corrupt files are skipped."""
    d = runs_dir(routines_home)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.json")):
        rec = read_json(path)
        if isinstance(rec, dict) and rec.get("lane_id"):
            out.append(rec)
    return out


def arm(routines_home: Path, lane: dict, *, default_on_failure: str,
        armed_by: str = "ui") -> dict | None:
    """Arm a sequential fire of `lane` (a record from rsched.lanes). Resolves the
    on_failure policy NOW (the lane's override, else the instance default) and snapshots
    the member list, so later edits to the lane never change this run. Returns the new
    in-flight record, or None if the lane is ALREADY in flight (the caller decides how to
    surface that — a lane fires as ONE chain at a time).
    """
    lane_id = str(lane.get("id") or "")
    if not lane_id:
        raise ValueError("lane has no id")
    if run_file(routines_home, lane_id).exists():
        return None  # one in-flight chain per lane
    override = lane.get("on_failure")
    resolved = override if override in ON_FAILURE else (
        default_on_failure if default_on_failure in ON_FAILURE else DEFAULT_ON_FAILURE)
    rec = {
        "id": new_id(),
        "lane_id": lane_id,
        "name": str(lane.get("name") or ""),
        "members": [dict(m) for m in lane.get("members") or []],
        "on_failure": resolved,
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
