"""New-routine wizard: session lifecycle + the clarify-chat stream. A clarify-instruction
engine run in a dot-hidden pseudo-routine dir (identical engine path, invisible to the
registry). Session persistence and snapshots live in wizard_store; the suggest → finalize →
scaffold half lives in api_wizard. All handlers attach to the shared wizard_common.router.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from ..daemon.llm_tailer import tail_llm_sidecar
from ..daemon.runner import abort_process
from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from . import wizard_store
from .sse import run_stream
from .wizard_common import (
    _center,
    _clarify_run_dir,
    _stop_tailer,
    _wizard_dir,
    _wizard_pid,
    _wizard_recorder,
    router,
)


@router.get("/wizard")
def wizard_list(request: Request) -> list[dict]:
    """Every in-flight new-routine session (the hidden .wizard-* dirs), newest first — so the UI
    can surface + resume them instead of only tracking one in memory.
    """
    return wizard_store.list_sessions(request.app.state)


@router.get("/wizard/{wid}")
def wizard_detail(request: Request, wid: str) -> dict:
    return wizard_store.snapshot(request.app.state, _wizard_dir(request, wid))


@router.delete("/wizard/{wid}")
async def wizard_cancel(request: Request, wid: str) -> dict:
    """Cancel a session: stop the clarify engine process and move the dir out of the way so it
    stops showing as in-flight (mirrors finalize's archive move — no dangling process or dir).
    """
    d = _wizard_dir(request, wid)
    sess = wizard_store.sessions(request.app.state).pop(wid, None)
    _stop_tailer(sess)
    proc = (sess or {}).get("proc")
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
    ts = (sess or {}).get("run_ts") or wizard_store.latest_run_ts(d)
    if ts and (d / "runs" / ts).is_dir():
        st = read_json(d / "runs" / ts / "status.json")
        await abort_process(st.get("pid") if isinstance(st, dict) else None,
                            d / "runs" / ts, f"{wid}:{ts}")
    await asyncio.to_thread(wizard_store.archive_session, request.app.state.server.routines_home,
                            d, f"{wid.lstrip('.')}-canceled")
    if (c := _center(request.app.state)) is not None:
        c.close_process(_wizard_pid(wid))
    return {"ok": True}


class StartBody(BaseModel):
    draft: str


@router.post("/wizard/start")
async def start(request: Request, body: StartBody) -> dict:
    if not body.draft.strip():
        raise HTTPException(400, "empty draft instruction")
    # Don't start a clarify run while the daemon is draining for a self-restart — the drain
    # now waits for live clarify runs, so one accepted here would never converge (and the
    # restart would otherwise kill it mid-conversation). Retry once the daemon is back.
    if request.app.state.scheduler.runner.draining:
        raise HTTPException(503, "the server is restarting — please retry in a moment")
    server = request.app.state.server
    # session creation is all disk writes plus a full library read (candidates) — off the loop
    wid, ts, d = await asyncio.to_thread(wizard_store.create_session, server, body.draft)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rsched.cli", "engine-run", str(d), "--run-ts", ts,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True, cwd=str(d))
    sess = {"proc": proc, "run_ts": ts, "dir": d}
    if (c := _center(request.app.state)) is not None:
        c.open_process(_wizard_pid(wid), kind="wizard",
                       label=f"Create routine: {body.draft.strip()[:50]}")
        # tail the clarify subprocess's sidecar → its turns become children of the create process
        sess["tailer"] = asyncio.create_task(tail_llm_sidecar(
            d / "runs" / ts, _wizard_recorder(c, _wizard_pid(wid), f"{wid}:{ts}")))
    wizard_store.sessions(request.app.state)[wid] = sess
    return {"wid": wid, "run_ts": ts}


@router.get("/wizard/{wid}/events")
async def events(request: Request, wid: str, offset: int = 0):
    return EventSourceResponse(run_stream(_clarify_run_dir(request, wid), offset))


@router.get("/wizard/{wid}/transcript")
def wizard_transcript(request: Request, wid: str, offset: int = 0) -> dict:
    """Paged clarify-chat transcript (mirrors /runs/{id}/transcript) — the byte offset it
    returns is what the UI resumes its SSE tail from after a dropped connection.
    """
    from ..engine.transcript import read_events

    events, new_offset = read_events(_clarify_run_dir(request, wid) / "transcript.jsonl", offset)
    return {"events": events, "offset": new_offset}


class AnswerBody(BaseModel):
    qid: str
    text: str
    intermediate: bool = False   # dialog reply — the question stays open (see interact.handle_ask)


@router.post("/wizard/{wid}/answer")
def answer(request: Request, wid: str, body: AnswerBody) -> dict:
    d = _wizard_dir(request, wid)
    atomic_write_json(d / "inbox" / f"answer-{body.qid}.json",
                      {"qid": body.qid, "text": body.text, "source": "wizard",
                       "intermediate": body.intermediate, "ts": now_iso()})
    return {"ok": True}
