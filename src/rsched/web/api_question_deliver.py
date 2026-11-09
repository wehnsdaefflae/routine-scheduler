"""An UNDELIVERED answer's two exits — `deliver` and `settle` (2026-11-09).

`web/decision_pickup` marks an answered decision `pickup: never` when no run will read it.
Both routes act on exactly that state and refuse anything else, naming what IS true:

- `POST /questions/{qid}/deliver` — one run reads it now: a finished conversation is resumed in
  place (the same terminal resume the answer route takes, F39), a routine gets ONE manual fire
  (the routine page's Run now). Your click starts it, which is the only way a run may start
  outside the schedule and the triggers you configured.
- `POST /questions/{qid}/settle` — no run: the ask and the answer are archived the way a run's
  consumption archives them (`engine/answer_settle`), for an answer you no longer want acted on.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import answerfile, registry
from ..config import load_routine
from ..engine import answer_settle
from .decision_pickup import annotate
from .decisions_read import _record_dir, find_question

router = APIRouter(tags=["questions"])


def _undelivered(request: Request, qid: str) -> tuple[dict, Path]:
    """The record and its dir, refusing unless the answer waits with no reader."""
    state = request.app.state
    match = find_question(state.server, qid)
    if match is None:
        raise HTTPException(404, f"no open decision {qid}")
    routine_dir = _record_dir(state.server, match)
    if not answerfile.path(routine_dir, qid).is_file():
        raise HTTPException(409, f"{qid} is not answered yet — answer it first")
    pick = annotate(state, [{**match, "answered": True}])[0].get("pickup") or {}
    if pick.get("state") != "never":
        raise HTTPException(409, f"{qid} already has a reader ({pick.get('state', 'unknown')})"
                                 " — it is read without this")
    return match, routine_dir


@router.post("/questions/{qid}/deliver")
async def deliver(request: Request, qid: str) -> dict:
    match, routine_dir = await run_in_threadpool(_undelivered, request, qid)
    state = request.app.state
    if match.get("background"):
        raise HTTPException(409, "a finished background task cannot be resumed — settle it")
    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(409, f"{match['routine']} has no loadable config — settle it")
    if match.get("conversation"):
        if await state.runner.resume_terminal(cfg, reason="converse"):
            return {"ok": True, "routine": match["routine"], "resumed": True}
        raise HTTPException(409, "the conversation could not be resumed — open it and write to it")
    info = registry.info(state.server, state.server.routines_home, cfg.slug)
    if info is None or not info.fireable:
        raise HTTPException(409, f"{cfg.slug} is off or retired — switch it on, or settle this")
    run_id = await state.runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"{cfg.slug} refused to start — see its page")
    return {"ok": True, "routine": cfg.slug, "run_id": run_id}


@router.post("/questions/{qid}/settle")
def settle(request: Request, qid: str) -> dict:
    match, routine_dir = _undelivered(request, qid)
    if not answer_settle.settle(routine_dir, qid):
        raise HTTPException(409, f"{qid}'s answer was consumed meanwhile")
    request.app.state.bus.publish({"event": "question_answered", "qid": qid,
                                   "routine": match["routine"]})
    return {"ok": True, "routine": match["routine"], "settled": True}
