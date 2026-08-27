"""Stopping conditions: read + whole-document PUT, for BOTH homes (F334/D98).

One implementation, two routes each, exactly like `apply_rule_edit` — a routine and a
conversation carry the same store, so a second copy of this would be a second place for the
semantics to drift. Routines were the "per-stage routine conditions LATER" half of the original
2026-08-14 order; they get the same surface plus the `stage` field, which is what "per-stage"
actually needs.

The read returns the evaluated document — the group verdicts and the overall satisfaction —
because every consumer needs them and recomputing the boolean structure in JavaScript is exactly
how a panel ends up disagreeing with the prompt the run was given.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..engine import stopping
from ..ids import now_iso
from .conversations_common import conversation_info
from .routines_common import _info, active_run_dir

router = APIRouter(tags=["stopping"])


class Condition(BaseModel):
    """One user-owned condition. `extra="forbid"` for the same reason every other save path
    forbids it: a misspelled key silently dropped reads as "saved".
    """

    model_config = ConfigDict(extra="forbid")
    id: str = ""
    text: str
    status: str = "open"
    group: str = ""
    requires: list[str] = []
    stage: str = ""          # routine conditions: live only while the run is in this stage
    ts: str = ""
    # `note` / `resolved_ts` / `resolved_run` are deliberately NOT accepted: the engine writes
    # them at a finish (stopping.ENGINE_OWNED) and `save` carries them forward, so a client can
    # neither fabricate a resolution nor erase one by editing a condition's text.


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    name: str = ""
    mode: str = "all"        # all = AND over its members, any = OR


class StoppingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "all"        # how the GROUPS combine
    groups: list[Group] = []
    conditions: list[Condition] = []


def _read(routine_dir: Path) -> dict:
    doc = stopping.load(routine_dir)
    by_id = {c["id"]: c for c in doc["conditions"]}
    # `blocked` travels with each row so the panel can grey a dormant condition and SAY why
    # without re-deriving the dependency logic. Phase-independent here: the read model has no
    # live run to ask, and a stage-scoped row shows its stage instead.
    rows = [{**c, "blocked": stopping.blocked_reason(c, by_id)} for c in doc["conditions"]]
    return {**doc, "conditions": rows, "verdict": stopping.evaluate(doc)}


def _write(routine_dir: Path, body: StoppingBody) -> dict:
    if len(body.conditions) > 60:
        raise HTTPException(400, "too many conditions (60 max) — a goal nobody can read is "
                                 "not a bound, and the prompt section has to stay legible")
    for g in body.groups:
        if g.mode not in stopping.MODES:
            raise HTTPException(400, f"group mode must be one of {list(stopping.MODES)}")
    for c in body.conditions:
        if c.status not in stopping.STATUSES:
            raise HTTPException(400, f"status must be one of {list(stopping.STATUSES)}")
    if body.mode not in stopping.MODES:
        raise HTTPException(400, f"mode must be one of {list(stopping.MODES)}")
    stopping.save(routine_dir, body.model_dump(), now=now_iso())
    return _read(routine_dir)


# ---- conversations ---------------------------------------------------------------------------

@router.get("/conversations/{slug}/stopping")
def get_conversation_stopping(request: Request, slug: str) -> dict:
    return _read(conversation_info(request, slug).cfg.dir)


@router.put("/conversations/{slug}/stopping")
def set_conversation_stopping(request: Request, slug: str, body: StoppingBody) -> dict:
    """Replace the document (user-owned, whole-document PUT — the same single-writer shape as
    the rules/permissions saves). A reply already in flight keeps the conditions it booted
    with; the next reply reads these.
    """
    info = conversation_info(request, slug)
    out = _write(info.cfg.dir, body)
    return {"ok": True, **out, "live_run": active_run_dir(info) is not None}


# ---- routines --------------------------------------------------------------------------------

@router.get("/routines/{slug}/stopping")
def get_routine_stopping(request: Request, slug: str) -> dict:
    return _read(_info(request, slug).cfg.dir)


@router.put("/routines/{slug}/stopping")
def set_routine_stopping(request: Request, slug: str, body: StoppingBody) -> dict:
    """The routine half of the original order. Not guarded by an active run, like the rules
    save and for the same reason: no run writes this file, so the web layer is the only writer
    and there is no two-writer race. A run already going keeps what it booted with.
    """
    info = _info(request, slug)
    out = _write(info.cfg.dir, body)
    return {"ok": True, **out, "live_run": active_run_dir(info) is not None}
