"""Queued creations: list, materialize, discard (F328).

The WEB layer materializes, exactly as it already applies forever-grants — that is the whole
reason this file exists rather than the engine doing it. A run never writes `routine.yaml`, so a
scheduled run's proposal has to cross into the config-writing half somewhere, and this is the
one place it does. Materializing goes through the SAME `workflows.scaffold` / `rsched.groups`
calls a conversation's confirmed creation uses — one materializer, not a second path that can
drift from it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import groups, pending

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
    routine_dir = scaffold(server, slug=slug, name=name,
                           instruction=str(fields.get("instruction") or ""),
                           workflow_slug=str(fields.get("workflow") or ""),
                           description=name,
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
    raise HTTPException(400, f"cannot materialize group verb {verb!r}")


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
    pending.drop(server.routines_home, pid)
    outcome = f"discarded ({body.reason.strip()})" if body.reason.strip() else "discarded"
    return {"ok": True, "id": pid, "notified": pending.notify_proposer(server, rec, outcome)}
