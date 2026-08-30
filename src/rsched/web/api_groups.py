"""Routine-groups CRUD (D53): the Routines page's group-surface API over the
instance-level `.control/groups.json` store (rsched.groups). (D80: the former /groups
subpage is retired — the routines page's group rows are the one management surface.)

A group is an ORDERED list of member records — {"slug"} — plus a mid-chain-failure
policy, and optionally a cron schedule (D71) that auto-arms the chain — saved here as a
friendly spec converted to cron + the server's tz, exactly like a routine's schedule.
The WEB layer only RECORDS it;
the daemon fires (the 0.62.0 split). While a group has a schedule, its members' own crons
are suppressed by the daemon and their Schedule dropdowns read "group managed". Every
member slug is validated against the live registry here (the store validates shape only),
so a group can never name a routine that does not exist.

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


class MemberSpec(BaseModel):
    """One membership record: the routine's slug."""

    slug: str = Field(min_length=1)


def _validate_members(request: Request, members: list[MemberSpec] | None) -> list[dict]:
    """Every member must name a real routine (the store keeps shape/order/dedup; existence
    is ours). A group of a template/disabled routine is allowed — only a NON-EXISTENT slug
    is rejected, with the offending slug named so the UI can point at it.
    """
    if not members:
        return []
    known = set(_catalog(request).keys())
    unknown = [m.slug for m in members if m.slug not in known]
    if unknown:
        raise HTTPException(400, f"unknown routine(s): {', '.join(sorted(unknown))}")
    return [{"slug": m.slug} for m in members]


def _config_layers(request: Request, config: dict) -> dict:
    """The group's shared permissions/capabilities in the routine-detail shape. A tiny stand-in
    carrying just the two fields `permission_layers_detail` reads keeps ONE implementation of
    that shape for both surfaces.
    """
    from types import SimpleNamespace

    from .routines_common import permission_layers_detail

    shared = SimpleNamespace(permissions=list(config.get("permissions") or []),
                             capabilities=dict(config.get("capabilities") or {}))
    perms, caps = permission_layers_detail(_state(request).server, shared)
    return {"permissions": perms, "capabilities": caps}


def _orphan_capabilities(server, config: dict) -> list[str]:
    """Capabilities this group switches on that none of its OWN permissions requires.

    Not an error: a member may hold the covering doc itself, which is the arrangement the
    floor cannot see and must not break. But it is nearly always a mistake — the group grants
    the means without the conduct — so whoever saves it is told, by name.
    """
    from ..grants import _DEFAULT_KIND_SOURCE, read_library_requires, split_util_verb

    caps = config.get("capabilities") or {}
    if not caps:
        return []
    lib = read_library_requires(server.permissions_home)
    held = list(config.get("permissions") or [])
    req_utils = {u for slug in held for u in (lib.get(slug) or {}).get("utils") or []}
    req_names = {split_util_verb(u)[0] for u in req_utils}
    req_actions = {a for slug in held for a in (lib.get(slug) or {}).get("actions") or []}
    out = [f"util {u!r}" for u in caps.get("utils") or []
           if u not in req_utils and split_util_verb(u)[0] not in req_names]
    out += [f"action {a!r}" for a in caps.get("actions") or []
            if a not in req_actions and _DEFAULT_KIND_SOURCE.get(a) not in held]
    return out


def _validate_config(request: Request, config: dict | None) -> dict | None:
    """Validate the group's SHARED routine config (D82) exactly as the routine save path
    validates a member's own: unknown permission/rule slugs and unknown machines are
    rejected by name, and the capability mapping is normalized. `groups._clean_config` keeps
    shape; existence is ours, because we hold the library and the machine catalog.

    Deliberately NOT floored against the group's permissions: the floor binds a ROUTINE's two
    layers, and flooring the group document in isolation would delete a capability whose
    covering permission the member holds itself.

    Nothing floors the MERGED config either, and that is deliberate too — enforcement reads
    capabilities ONLY (test_policy_enforces_capabilities_not_docs), precisely so the doc layer
    can never widen what a run may do. The cost is that a group CAN hand its members a reserved
    util or gated kind with no conduct doc behind it, and the three invariants above each
    correctly decline to catch it. So it is REPORTED instead, twice: `_orphan_capabilities`
    below warns whoever saves the group, and `readmodels/surface.py` shows it per routine
    however it got there — a hand-edited file and a restored backup arrive with no save at all.
    """
    if config is None:
        return None
    from .. import library_docs
    from ..grants import normalize_capabilities

    server = _state(request).server
    unknown: list[str] = []
    for key, home in (("permissions", server.permissions_home), ("rules", server.rules_home)):
        known = set(library_docs.slugs(home))
        unknown += [f"{key[:-1]} {s!r}" for s in config.get(key) or [] if s not in known]
    catalog = _state(request).server.machines
    unknown += [f"machine {m!r}" for m in config.get("machines") or [] if m not in catalog]
    if unknown:
        raise HTTPException(400, f"unknown {', '.join(sorted(unknown))}")
    if "capabilities" in config:
        caps, problems = normalize_capabilities(config["capabilities"], label="config.capabilities")
        if problems:
            raise HTTPException(422, "; ".join(problems))
        config = {**config, "capabilities": caps}
    return config


class GroupCreate(BaseModel):
    name: str = Field(min_length=1)
    members: list[MemberSpec] = Field(default_factory=list)
    on_failure: str | None = None
    # D71: {"friendly": {…}} — the same spec shape the routine schedule editor sends;
    # cron is built server-side and the server tz recorded beside it.
    schedule: dict | None = None


class GroupPatch(BaseModel):
    # forbid unknown keys, like RoutinePatch: a silently-dropped stray reads as "saved"
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    members: list[MemberSpec] | None = None
    # Tri-state on_failure is expressed with a separate flag so JSON null (inherit) is
    # distinguishable from "field omitted" (leave unchanged) — Pydantic collapses both to None.
    on_failure: str | None = None
    set_on_failure: bool = False
    schedule: dict | None = None
    # Whole-group pause: true stops the group's cron from auto-arming its chain (members
    # stay group-managed, so NOTHING in the group fires on a schedule); an explicit
    # "Run now" still works. None = leave unchanged.
    paused: bool | None = None
    # Shared routine config every member INHERITS (D82). REPLACES wholesale, like models/
    # machines on a routine: dropping a key here returns that setting to each member's own
    # routine.yaml. None = leave unchanged.
    config: dict | None = None


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
    """The routines page's group payload: the instance default + every group.
    `known_routines` is the ordered slug/name list the group-member picker offers.
    """
    home = _routines_home(request)
    catalog = _catalog(request)
    known = [{"slug": s, "name": info.cfg.name or s} for s, info in sorted(catalog.items())]
    # in-flight sequential fires (Phase B), keyed by group id, so the UI can show a running
    # chain's progress and refuse a duplicate "Run now" (the F292 two-pass `phase` field
    # is retired with the split flag — D90, 0.205.0; chains fire once over the members)
    in_flight = {str(r["group_id"]): {"cursor": r.get("cursor", 0), "status": r.get("status"),
                                      "members": r.get("members", []), "log": r.get("log", [])}
                 for r in group_runs.in_flight(home)}
    # each group rides out with its schedule prefill (the editor speaks friendly specs)
    # plus the human sentence, so list surfaces (R313: the routines overview) can show a
    # member's REAL schedule — the group's — without a client-side cron parser
    recs = [{**g, "schedule_friendly": schedule.cron_to_friendly(g["cron"]),
             "schedule_desc": schedule.describe(g["cron"]) if g["cron"] else "",
             # D82: the group's SHARED config rendered in the same two-layer shape the routine
             # page uses, so the group editor mounts the very same permissions control rather
             # than a lookalike that drifts from it.
             "config_layers": _config_layers(request, g.get("config") or {})}
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
                           paused=body.paused,
                           config=_validate_config(request, body.config))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rec is None:
        raise HTTPException(404, f"no group {gid!r}")
    if sched is not None or members is not None or body.paused is not None:
        _rescan(request)   # membership + pause changes move the fire/suppression tables too
    # An orphan capability is legal (a member may hold the covering doc) but nearly always a
    # mistake, and nothing downstream can catch it — so say so at the one moment somebody is
    # looking. Returned, never raised: refusing would break the legitimate arrangement.
    warnings = _orphan_capabilities(_state(request).server, body.config or {})
    return {"ok": True, "group": rec,
            **({"warnings": [f"{w} is switched on, but no permission in this group requires it "
                             "— it takes effect only for members holding a covering doc "
                             "themselves" for w in warnings]} if warnings else {})}


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
