"""Routine groups — the durable store for named, ordered collections of routines (D53).

A group is an ORDERED list of routine slugs plus a mid-chain-failure policy. Phase A (this
module + api_groups + the Groups UI) is the STORE and its CRUD surface only: creating,
naming, ordering and deleting groups, and choosing what happens if a member fails mid-chain.
Phase B (sequential-fire) is a later increment that will READ this store to run a group's
members back-to-back on the daemon tick — nothing here fires anything yet.

Ownership mirrors rsched.triggers / rsched.schedule_once: a group is instance-level operator
state that the WEB layer writes and a future daemon reads, so it CANNOT live in a routine's
routine.yaml (config is the user's, per-routine, never run-written across routines). It lives
in ONE daemon-owned file the registry's dot-dir scan ignores:

    <routines_home>/.control/groups.json

Shape (single document, atomic-written):

    {"default_on_failure": "stop",
     "groups": [{"id": "grp-1a2b3c4d", "name": "Morning jobs",
                 "members": ["weight-coach", "news-digest"],
                 "on_failure": null,          # null = inherit default_on_failure
                 "created": "2026-07-31T…"}]}

This module owns the shared vocabulary and the file IO. It validates SHAPE only (types,
dedup, the on_failure vocabulary); validating that each member slug names a REAL routine is
the API layer's job (it holds the registry), exactly as api_hooks validates against registry.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

# What to do when a member run fails partway through a sequential group fire (Phase B reads
# this; Phase A only stores it). "stop" = abort the rest of the chain; "continue" = fire the
# remaining members anyway. A group's own value may be null → inherit the instance default.
ON_FAILURE = ("stop", "continue")
DEFAULT_ON_FAILURE = "stop"

# Sentinel for update(): distinguishes "field not passed" (leave unchanged) from an explicit
# None (inherit the instance default) — a tri-state a plain None default cannot express.
_UNSET = object()


def new_id() -> str:
    """A stable group handle — server-generated, never client-supplied."""
    return f"grp-{uuid.uuid4().hex[:8]}"


def groups_file(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "groups.json"


def load(routines_home: Path) -> dict:
    """The whole store, normalized: {default_on_failure, groups:[…]}. A missing or corrupt
    file reads as the empty store with the built-in default — never raises.
    """
    raw = read_json(groups_file(routines_home))
    if not isinstance(raw, dict):
        raw = {}
    default = raw.get("default_on_failure")
    if default not in ON_FAILURE:
        default = DEFAULT_ON_FAILURE
    groups = [_normalize(g) for g in raw.get("groups") or [] if isinstance(g, dict)]
    return {"default_on_failure": default, "groups": groups}


def _normalize(g: dict) -> dict:
    on_failure = g.get("on_failure")
    if on_failure not in ON_FAILURE:
        on_failure = None
    return {
        "id": str(g.get("id") or ""),
        "name": str(g.get("name") or ""),
        "members": _clean_members(g.get("members")),
        "on_failure": on_failure,
        "created": str(g.get("created") or ""),
    }


def _clean_members(members: object) -> list[str]:
    """Coerce to an ordered, de-duplicated list of non-empty slug strings (order preserved —
    it is the fire order Phase B will use).
    """
    out: list[str] = []
    if isinstance(members, list):
        for m in members:
            s = str(m or "").strip()
            if s and s not in out:
                out.append(s)
    return out


def _save(routines_home: Path, data: dict) -> None:
    atomic_write_json(groups_file(routines_home), data)


def list_groups(routines_home: Path) -> list[dict]:
    return load(routines_home)["groups"]


def default_on_failure(routines_home: Path) -> str:
    return load(routines_home)["default_on_failure"]


def set_default_on_failure(routines_home: Path, value: str) -> str:
    """Set the instance-wide mid-chain-failure default. Raises ValueError on a bad value."""
    if value not in ON_FAILURE:
        raise ValueError(f"on_failure must be one of {ON_FAILURE}, got {value!r}")
    data = load(routines_home)
    data["default_on_failure"] = value
    _save(routines_home, data)
    return value


def get(routines_home: Path, gid: str) -> dict | None:
    for g in list_groups(routines_home):
        if g["id"] == gid:
            return g
    return None


def create(routines_home: Path, *, name: str, members: list[str] | None = None,
           on_failure: str | None = None) -> dict:
    """Create a group. `name` must be non-empty; `members` is stored in order (deduped);
    `on_failure` must be in ON_FAILURE or None (inherit). Raises ValueError on a bad value.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("group name is required")
    if on_failure is not None and on_failure not in ON_FAILURE:
        raise ValueError(f"on_failure must be one of {ON_FAILURE} or null, got {on_failure!r}")
    rec = {
        "id": new_id(),
        "name": name,
        "members": _clean_members(members),
        "on_failure": on_failure,
        "created": now_iso(),
    }
    data = load(routines_home)
    data["groups"].append(rec)
    _save(routines_home, data)
    return rec


def update(routines_home: Path, gid: str, *, name: str | None = None,
           members: list[str] | None = None, on_failure: object = _UNSET) -> dict | None:
    """Patch a group in place (only the fields passed are touched). `on_failure` is a
    tri-state: omit it to leave unchanged, pass None to inherit the default, pass a value in
    ON_FAILURE to override. Returns the updated record, or None if no group has that id.
    Raises ValueError on a bad value.
    """
    data = load(routines_home)
    for g in data["groups"]:
        if g["id"] != gid:
            continue
        if name is not None:
            nm = str(name).strip()
            if not nm:
                raise ValueError("group name cannot be empty")
            g["name"] = nm
        if members is not None:
            g["members"] = _clean_members(members)
        if on_failure is not _UNSET:
            if on_failure is not None and on_failure not in ON_FAILURE:
                raise ValueError(
                    f"on_failure must be one of {ON_FAILURE} or null, got {on_failure!r}")
            g["on_failure"] = on_failure
        _save(routines_home, data)
        return g
    return None


def delete(routines_home: Path, gid: str) -> bool:
    """Delete a group by id. Idempotent; returns True if one was removed."""
    data = load(routines_home)
    before = len(data["groups"])
    data["groups"] = [g for g in data["groups"] if g["id"] != gid]
    if len(data["groups"]) == before:
        return False
    _save(routines_home, data)
    return True
