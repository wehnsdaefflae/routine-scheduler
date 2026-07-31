"""Routine-groups CRUD (D53 Phase A): the Groups page's API over the instance-level
`.control/groups.json` store (rsched.groups).

A group is an ORDERED list of routine slugs plus a mid-chain-failure policy. This surface
lets the operator create/name/order/delete groups and set the instance default + per-group
override. It does NOT fire anything — sequential-fire is Phase B, which reads this store on
the daemon tick. Every member slug is validated against the live registry here (the store
validates shape only), so a group can never name a routine that does not exist.

`router` rides the normal authed include in app.py like every other api_* module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import groups
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


class GroupPatch(BaseModel):
    name: str | None = None
    members: list[str] | None = None
    # Tri-state on_failure is expressed with a separate flag so JSON null (inherit) is
    # distinguishable from "field omitted" (leave unchanged) — Pydantic collapses both to None.
    on_failure: str | None = None
    set_on_failure: bool = False


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
    return {"default_on_failure": groups.default_on_failure(home),
            "on_failure_vocab": list(groups.ON_FAILURE),
            "groups": groups.list_groups(home),
            "known_routines": known}


@router.put("/groups/default")
def set_default(request: Request, body: DefaultBody) -> dict:
    try:
        value = groups.set_default_on_failure(_routines_home(request), body.default_on_failure)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "default_on_failure": value}


@router.post("/groups")
def create_group(request: Request, body: GroupCreate) -> dict:
    members = _validate_members(request, body.members)
    try:
        rec = groups.create(_routines_home(request), name=body.name, members=members,
                            on_failure=body.on_failure)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "group": rec}


@router.patch("/groups/{gid}")
def update_group(request: Request, gid: str, body: GroupPatch) -> dict:
    members = _validate_members(request, body.members) if body.members is not None else None
    on_failure = body.on_failure if body.set_on_failure else groups._UNSET
    try:
        rec = groups.update(_routines_home(request), gid, name=body.name,
                           members=members, on_failure=on_failure)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rec is None:
        raise HTTPException(404, f"no group {gid!r}")
    return {"ok": True, "group": rec}


@router.delete("/groups/{gid}")
def delete_group(request: Request, gid: str) -> dict:
    if not groups.delete(_routines_home(request), gid):
        raise HTTPException(404, f"no group {gid!r}")
    return {"ok": True}
