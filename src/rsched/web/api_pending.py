"""Queued proposals: list, materialize, discard (F328).

The WEB layer materializes, exactly as it already applies forever-grants — that is the whole
reason this file exists rather than the engine doing it. A run never writes `routine.yaml`, so a
scheduled run's proposal has to cross into the config-writing half somewhere, and this is the
one place it does. Materializing goes through the SAME `workflows.scaffold` / `rsched.groups`
calls a conversation's confirmed creation uses — one materializer, not a second path that can
drift from it.

Three kinds ride this queue. Two are creations (`create_routine`, `manage_group`). The third,
`goal-reached`, is the opposite: a routine reporting that it is FINISHED. It is queued by
`engine/goalreached.py` the run its final goal is met, and by then the routine has already stopped
running — that half is derived from its goal document and needs no click. Approving writes
`enabled: false` through the ordinary PATCH; discarding reopens the goal, which puts the routine
back on the schedule. Doing nothing leaves it paused with the proposal standing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import groups, pending
from ..engine import stopping
from ..ids import now_iso

router = APIRouter(tags=["pending"])


class Discard(BaseModel):
    reason: str = ""


@router.get("/pending-creations")
def list_pending(request: Request) -> list[dict]:
    """Everything a run has proposed and nobody has decided yet, oldest first."""
    return pending.load_all(request.app.state.server.routines_home)


def _materialize_routine(server, fields: dict) -> dict:
    from ..workflows.scaffold import scaffold

    slug = str(fields.get("slug") or "").strip()
    if (server.routines_home / slug).exists():
        raise HTTPException(409, f"a routine {slug!r} already exists — discard this proposal or "
                                 "rename it before creating")
    name = str(fields.get("name") or "").strip()
    raw_stopping = fields.get("stopping")
    instruction = str(fields.get("instruction") or "")
    workflow_slug = str(fields.get("workflow") or "")
    from ..workflows.suggest import generate_description
    routine_dir = scaffold(server, slug=slug, name=name,
                           instruction=instruction, workflow_slug=workflow_slug,
                           # a COMPREHENSIVE generated description — the same one a
                           # conversation's confirmed create gets; falls back to the name
                           # when no endpoint answers, so materialization never fails on it
                           description=generate_description(server, name=name,
                                                            instruction=instruction,
                                                            workflow_slug=workflow_slug),
                           # the queued proposal carries the DONE answer the same way a
                           # conversation's confirmed call does — one materializer, one path
                           stopping=[t for t in raw_stopping if isinstance(t, str) and t.strip()]
                           if isinstance(raw_stopping, list) else None)
    return {"created": "routine", "slug": slug, "dir": str(routine_dir)}


def _materialize_group(server, fields: dict) -> dict:
    verb = str(fields.get("verb") or "").strip()
    home = server.routines_home
    gid = str(fields.get("target") or "").strip()
    name = str(fields.get("name") or "").strip()
    raw_members = fields.get("members")
    # The action's flat ordered slugs become the store's member RECORDS — the same wrap
    # engine/manage_group does, so a proposal builds the group a conversation would have.
    members = ([{"slug": str(m)} for m in raw_members]
               if isinstance(raw_members, list) else None)
    on_failure = fields.get("on_failure")
    cron = str(fields.get("cron") or "")
    if verb == "create":
        rec = groups.create(home, name=name, members=members,
                            on_failure=str(on_failure) if on_failure else None, cron=cron)
        return {"created": "group", "gid": rec["id"], "name": rec.get("name")}
    if verb == "update":
        paused = fields.get("paused")
        # `on_failure` is a TRI-STATE in the store (omit = unchanged, None = inherit, value =
        # override) and `cron` "" clears a schedule, so both are only passed when the proposal
        # actually carried them — a default would silently rewrite a field nobody proposed.
        extra: dict[str, Any] = {}
        if on_failure:
            extra["on_failure"] = str(on_failure)
        if "cron" in fields:
            extra["cron"] = cron
        updated = groups.update(
            home, gid, name=name or None, members=members,
            paused=bool(paused) if paused is not None else None, **extra)
        if updated is None:
            raise HTTPException(404, f"no group {gid!r} — it was deleted since the proposal")
        return {"updated": "group", "gid": gid}
    if verb == "delete":
        if not groups.delete(home, gid):
            raise HTTPException(404, f"no group {gid!r} — it is already gone")
        return {"deleted": "group", "gid": gid}
    if verb == "set-default":
        value = groups.set_default_on_failure(home, str(fields.get("on_failure") or ""))
        return {"default_on_failure": value}
    # Only config verbs (create/update/delete/set-default) are proposable — the engine stopped
    # queuing `run` (a fire is ephemeral, not a creation). A non-config verb reaches here only as
    # a LEGACY record queued before that fix; tell the operator to discard it and fire live.
    raise HTTPException(
        400, f"a group {verb!r} is not a creation and cannot be approved here — discard this "
             "proposal and fire the group live from its row instead")


def _materialize_goal_reached(request: Request, rec: dict) -> dict:
    """Confirm a retirement: write `enabled: false` through the ONE config writer.

    The routine has ALREADY stopped running — the scheduler builds no fire table entry for a
    routine whose goal is satisfied (registry.RoutineInfo.retired), which is what let it retire
    itself without anything writing config. This click is what makes that permanent and legible:
    after it, the routine reads as switched off in every surface that has ever meant it, and it
    survives someone clearing a goal condition later.

    Group membership is deliberately NOT touched. A retired member is skipped by its chains
    without counting as a failure (daemon/group_runs.py), so removing it would buy nothing and
    cost the D82 config it inherits and its access to the group store — and moving a member
    between groups silently changing its effective config is a trap this codebase already knows.
    """
    from .api_routine_patch import RoutinePatch, patch_routine

    slug = str(rec.get("routine") or "")
    out = patch_routine(request, slug, RoutinePatch(enabled=False))
    return {"retired": slug, "updated": out.get("updated", [])}


@router.post("/pending-creations/{pid}/materialize")
def materialize(request: Request, pid: str) -> dict:
    """Build what the run proposed. The record is dropped either way it ends — a proposal that
    materialized is done, and one that failed is a proposal the operator must look at again
    rather than a button that silently does nothing twice.
    """
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    fields = rec.get("fields") or {}
    try:
        if rec.get("kind") == "create_routine":
            out = _materialize_routine(server, fields)
        elif rec.get("kind") == "manage_group":
            out = _materialize_group(server, fields)
        elif rec.get("kind") == "goal-reached":
            out = _materialize_goal_reached(request, rec)
        else:
            raise HTTPException(400, f"unknown proposal kind {rec.get('kind')!r}")
    except ValueError as exc:
        # a bad slug or an unknown pattern: the proposal is wrong, not the click — keep it on
        # the page with a legible reason so the operator can discard it deliberately
        raise HTTPException(400, str(exc)) from exc
    pending.drop(server.routines_home, pid)
    told = pending.notify_proposer(server, rec, "approved and materialized")
    return {"ok": True, "id": pid, "notified": told, **out}


@router.post("/pending-creations/{pid}/discard")
def discard(request: Request, pid: str, body: Discard) -> dict:
    """Throw the proposal away. The proposing routine is told, so its next run stops waiting on
    something that is never coming.
    """
    server = request.app.state.server
    rec = pending.load(server.routines_home, pid)
    if rec is None:
        raise HTTPException(404, f"no pending creation {pid!r}")
    reopened: list[str] = []
    if rec.get("kind") == "goal-reached":
        # Discarding a retirement means "not yet — keep going", and that has to change the goal
        # document, because retirement is DERIVED from it. Dropping the record alone would leave
        # the routine unscheduled with nothing left on the page to act on.
        routine_dir = server.routines_home / str(rec.get("routine") or "")
        if (routine_dir / "routine.yaml").is_file():
            reopened = stopping.reopen_goal(routine_dir, now=now_iso())
            request.app.state.scheduler.rescan()
    pending.drop(server.routines_home, pid)
    outcome = f"discarded ({body.reason.strip()})" if body.reason.strip() else "discarded"
    if reopened:
        outcome = (f"declined — the goal is not reached, so {', '.join(reopened)} "
                   "were reopened and the routine is scheduled again")
    return {"ok": True, "id": pid, "reopened": reopened,
            "notified": pending.notify_proposer(server, rec, outcome)}
