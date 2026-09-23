"""CONTROLLING a run in flight — inject, converse, pause, switch, rewind, abort.

Split out of `api_runs.py` (F393): reading a run and steering one are different jobs, and only
these routes write.

They share one discipline worth stating once: the WEB layer records an intent and the ENGINE
acts on it. A model or deliberation switch, or a newly bound rule, goes into the run's
`control.json` and is applied at the next turn boundary — never written into the run's state
from out here, because the engine is the single writer of everything under `runs/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..config import DELIBERATION_LEVELS, load_routine
from ..daemon.runner_state import abort_process
from ..ids import now_iso
from ..paths import read_json
from ..registry import TERMINAL_STATES
from .api_runs import _run_dir, require_active, require_terminal, run_state
from .routines_common import merge_control

router = APIRouter(tags=["run-control"])


async def _file_inbox_message(run_dir: Path, text: str,
                              files: list[UploadFile] | None, via: str) -> None:
    """Deliver a run-page message. Uploads are stored under `attachments/` BESIDE the
    polled inbox — the routine dir, i.e. the run's working dir, so the recorded rels
    resolve for read_file / view_image. The message carries the conversation-style
    attachment block plus the `attachments` rels the engine auto-attaches
    (engine/inbox.py → engine/control.py).
    """
    from ..conversations import attachment_note
    from ..engine import inbox
    from .conversations_common import _save_attachments

    routine_dir = run_dir.parent.parent
    rels = await _save_attachments(routine_dir, files or [])
    inbox.file_message(routine_dir, text.rstrip() + attachment_note(rels), via=via,
                       extra={"attachments": rels} if rels else None)

@router.post("/runs/{run_id}/inject")
async def inject(request: Request, run_id: str, text: Annotated[str, Form()],
                 files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    """Queue a user message for the run — multipart, so file attachments ride along
    exactly like a conversation message (saved beside the polled inbox and auto-attached
    by the engine when the main model can show them).
    """
    _, run_dir = _run_dir(request, run_id)
    text = text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    if not text.strip():
        raise HTTPException(400, "empty message")
    state = run_state(run_dir)
    await _file_inbox_message(run_dir, text, files, via="web")
    return {"ok": True,
            "delivery": "mid-run" if state not in TERMINAL_STATES else "next-run"}

@router.post("/runs/{run_id}/converse")
async def converse(request: Request, run_id: str, text: Annotated[str, Form()],
                   recipe_edit: Annotated[bool, Form()] = False,
                   files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    """Append a message (with optional file attachments) to THIS run's conversation.
    Active run: an ordinary injection, picked up at the next turn boundary. Terminal run:
    the message lands in the inbox and the run is resumed in place (rehydrated transcript,
    fresh budget window) — so any run, live or finished, is an open-ended conversation.
    `recipe_edit` is the "editable recipe" checkbox: resume the finished run as a NORMAL
    conversation whose leg may edit this routine's own recipe via the run-scoped marker.
    """
    slug, run_dir = _run_dir(request, run_id)
    text = text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    if not text.strip():
        raise HTTPException(400, "empty message")
    routine_dir = run_dir.parent.parent
    if recipe_edit:
        # Validate BEFORE the message is filed, so a rejected unlock delivers nothing.
        from ..paths import within
        if not within(request.app.state.server.routines_home, routine_dir):
            raise HTTPException(400, "recipe editing applies to routine runs only")
        if run_state(run_dir) not in TERMINAL_STATES:
            raise HTTPException(409, "recipe editing unlocks when a FINISHED run is "
                                     "resumed — wait for the run to finish")
    state = run_state(run_dir)
    # R81: a message to a TERMINAL run resumes it, but resume() refuses while the daemon is
    # draining for a self-update restart — and nothing re-drives a terminal conversation's
    # pending inbox after relaunch (recover_orphans only closes dead-pid ACTIVE runs). Filing
    # first and letting the resume fail strands the message AND returns a 409 that reads as
    # total failure, so the operator blind-resends (each resend a duplicate — the observed
    # 6× spam). Refuse up front, BEFORE filing, with a clear "not saved — resend once" signal.
    # A live (mid-run) message is unaffected: the in-flight run drains it at its next turn
    # boundary; draining does not kill an already-running run.
    if state in TERMINAL_STATES and getattr(request.app.state.runner, "draining", False):
        raise HTTPException(
            503, "the server is restarting — your message was NOT saved. Resend it once, in a "
                 "moment, after the server is back (repeated resends only pile up duplicates).")
    await _file_inbox_message(run_dir, text, files, via="web-converse")
    if state not in TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run"}
    from ..config import load_routine
    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    if recipe_edit:
        # The "editable recipe" checkbox: the SAME conversation continues, with the sole
        # difference that this leg may edit the routine's own recipe files (one-shot
        # marker, engine/revise.py — cleared when the loop reads it at init).
        from ..engine.revise import write_revise_marker
        write_revise_marker(run_dir, text.strip())
    # D62: an ADMIN resume — the operator drives this conversation leg with the full toolset.
    # The admin token is compared HERE (constant-time, fail-closed) and NEVER reaches the
    # engine; on a match a one-shot marker unlocks capability gating for the resumed leg only.
    from ..engine.admin import ADMIN_HEADER, admin_token_valid, write_admin_marker
    if admin_token_valid(request.headers.get(ADMIN_HEADER)):
        write_admin_marker(run_dir)
    rid = await request.app.state.runner.resume_terminal(cfg, run_dir.name, reason="converse")
    if not rid:
        raise HTTPException(409, "could not resume — another run of this routine is active, "
                                 "or the daemon is draining")
    return {"ok": True, "delivery": "resumed", "run_id": rid}

@router.post("/runs/{run_id}/pause")
def pause(request: Request, run_id: str) -> dict:
    return _set_pause(request, run_id, True)

@router.post("/runs/{run_id}/resume")
def resume(request: Request, run_id: str) -> dict:
    return _set_pause(request, run_id, False)

def _set_pause(request: Request, run_id: str, value: bool) -> dict:
    _, run_dir = _run_dir(request, run_id)
    state = run_state(run_dir)
    if state in TERMINAL_STATES:
        raise HTTPException(409, f"run is already {state}")
    merge_control(run_dir, {"pause": value, "ts": now_iso()})
    return {"ok": True, "pause": value}

class ModelSwitch(BaseModel):
    model: str           # a catalog model name
    kind: str = "main"   # main | tool_call | uncensored (the honeypot role)

@router.post("/runs/{run_id}/model")
def switch_model(request: Request, run_id: str, body: ModelSwitch) -> dict:
    """Switch a live run's model mid-flight. Writes control.json (web-owned); the engine applies it
    at the next turn boundary, where for_model already re-resolves the model every turn.
    """
    _, run_dir = _run_dir(request, run_id)
    server = request.app.state.server
    if body.model not in server.models:
        raise HTTPException(400, f"unknown model {body.model!r} — add it to the catalog first")
    if body.kind not in ("main", "tool_call", "uncensored"):
        raise HTTPException(400, "kind must be main|tool_call|uncensored")
    require_active(run_dir, "switch")
    # merge per-role into any PENDING switch (two quick per-role POSTs must not race:
    # the dict is replaced wholesale, so without the fold the second would drop the
    # first before the engine drains it at the turn boundary; re-applying an already
    # -set role there is an idempotent assignment, so the refreshed ts is harmless)
    ctrl = read_json(run_dir / "control.json")
    pending = ctrl.get("switch_model") if isinstance(ctrl, dict) else None
    sw = dict(pending) if isinstance(pending, dict) else {}
    sw[body.kind] = body.model
    sw["ts"] = now_iso()
    merge_control(run_dir, {"switch_model": sw})
    return {"ok": True, "switch": f"{body.kind} → {body.model}"}

class DeliberationSwitch(BaseModel):
    level: str   # one of DELIBERATION_LEVELS

@router.post("/runs/{run_id}/deliberation")
def switch_deliberation(request: Request, run_id: str, body: DeliberationSwitch) -> dict:
    """Re-level a live run's deliberation mid-flight (run-scoped, like a model switch: the
    durable value stays in routine.yaml). Writes control.json; the engine applies it at the
    next turn boundary with an engine note carrying the new say contract.
    """
    _, run_dir = _run_dir(request, run_id)
    if body.level not in DELIBERATION_LEVELS:
        raise HTTPException(400, f"unknown level {body.level!r} "
                                 f"(expected one of {DELIBERATION_LEVELS})")
    require_active(run_dir, "switch")
    merge_control(run_dir, {"set_deliberation": {"level": body.level, "ts": now_iso()}})
    return {"ok": True, "switch": f"deliberation → {body.level}"}

@router.post("/runs/{run_id}/resume-run")
async def resume_run(request: Request, run_id: str) -> dict:
    """Resume an interrupted run in place: re-spawn the engine on the SAME run dir, rehydrating its
    transcript so it continues where it left off (fresh budget window). Only terminal runs.
    """
    slug, run_dir = _run_dir(request, run_id)
    require_terminal(run_dir, "resumes")
    from ..config import load_routine

    cfg, _ = load_routine(run_dir.parent.parent)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    runner = request.app.state.runner
    rid = await runner.resume(cfg, run_dir.name, reason="user")
    if not rid:
        # Name the ACTUAL blocker (runner.resume_blocker is what resume itself consults), not
        # the old catch-all list of three — which was shown for a run that was none of them.
        why = runner.resume_blocker(cfg, run_dir.name) or "the run could not be started"
        raise HTTPException(409, f"could not resume: {why}")
    return {"ok": True, "run_id": rid}

class Rewind(BaseModel):
    turn: int   # keep the transcript THROUGH this turn, drop everything after, then resume

@router.post("/runs/{run_id}/rewind")
async def rewind_run(request: Request, run_id: str, body: Rewind) -> dict:
    """D69: rewind a terminal conversation to a chosen turn and re-open it live from there —
    the remedy for a run that died or derailed (e.g. a context overflow) instead of losing it.
    Truncates the transcript through `turn` (archiving the dropped tail) and then resumes on the
    same run dir, so the replay continues from the kept point with a fresh budget window.
    """
    slug, run_dir = _run_dir(request, run_id)
    require_terminal(run_dir, "rewinds")
    from ..engine.history import rewind_transcript

    info = rewind_transcript(run_dir, body.turn)
    if info is None:
        raise HTTPException(400,
                            f"cannot rewind to turn {body.turn} — no such turn, or it is already "
                            "the last turn (nothing to drop)")
    cfg, _ = load_routine(run_dir.parent.parent)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    rid = await request.app.state.runner.resume(cfg, run_dir.name, reason="user")
    if not rid:
        raise HTTPException(409, "rewound, but could not resume (already running or draining)")
    return {"ok": True, "run_id": rid, **info}

async def abort_with_fallback(runner, slug: str, run_dir: Path) -> bool:
    """Abort via the runner (daemon-owned runs) with a recorded-pid fallback for runs the
    daemon doesn't track (a CLI run, a pre-restart orphan) — the ONE abort sequence the
    run, conversation, and background endpoints all share.
    """
    if await runner.abort(slug):
        return True
    st = read_json(run_dir / "status.json")
    pid = st.get("pid") if isinstance(st, dict) else None
    return await abort_process(pid)

@router.post("/runs/{run_id}/abort")
async def abort(request: Request, run_id: str) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    if not await abort_with_fallback(request.app.state.runner, slug, run_dir):
        raise HTTPException(409, "no live process for this run")
    return {"ok": True}
