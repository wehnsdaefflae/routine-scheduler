"""Routine LANES — the temporal axis: which routines fire, in what order, on whose clock.

A lane is an ORDERED list of member records plus a mid-chain-failure policy and, optionally, a
CRON SCHEDULE: a scheduled lane auto-arms its sequential chain on the lane's cron (member by
member — daemon/lane_runs.py) and every member's OWN cron is SUPPRESSED while it belongs to a
scheduled lane — one fire path, no double-firing. The routine page's Schedule dropdown shows
such a member as "lane managed". An UNSCHEDULED lane changes nothing about its members' own
schedules.

**A routine belongs to AT MOST ONE lane and that is enforced.** This is the whole reason the
axis exists on its own. Cron exclusivity is a hard fact — a routine in two scheduled lanes
would fire twice — so the temporal axis is the strictest of the three, which is why it may not
share a record with them. One record carrying timing AND config AND a trust boundary AND a
semantic label quantizes the other three to this cardinality: nobody can say "these five
routines share a permission surface" without also saying "and they fire together", so the
shared surface gets copied once per cadence instead. That cost is measured, not hypothetical:
this instance carried four `Instance ·` copies of one byte-identical 294-char config block and
two `Professional ·` copies of another, the missing dimensions hand-encoded in the NAMES.

The three axes are separate objects with different cardinalities:

- **lane** (here) — WHEN and IN WHAT ORDER. At most one per routine. Daemon-owned,
  `<routines_home>/.control/lanes.json`.
- **[domain](domains.py)** — WHAT THEY SHARE: the inherited config block, the shared store,
  and the notes boundary. At most one per routine, named in its OWN routine.yaml.
- **tags** — WHAT IT IS ABOUT. Any number, already on the routine, already "a label, not
  behaviour" (`configflow.py`).

Config clustering and the trust boundary are ONE object rather than two on purpose: they answer
the same question ("which routines are close enough to share?") and have the same cardinality.
Splitting them would dissolve the argument that makes `domainnotes` approval-free — a note
cannot leave the domain because the domain's store is in its members' fs roots and nobody
else's.

A membership record carries the member's slug (a record, not a bare string, so a future
per-member field has a home). A chain fires ONCE: every member in order. A flow with an inbound
and an outbound end BRACKETS the lane (D90, 2026-08-16): a dedicated inbound-router routine
placed first in the order and a dedicated outbound-sender routine placed last — two
single-purpose members instead of one member running twice.

Ownership mirrors rsched.triggers / rsched.schedule_once: a lane is instance-level operator
state that the WEB layer writes and the daemon reads (web RECORDS, daemon FIRES), so it CANNOT
live in a routine's routine.yaml. A DOMAIN is the opposite and lives exactly there: which
routines share a surface is an ordinary per-routine setting, so putting it in the routine's own
file is what makes "at most one" a fact of the file rather than a rule someone has to enforce
across a list.

    <routines_home>/.control/lanes.json

Shape (single document, atomic-written):

    {"default_on_failure": "stop",
     "lanes": [{"id": "lane-1a2b3c4d", "name": "Morning jobs",
                "members": [{"slug": "weight-coach"}, {"slug": "news-digest"}],
                "on_failure": null,          # null = inherit default_on_failure
                "cron": "0 7 * * *",         # "" = unscheduled (fire only when armed)
                "tz": "Europe/Berlin",       # written beside cron by the web layer
                "paused": false,             # true = the cron never auto-arms (whole-lane
                                             # pause; an explicit Run now still fires)
                "created": "2026-07-31T…"}]}

This module owns the shared vocabulary and the file IO. It validates SHAPE only (types, dedup,
the on_failure vocabulary, cron syntax); validating that each member slug names a REAL routine
is the API layer's job (it holds the registry), exactly as api_hooks validates against
registry. One lane document must never be the place a stale reference takes the store down.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from croniter import croniter

from .ids import now_iso
from .paths import atomic_write_json, read_json

# What to do when a member run fails partway through a sequential lane fire (Phase B reads
# this; Phase A only stores it). "stop" = abort the rest of the chain; "continue" = fire the
# remaining members anyway. A lane's own value may be null → inherit the instance default.
ON_FAILURE = ("stop", "continue")
DEFAULT_ON_FAILURE = "stop"

# Sentinel for update(): distinguishes "field not passed" (leave unchanged) from an explicit
# None (inherit the instance default) — a tri-state a plain None default cannot express.
_UNSET = object()


def new_id() -> str:
    """A stable lane handle — server-generated, never client-supplied.

    The prefix is cosmetic — an id is an OPAQUE handle nothing parses. A lane may carry any
    prefix; one id naming both a lane and a domain names two unrelated records.
    """
    return f"lane-{uuid.uuid4().hex[:8]}"


def lanes_file(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "lanes.json"


def load(routines_home: Path) -> dict:
    """The whole store, normalized: {default_on_failure, lanes:[…]}. A missing or corrupt
    file reads as the empty store with the built-in default — never raises.
    """
    raw = read_json(lanes_file(routines_home))
    if not isinstance(raw, dict):
        raw = {}
    default = raw.get("default_on_failure")
    if default not in ON_FAILURE:
        default = DEFAULT_ON_FAILURE
    lanes = [_normalize(rec) for rec in raw.get("lanes") or [] if isinstance(rec, dict)]
    return {"default_on_failure": default, "lanes": lanes}


def _normalize(rec: dict) -> dict:
    on_failure = rec.get("on_failure")
    if on_failure not in ON_FAILURE:
        on_failure = None
    cron = str(rec.get("cron") or "").strip()
    if cron and not croniter.is_valid(cron):
        cron = ""                       # a corrupt row degrades to unscheduled, never raises
    return {
        "id": str(rec.get("id") or ""),
        "name": str(rec.get("name") or ""),
        "members": _clean_members(rec.get("members")),
        "on_failure": on_failure,
        "cron": cron,
        "tz": str(rec.get("tz") or ""),
        "paused": bool(rec.get("paused")),
        "created": str(rec.get("created") or ""),
    }


def _check_cron(cron: str) -> str:
    cron = str(cron or "").strip()
    if cron and not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression {cron!r}")
    return cron


def scheduled_member_slugs(routines_home: Path) -> set[str]:
    """Every routine slug whose OWN cron is suppressed because it belongs to a lane WITH a
    schedule (D71) — the daemon's cron-fire loop and boot catch-up skip these, the week
    endpoint (api_schedule) withholds their fires, and the routine page renders their
    Schedule dropdown as "lane managed".
    """
    return {m for lane in list_lanes(routines_home) if lane["cron"] for m in member_slugs(lane)}


def lane_of(routines_home: Path, slug: str) -> dict | None:
    """The ONE lane this routine belongs to, or None.

    Singular because the cardinality is enforced (`add_members` refuses a slug another lane
    already holds): a routine in two scheduled lanes would fire twice, which is the hard fact
    the whole axis is shaped around.
    """
    return next((lane for lane in list_lanes(routines_home) if slug in member_slugs(lane)), None)


def member_slugs(lane: dict) -> list[str]:
    """The lane's ordered member slugs — for the many consumers that need the fire order
    but not the per-member flags.
    """
    return [m["slug"] for m in lane.get("members") or []]


def _clean_members(members: object) -> list[dict]:
    """Coerce to an ordered, de-duplicated list of member RECORDS {"slug"} (order
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
                out.append({"slug": s})
    return out


def _save(routines_home: Path, data: dict) -> None:
    atomic_write_json(lanes_file(routines_home), data)


def list_lanes(routines_home: Path) -> list[dict]:
    return load(routines_home)["lanes"]


def _claimed_elsewhere(routines_home: Path, members: list[dict], keep: str = "") -> list[str]:
    """Slugs in `members` that another lane (id != `keep`) already holds.

    The cardinality check, in ONE place, because it is the invariant the axis exists for and
    a second copy of it would be a second chance to get it wrong. Returns the offenders so
    the caller can name them: "already in <lane>" is actionable, "invalid" is not.
    """
    wanted = {m["slug"] for m in _clean_members(members)}
    return sorted(s for lane in list_lanes(routines_home) if lane["id"] != keep
                  for s in member_slugs(lane) if s in wanted)


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


def get(routines_home: Path, lane_id: str) -> dict | None:
    for lane in list_lanes(routines_home):
        if lane["id"] == lane_id:
            return lane
    return None


def create(routines_home: Path, *, name: str, members: list[dict] | None = None,
           on_failure: str | None = None, cron: str = "", tz: str = "") -> dict:
    """Create a lane. `name` must be non-empty; `members` is an ordered list of records
    {"slug"} stored in order (deduped by slug); `on_failure` must be in ON_FAILURE
    or None (inherit); `cron` (optional, D71) must be a valid cron expression — "" leaves
    the lane unscheduled. Raises ValueError on a bad value — including a member another lane
    already holds, which is the cardinality the axis is built on.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("lane name is required")
    if on_failure is not None and on_failure not in ON_FAILURE:
        raise ValueError(f"on_failure must be one of {ON_FAILURE} or null, got {on_failure!r}")
    if taken := _claimed_elsewhere(routines_home, members or []):
        raise ValueError("a routine belongs to at most one lane; already claimed: "
                         + ", ".join(taken))
    rec: dict = {
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
    data["lanes"].append(rec)
    _save(routines_home, data)
    return rec


# The patchable lane surface IS this parameter list; an options object would only relocate it
# (the same reason scaffold() takes its fields flat).
def update(routines_home: Path, lane_id: str, *, name: str | None = None,
           members: list[dict] | None = None, on_failure: object = _UNSET,
           cron: str | None = None, tz: str | None = None,
           paused: bool | None = None) -> dict | None:
    """Patch a lane in place (only the fields passed are touched). `members` replaces the
    whole record list ({"slug"} each). `on_failure` is a tri-state: omit it to
    leave unchanged, pass None to inherit the default, pass a value in ON_FAILURE to
    override. `cron` "" clears the schedule (members fire on their own crons again); a
    non-empty value must be valid cron.
    Returns the updated record, or None if no lane has that id. Raises ValueError on a bad
    value.

    A shared config block is a DOMAIN's, named in the routine's own routine.yaml: patching a
    lane moves timing and order, never what a member may do.
    """
    data = load(routines_home)
    for lane in data["lanes"]:
        if lane["id"] != lane_id:
            continue
        if name is not None:
            nm = str(name).strip()
            if not nm:
                raise ValueError("lane name cannot be empty")
            lane["name"] = nm
        if members is not None:
            if taken := _claimed_elsewhere(routines_home, members, keep=lane_id):
                raise ValueError("a routine belongs to at most one lane; already claimed: "
                                 + ", ".join(taken))
            lane["members"] = _clean_members(members)
        if on_failure is not _UNSET:
            if on_failure is not None and on_failure not in ON_FAILURE:
                raise ValueError(
                    f"on_failure must be one of {ON_FAILURE} or null, got {on_failure!r}")
            lane["on_failure"] = on_failure
        if cron is not None:
            lane["cron"] = _check_cron(cron)
        if tz is not None:
            lane["tz"] = str(tz)
        if paused is not None:
            lane["paused"] = bool(paused)
        _save(routines_home, data)
        return lane
    return None


def delete(routines_home: Path, lane_id: str) -> bool:
    """Delete a lane by id. Idempotent; returns True if one was removed.

    Nothing else has to be cleaned up: a lane owns no store and no config, so deleting one
    returns its members to their own crons and changes nothing else about them.
    """
    data = load(routines_home)
    before = len(data["lanes"])
    data["lanes"] = [lane for lane in data["lanes"] if lane["id"] != lane_id]
    if len(data["lanes"]) == before:
        return False
    _save(routines_home, data)
    return True
