"""Routine-lanes CRUD (D53): the Routines page's lane-surface API over the
instance-level `.control/lanes.json` store (rsched.lanes). (D80: the routines page's lane rows
are the one management surface — there is no separate lanes subpage.)

A lane is an ORDERED list of member records — {"slug"} — plus a mid-chain-failure policy and,
optionally, a cron schedule (D71) that auto-arms the chain — saved here as a friendly spec
converted to cron + the server's tz, exactly like a routine's schedule. The WEB layer only
RECORDS it; the daemon fires (the 0.62.0 split). While a lane has a schedule, its members' own
crons are suppressed by the daemon and their Schedule dropdowns read "lane managed". Every
member slug the caller ADDS is validated against the live registry here (the store validates
shape only), so a lane can never be given a routine that does not exist. A slug it already
holds is exempt: routines are deleted out of band, so a stale member must not lock the whole
lane against every further edit (F442). `rsched validate` names the stale ones.

**This surface carries no shared config** and nothing in this file validates one. A lane
decides WHEN routines fire and owns nothing else; the shared config block, the shared store and
the notes boundary belong to the DOMAIN (web/api_domains.py), which a routine names in its own
routine.yaml — and so do the helpers that validate that block. Keeping them apart is what makes
deleting a lane a pure timing change: its members return to their own crons and nothing about
their permissions moves (docs/lanes-domains.md).

`router` rides the normal authed include in app.py like every other api_* module.
"""

from __future__ import annotations

from collections.abc import Collection

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import lane_runs, lanes, schedule
from .routines_common import _catalog, _state

router = APIRouter(tags=["lanes"])


def _routines_home(request: Request):
    return _state(request).server.routines_home


class MemberSpec(BaseModel):
    """One membership record: the routine's slug."""

    slug: str = Field(min_length=1)


def _validate_members(request: Request, members: list[MemberSpec] | None,
                      *, already: Collection[str] = ()) -> list[dict]:
    """Every member the caller ADDS must name a real routine (the store keeps
    shape/order/dedup; existence is ours). A lane member that is a template or a disabled
    routine is allowed — only a NON-EXISTENT slug is rejected, with the offending slug named
    so the UI can point at it.

    `already` — the slugs the lane holds TODAY — is exempt, an exemption that is the whole
    of F442. Routines are deleted out of band (there is no delete endpoint for a cascade to
    hang off), so a lane can end up naming a slug that no longer resolves; validating the
    whole submitted list would refuse every edit to that lane, because both the routine page
    and the dashboard send the members they are KEEPING alongside the one they are changing.
    Joining a lane must not require repairing it first. The stale slug is surfaced instead,
    where a human can act on it: `rsched validate` names it as an instance problem.
    """
    if not members:
        return []
    known = set(_catalog(request).keys()) | set(already)
    unknown = [m.slug for m in members if m.slug not in known]
    if unknown:
        raise HTTPException(400, f"unknown routine(s): {', '.join(sorted(unknown))}")
    return [{"slug": m.slug} for m in members]


class LaneCreate(BaseModel):
    name: str = Field(min_length=1)
    members: list[MemberSpec] = Field(default_factory=list)
    on_failure: str | None = None
    # D71: {"friendly": {…}} — the same spec shape the routine schedule editor sends;
    # cron is built server-side and the server tz recorded beside it.
    schedule: dict | None = None


class LanePatch(BaseModel):
    # forbid unknown keys, like RoutinePatch: a silently-dropped stray reads as "saved"
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    members: list[MemberSpec] | None = None
    # Tri-state on_failure is expressed with a separate flag so JSON null (inherit) is
    # distinguishable from "field omitted" (leave unchanged) — Pydantic collapses both to None.
    on_failure: str | None = None
    set_on_failure: bool = False
    schedule: dict | None = None
    # Whole-lane pause: true stops the lane's cron from auto-arming its chain (members
    # stay lane-managed, so NOTHING in the lane fires on a schedule); an explicit
    # "Run now" still works. None = leave unchanged.
    paused: bool | None = None


def _schedule_to_cron(spec: dict | None) -> tuple[str, str] | None:
    """The body's schedule field → (cron, tz), or None when the field was omitted. A
    friendly 'manual' spec yields ("", "") — the lane becomes unscheduled and its
    members' own crons fire again. Raises HTTPException(400) on a bad spec.
    """
    if spec is None:
        return None
    try:
        cron = schedule.friendly_to_cron(spec.get("friendly") or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"bad schedule: {exc}") from exc
    return (cron, schedule.server_tz() if cron else "")


class DefaultBody(BaseModel):
    default_on_failure: str


@router.get("/lanes")
def list_lanes_route(request: Request) -> dict:
    """The routines page's lane payload: the instance default + every lane.
    `known_routines` is the ordered slug/name list the lane-member picker offers.
    """
    home = _routines_home(request)
    catalog = _catalog(request)
    known = [{"slug": s, "name": info.cfg.name or s} for s, info in sorted(catalog.items())]
    # in-flight sequential fires (Phase B), keyed by lane id, so the UI can show a running
    # chain's progress and refuse a duplicate "Run now" (a chain fires each member ONCE, in
    # order — there is no per-pass phase to report — D90)
    in_flight = {str(r["lane_id"]): {"cursor": r.get("cursor", 0), "status": r.get("status"),
                                     "members": r.get("members", []), "log": r.get("log", [])}
                 for r in lane_runs.in_flight(home)}
    # each lane rides out with its schedule prefill (the editor speaks friendly specs)
    # plus the human sentence, so list surfaces (R313: the routines overview) can show a
    # member's REAL schedule — the lane's — without a client-side cron parser
    recs = [{**lane, "schedule_friendly": schedule.cron_to_friendly(lane["cron"]),
             "schedule_desc": schedule.describe(lane["cron"]) if lane["cron"] else "",
             }
            for lane in lanes.list_lanes(home)]
    return {"default_on_failure": lanes.default_on_failure(home),
            "on_failure_vocab": list(lanes.ON_FAILURE),
            "lanes": recs,
            "in_flight": in_flight,
            "known_routines": known,
            "server_tz": schedule.server_tz()}


@router.put("/lanes/default")
def set_default(request: Request, body: DefaultBody) -> dict:
    try:
        value = lanes.set_default_on_failure(_routines_home(request), body.default_on_failure)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "default_on_failure": value}


def _rescan(request: Request) -> None:
    """A schedule change must reach the daemon's fire table (and the member-suppression
    set) now, not at the next periodic rescan — mirroring the routine-schedule save.
    """
    try:
        request.app.state.scheduler.rescan()
    except AttributeError:
        pass   # test apps without a scheduler — the store on disk is already right


@router.post("/lanes")
def create_lane(request: Request, body: LaneCreate) -> dict:
    members = _validate_members(request, body.members)
    sched = _schedule_to_cron(body.schedule)
    try:
        rec = lanes.create(_routines_home(request), name=body.name, members=members,
                           on_failure=body.on_failure,
                           cron=sched[0] if sched else "", tz=sched[1] if sched else "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if sched:
        _rescan(request)
    return {"ok": True, "lane": rec}


@router.patch("/lanes/{lane_id}")
def update_lane(request: Request, lane_id: str, body: LanePatch) -> dict:
    current = lanes.get(_routines_home(request), lane_id)
    members = (_validate_members(request, body.members,
                                 already=lanes.member_slugs(current or {}))
               if body.members is not None else None)
    on_failure = body.on_failure if body.set_on_failure else lanes._UNSET
    sched = _schedule_to_cron(body.schedule)
    try:
        rec = lanes.update(_routines_home(request), lane_id, name=body.name,
                           members=members, on_failure=on_failure,
                           cron=sched[0] if sched else None,
                           tz=sched[1] if sched else None,
                           paused=body.paused)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rec is None:
        raise HTTPException(404, f"no lane {lane_id!r}")
    if sched is not None or members is not None or body.paused is not None:
        _rescan(request)   # membership + pause changes move the fire/suppression tables too
    return {"ok": True, "lane": rec}


@router.delete("/lanes/{lane_id}")
def delete_lane(request: Request, lane_id: str) -> dict:
    if not lanes.delete(_routines_home(request), lane_id):
        raise HTTPException(404, f"no lane {lane_id!r}")
    return {"ok": True}


@router.post("/lanes/{lane_id}/run")
def run_lane(request: Request, lane_id: str) -> dict:
    """Arm a sequential fire of lane `lane_id` (D53 Phase B): the daemon's LaneRunManager
    picks it up on the next tick and fires the members in order. The on_failure policy is
    resolved NOW (the lane's override, else the instance default) and the member list is
    snapshotted, so later edits to the lane never change this run. 404 if the lane is unknown;
    409 if a chain for it is already in flight (a lane fires as ONE chain at a time).
    """
    home = _routines_home(request)
    lane = lanes.get(home, lane_id)
    if lane is None:
        raise HTTPException(404, f"no lane {lane_id!r}")
    if not lane.get("members"):
        raise HTTPException(400, "lane has no members to fire")
    rec = lane_runs.arm(home, lane, default_on_failure=lanes.default_on_failure(home),
                        armed_by="ui")
    if rec is None:
        raise HTTPException(409, f"lane {lane_id!r} is already running")
    return {"ok": True, "run": rec}
