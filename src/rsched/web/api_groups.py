"""Routine-groups CRUD (D53): the Groups page's API over the instance-level
`.control/groups.json` store (rsched.groups).

A group is an ORDERED list of routine slugs plus a mid-chain-failure policy, and
optionally a cron schedule (D71) that auto-arms the chain — saved here as a friendly spec
converted to cron + the server's tz, exactly like a routine's schedule. The WEB layer only
RECORDS it; the daemon fires (the 0.62.0 split). While a group has a schedule, its
members' own crons are suppressed by the daemon and their Schedule dropdowns read "group
managed". Every member slug is validated against the live registry here (the store
validates shape only), so a group can never name a routine that does not exist.

`router` rides the normal authed include in app.py like every other api_* module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import group_runs, groups, schedule
from .routines_common import _catalog, _state

router = APIRouter(tags=["groups"])


def _routines_home(request: Request):
    return _state(request).server.routines_home


def _validate_members(request: Request, members: list[str] | None) -> list[str]:
    """Every member must name a real routine (the store keeps shape/order/dedup; existence
    is ours). A group of a template/disabled routine is allowed — only a NON-EXISTENT slug
    is rejected, with the offending slug named so the UI can point at it.
    """
    if not members:
        return []
    known = set(_catalog(request).keys())
    unknown = [m for m in members if m not in known]
    if unknown:
        raise HTTPException(400, f"unknown routine(s): {', '.join(sorted(unknown))}")
    return list(members)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1)
    members: list[str] = Field(default_factory=list)
    on_failure: str | None = None
    # D71: {"friendly": {…}} — the same spec shape the routine schedule editor sends;
    # cron is built server-side and the server tz recorded beside it.
    schedule: dict | None = None


class GroupPatch(BaseModel):
    # forbid unknown keys, like RoutinePatch: a silently-dropped stray reads as "saved"
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    members: list[str] | None = None
    # Tri-state on_failure is expressed with a separate flag so JSON null (inherit) is
    # distinguishable from "field omitted" (leave unchanged) — Pydantic collapses both to None.
    on_failure: str | None = None
    set_on_failure: bool = False
    schedule: dict | None = None
    # Whole-group pause: true stops the group's cron from auto-arming its chain (members
    # stay group-managed, so NOTHING in the group fires on a schedule); an explicit
    # "Run now" still works. None = leave unchanged.
    paused: bool | None = None


def _schedule_to_cron(spec: dict | None) -> tuple[str, str] | None:
    """The body's schedule field → (cron, tz), or None when the field was omitted. A
    friendly 'manual' spec yields ("", "") — the group becomes unscheduled and its
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


@router.get("/groups")
def list_groups(request: Request) -> dict:
    """The Groups page payload: the instance default + every group. `known_routines` is the
    ordered slug/name list the group-member picker offers.
    """
    home = _routines_home(request)
    catalog = _catalog(request)
    known = [{"slug": s, "name": info.cfg.name or s} for s, info in sorted(catalog.items())]
    # in-flight sequential fires (Phase B), keyed by group id, so the UI can show a running
    # chain's progress and refuse a duplicate "Run now"
    in_flight = {str(r["group_id"]): {"cursor": r.get("cursor", 0), "status": r.get("status"),
                                      "members": r.get("members", []), "log": r.get("log", [])}
                 for r in group_runs.in_flight(home)}
    # each group rides out with its schedule prefill (the editor speaks friendly specs)
    recs = [{**g, "schedule_friendly": schedule.cron_to_friendly(g["cron"])}
            for g in groups.list_groups(home)]
    return {"default_on_failure": groups.default_on_failure(home),
            "on_failure_vocab": list(groups.ON_FAILURE),
            "groups": recs,
            "in_flight": in_flight,
            "known_routines": known,
            "server_tz": schedule.server_tz()}


@router.put("/groups/default")
def set_default(request: Request, body: DefaultBody) -> dict:
    try:
        value = groups.set_default_on_failure(_routines_home(request), body.default_on_failure)
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


@router.post("/groups")
def create_group(request: Request, body: GroupCreate) -> dict:
    members = _validate_members(request, body.members)
    sched = _schedule_to_cron(body.schedule)
    try:
        rec = groups.create(_routines_home(request), name=body.name, members=members,
                            on_failure=body.on_failure,
                            cron=sched[0] if sched else "", tz=sched[1] if sched else "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if sched:
        _rescan(request)
    return {"ok": True, "group": rec}


@router.patch("/groups/{gid}")
def update_group(request: Request, gid: str, body: GroupPatch) -> dict:
    members = _validate_members(request, body.members) if body.members is not None else None
    on_failure = body.on_failure if body.set_on_failure else groups._UNSET
    sched = _schedule_to_cron(body.schedule)
    try:
        rec = groups.update(_routines_home(request), gid, name=body.name,
                           members=members, on_failure=on_failure,
                           cron=sched[0] if sched else None,
                           tz=sched[1] if sched else None,
                           paused=body.paused)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rec is None:
        raise HTTPException(404, f"no group {gid!r}")
    if sched is not None or members is not None or body.paused is not None:
        _rescan(request)   # membership + pause changes move the fire/suppression tables too
    return {"ok": True, "group": rec}


@router.delete("/groups/{gid}")
def delete_group(request: Request, gid: str) -> dict:
    if not groups.delete(_routines_home(request), gid):
        raise HTTPException(404, f"no group {gid!r}")
    return {"ok": True}


@router.post("/groups/{gid}/run")
def run_group(request: Request, gid: str) -> dict:
    """Arm a sequential fire of group `gid` (D53 Phase B): the daemon's GroupRunManager picks
    it up on the next tick and fires the members in order. The on_failure policy is resolved
    NOW (the group's override, else the instance default) and the member list is snapshotted,
    so later edits to the group never change this run. 404 if the group is unknown; 409 if a
    chain for it is already in flight (a group fires as ONE chain at a time).
    """
    home = _routines_home(request)
    group = groups.get(home, gid)
    if group is None:
        raise HTTPException(404, f"no group {gid!r}")
    if not group.get("members"):
        raise HTTPException(400, "group has no members to fire")
    rec = group_runs.arm(home, group, default_on_failure=groups.default_on_failure(home),
                         armed_by="ui")
    if rec is None:
        raise HTTPException(409, f"group {gid!r} is already running")
    return {"ok": True, "run": rec}
