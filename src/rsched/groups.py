"""Routine groups — the durable store for named, ordered collections of routines (D53).

A group is an ORDERED list of member records plus a mid-chain-failure policy, and optionally
a CRON SCHEDULE (D71): a scheduled group auto-arms its sequential chain on the group's cron
(the chain runs member by member — daemon/group_runs.py), and every member's OWN cron is
SUPPRESSED while it belongs to a scheduled group — one fire path, no double-firing. The
routine page's Schedule dropdown shows such a member as "group managed". An UNSCHEDULED
group changes nothing about its members' own schedules.

A membership record carries a per-member `split` flag (F292): a chain fires in TWO passes —
an ingest pass over every member in order, then an outbound pass over the SPLIT members in
the same order. A split member therefore runs once per pass, told which half it is in via a
run-scoped `phase=ingest|outbound` boot param its recipe branches on (ingest/process and
stage state, or read the staged state and communicate); a non-split member runs once, in the
ingest pass, with no param. A group with no split members chains once, exactly as before.

Ownership mirrors rsched.triggers / rsched.schedule_once: a group is instance-level operator
state that the WEB layer writes and the daemon reads (web RECORDS, daemon FIRES), so it
CANNOT live in a routine's routine.yaml (config is the user's, per-routine, never
run-written across routines). It lives in ONE daemon-owned file the registry's dot-dir scan
ignores:

    <routines_home>/.control/groups.json

Shape (single document, atomic-written):

    {"default_on_failure": "stop",
     "groups": [{"id": "grp-1a2b3c4d", "name": "Morning jobs",
                 "members": [{"slug": "weight-coach", "split": false},
                             {"slug": "news-digest", "split": true}],
                 "on_failure": null,          # null = inherit default_on_failure
                 "cron": "0 7 * * *",         # "" = unscheduled (fire only when armed)
                 "tz": "Europe/Berlin",       # written beside cron by the web layer
                 "paused": false,             # true = the cron never auto-arms (whole-group
                                              # pause; an explicit Run now still fires)
                 "created": "2026-07-31T…"}]}

This module owns the shared vocabulary and the file IO. It validates SHAPE only (types,
dedup, the on_failure vocabulary, cron syntax); validating that each member slug names a
REAL routine is the API layer's job (it holds the registry), exactly as api_hooks validates
against registry.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from croniter import croniter

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


# ---- the shared store (D67, option B-i) --------------------------------------------------
#
# Every run of a grouped routine gets its group's store dir injected into its fs read+write
# roots at boot (engine/runtime seeds RunContext.group_store_roots) — an INJECTED FS ROOT,
# not a new action kind: the normal file actions and the util sandbox already honor the
# effective roots. Writers are whole-file atomic (the engine's write path), and collisions
# are last-write-wins PER FILE — concurrent members should write per-routine filenames
# (`<slug>-<topic>.md`) and treat shared files as read-mostly. The dir is created lazily at
# run boot; it is run data under .control/, not config — engine-side creation is fine.

STORES_DIRNAME = "group-stores"


def store_dir(routines_home: Path, gid: str) -> Path:
    return Path(routines_home) / ".control" / STORES_DIRNAME / gid


def member_store_roots(routines_home: Path, slug: str, *, create: bool = False) -> list[Path]:
    """The shared-store dirs for every group `slug` belongs to (usually 0 or 1). With
    `create`, each is made on the spot — the boot-time caller's job, so the root a run is
    told about always exists.
    """
    out: list[Path] = []
    for g in list_groups(routines_home):
        if slug in member_slugs(g):
            d = store_dir(routines_home, g["id"])
            if create:
                d.mkdir(parents=True, exist_ok=True)
            out.append(d)
    return out


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
    cron = str(g.get("cron") or "").strip()
    if cron and not croniter.is_valid(cron):
        cron = ""                       # a corrupt row degrades to unscheduled, never raises
    return {
        "id": str(g.get("id") or ""),
        "name": str(g.get("name") or ""),
        "members": _clean_members(g.get("members")),
        "on_failure": on_failure,
        "cron": cron,
        "tz": str(g.get("tz") or ""),
        "paused": bool(g.get("paused")),
        "created": str(g.get("created") or ""),
    }


def _check_cron(cron: str) -> str:
    cron = str(cron or "").strip()
    if cron and not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression {cron!r}")
    return cron


def scheduled_member_slugs(routines_home: Path) -> set[str]:
    """Every routine slug whose OWN cron is suppressed because it belongs to a group WITH a
    schedule (D71) — the daemon's cron-fire loop and boot catch-up skip these, and the
    routine page renders their Schedule dropdown as "group managed".
    """
    return {m for g in list_groups(routines_home) if g["cron"] for m in member_slugs(g)}


def member_slugs(group: dict) -> list[str]:
    """The group's ordered member slugs — for the many consumers that need the fire order
    but not the per-member flags.
    """
    return [m["slug"] for m in group.get("members") or []]


def split_slugs(group: dict) -> list[str]:
    """The ordered subset of members flagged `split` (F292) — the outbound pass's fire list."""
    return [m["slug"] for m in group.get("members") or [] if m.get("split")]


def _clean_members(members: object) -> list[dict]:
    """Coerce to an ordered, de-duplicated list of member RECORDS {"slug", "split"} (order
    preserved — it is the fire order a chain uses; dedup is by slug, first record wins).
    Junk entries — non-dicts, blank slugs — are dropped, never raised on.
    """
    out: list[dict] = []
    seen: set[str] = set()
    if isinstance(members, list):
        for m in members:
            if not isinstance(m, dict):
                continue
            s = str(m.get("slug") or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append({"slug": s, "split": bool(m.get("split"))})
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


def create(routines_home: Path, *, name: str, members: list[dict] | None = None,
           on_failure: str | None = None, cron: str = "", tz: str = "") -> dict:
    """Create a group. `name` must be non-empty; `members` is an ordered list of records
    {"slug", "split"} stored in order (deduped by slug); `on_failure` must be in ON_FAILURE
    or None (inherit); `cron` (optional, D71) must be a valid cron expression — "" leaves
    the group unscheduled. Raises ValueError on a bad value.
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
        "cron": _check_cron(cron),
        "tz": str(tz or ""),
        "paused": False,
        "created": now_iso(),
    }
    data = load(routines_home)
    data["groups"].append(rec)
    _save(routines_home, data)
    return rec


def update(routines_home: Path, gid: str, *, name: str | None = None,
           members: list[dict] | None = None, on_failure: object = _UNSET,
           cron: str | None = None, tz: str | None = None,
           paused: bool | None = None) -> dict | None:
    """Patch a group in place (only the fields passed are touched). `members` replaces the
    whole record list ({"slug", "split"} each). `on_failure` is a tri-state: omit it to
    leave unchanged, pass None to inherit the default, pass a value in ON_FAILURE to
    override. `cron` "" clears the schedule (members fire on their own crons again); a
    non-empty value must be valid cron. Returns the updated record, or None if no group has
    that id. Raises ValueError on a bad value.
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
        if cron is not None:
            g["cron"] = _check_cron(cron)
        if tz is not None:
            g["tz"] = str(tz)
        if paused is not None:
            g["paused"] = bool(paused)
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
