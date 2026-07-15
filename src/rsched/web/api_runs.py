"""Run access: index, transcripts (paged + SSE live tail), intervention
(inject / pause / resume / abort).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from ..daemon import registry
from ..daemon.registry import TERMINAL_STATES
from ..daemon.runner import abort_process
from ..engine.transcript import read_events
from ..ids import now_iso, parse_run_id
from ..paths import atomic_write_json, read_json
from .sse import run_stream

router = APIRouter(tags=["runs"])


def _run_dir(request: Request, run_id: str) -> tuple[str, Path]:
    """Resolve a run id in routines_home OR conversations_home — a conversation's run is a
    run like any other (transcript, SSE, inject, converse, abort all apply). The owning
    routine/conversation dir is always run_dir.parent.parent.
    """
    try:
        slug, ts = parse_run_id(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    server = request.app.state.server
    for home in (server.routines_home, server.conversations_home, server.background_home):
        run_dir = home / slug / "runs" / ts
        if run_dir.is_dir():
            return slug, run_dir
    raise HTTPException(404, f"no run {run_id!r}")


@router.get("/runs")
def run_index(request: Request, routine: str | None = None, limit: int = 30) -> list[dict]:
    """Recent runs, newest first. `routine` filters to ONE slug, resolved across all three
    homes like _run_dir — a conversation's or a detached task's runs list here too;
    without it, the index covers routines_home (the dashboard's world).
    """
    server = request.app.state.server
    if routine:
        runs = next((registry.run_index(home / routine, routine)
                     for home in (server.routines_home, server.conversations_home,
                                  server.background_home)
                     if (home / routine / "routine.yaml").exists()), [])
    else:
        runs = [r for info in registry.scan(server).values() for r in info.runs]
    runs.sort(key=lambda r: r.ts, reverse=True)
    return [{"run_id": r.run_id, "routine": r.run_id.split(":", 1)[0], "ts": r.ts,
             "state": r.state, "turn": r.turn, "summary": r.summary[:200],
             "usage": r.usage, "elapsed_s": r.elapsed_s, "updated": r.updated}
            for r in runs[:limit]]


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    info = registry.read_run(run_dir, slug)
    subs = sorted(int(p.name) for p in (run_dir / "sub").iterdir()
                  if p.name.isdigit()) if (run_dir / "sub").is_dir() else []
    st = read_json(run_dir / "status.json")
    model = st.get("model") if isinstance(st, dict) else ""
    return {"run_id": info.run_id, "routine": slug, "ts": info.ts, "state": info.state,
            "turn": info.turn, "usage": info.usage, "elapsed_s": info.elapsed_s,
            "question": info.question, "model": model,
            "summary": info.summary, "updated": info.updated, "subruns": subs}


@router.get("/runs/{run_id}/transcript")
def run_transcript(request: Request, run_id: str, offset: int = 0, sub: str | None = None) -> dict:
    """Paged transcript events. `sub` selects a subrun's transcript; a nested child is a
    slash path of subrun numbers ("2/1" = child 1 of child 2), matching sub/<n>/sub/<m>/
    on disk — the UI unfolds subrun conversations recursively with this.
    """
    import re

    _, run_dir = _run_dir(request, run_id)
    if sub is not None and not re.fullmatch(r"\d+(?:/\d+)*", sub):
        raise HTTPException(400, "sub must be a subrun number or a nested n/m/... path")
    for n in sub.split("/") if sub else []:
        run_dir = run_dir / "sub" / n
    events, new_offset = read_events(run_dir / "transcript.jsonl", offset)
    return {"events": events, "offset": new_offset}


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str, offset: int = 0):
    _, run_dir = _run_dir(request, run_id)
    return EventSourceResponse(run_stream(run_dir, offset))


@router.get("/runs/{run_id}/phases")
def run_phases(request: Request, run_id: str) -> dict:
    """Per-phase instrumentation (turns / tokens / cost / wall-clock) derived from the
    run's transcript — the state-graph rail's numbers.
    """
    from ..statemap import phase_stats

    _, run_dir = _run_dir(request, run_id)
    return {"phases": phase_stats(run_dir)}


@router.get("/runs/{run_id}/tree")
def run_tree(request: Request, run_id: str) -> dict:
    """The recursive task tree: this run's sequential subtasks + parallel subruns, each a node
    with mode / state / live turns / allotted budget and its own children nested. A read-model
    over the on-disk sub/ transcripts (nothing is written) — the rail's decomposition view.
    """
    from .tasktree import build_tree

    _, run_dir = _run_dir(request, run_id)
    return {"tree": build_tree(run_dir)}


class Inject(BaseModel):
    text: str


@router.post("/runs/{run_id}/inject")
def inject(request: Request, run_id: str, body: Inject) -> dict:
    _, run_dir = _run_dir(request, run_id)
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    routine_dir = run_dir.parent.parent
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    atomic_write_json(routine_dir / "inbox" / f"msg-{now_iso().replace(':', '')}.json",
                      {"text": body.text, "ts": now_iso(), "via": "web"})
    return {"ok": True,
            "delivery": "mid-run" if state not in TERMINAL_STATES else "next-run"}


@router.post("/runs/{run_id}/converse")
async def converse(request: Request, run_id: str, body: Inject) -> dict:
    """Append a message to THIS run's conversation. Active run: an ordinary injection, picked
    up at the next turn boundary. Terminal run: the message lands in the inbox and the run is
    resumed in place (rehydrated transcript, fresh budget window) — so any run, live or
    finished, is an open-ended conversation.
    """
    slug, run_dir = _run_dir(request, run_id)
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    routine_dir = run_dir.parent.parent
    atomic_write_json(routine_dir / "inbox" / f"msg-{now_iso().replace(':', '')}.json",
                      {"text": body.text, "ts": now_iso(), "via": "web-converse"})
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    if state not in TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run"}
    from ..config import load_routine

    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
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
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    if state in TERMINAL_STATES:
        raise HTTPException(409, f"run is already {state}")
    ctrl = read_json(run_dir / "control.json")
    ctrl = dict(ctrl) if isinstance(ctrl, dict) else {}       # keep any pending switch_model
    ctrl.update({"pause": value, "ts": now_iso()})
    atomic_write_json(run_dir / "control.json", ctrl)
    return {"ok": True, "pause": value}


class ModelSwitch(BaseModel):
    model: str           # a catalog model name
    kind: str = "main"   # main | subroutine | tool_call


@router.post("/runs/{run_id}/model")
def switch_model(request: Request, run_id: str, body: ModelSwitch) -> dict:
    """Switch a live run's model mid-flight. Writes control.json (web-owned); the engine applies it
    at the next turn boundary, where for_model already re-resolves the model every turn.
    """
    _, run_dir = _run_dir(request, run_id)
    server = request.app.state.server
    if body.model not in server.models:
        raise HTTPException(400, f"unknown model {body.model!r} — add it to the catalog first")
    if body.kind not in ("main", "subroutine", "tool_call"):
        raise HTTPException(400, "kind must be main|subroutine|tool_call")
    st = read_json(run_dir / "status.json")
    if (st.get("state") if isinstance(st, dict) else None) in TERMINAL_STATES:
        raise HTTPException(409, "run is not active; nothing to switch")
    ctrl = read_json(run_dir / "control.json")
    ctrl = dict(ctrl) if isinstance(ctrl, dict) else {}       # keep pause
    ctrl["switch_model"] = {body.kind: body.model, "ts": now_iso()}
    atomic_write_json(run_dir / "control.json", ctrl)
    return {"ok": True, "switch": f"{body.kind} → {body.model}"}


@router.post("/runs/{run_id}/resume-run")
async def resume_run(request: Request, run_id: str) -> dict:
    """Resume an interrupted run in place: re-spawn the engine on the SAME run dir, rehydrating its
    transcript so it continues where it left off (fresh budget window). Only terminal runs.
    """
    slug, run_dir = _run_dir(request, run_id)
    st = read_json(run_dir / "status.json")
    if (st.get("state") if isinstance(st, dict) else None) not in TERMINAL_STATES:
        raise HTTPException(409,
                            "run is still active — only a finished/failed/aborted run resumes")
    from ..config import load_routine

    cfg, _ = load_routine(run_dir.parent.parent)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    rid = await request.app.state.runner.resume(cfg, run_dir.name, reason="user")
    if not rid:
        raise HTTPException(409, "could not resume (already running, draining, or run dir gone)")
    return {"ok": True, "run_id": rid}


@router.post("/runs/{run_id}/abort")
async def abort(request: Request, run_id: str) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    runner = request.app.state.runner
    ok = await runner.abort(slug)
    if not ok:  # not daemon-owned (CLI run?) — fall back to the recorded pid
        st = read_json(run_dir / "status.json")
        pid = st.get("pid") if isinstance(st, dict) else None
        ok = await abort_process(pid, run_dir, run_id)
    if not ok:
        raise HTTPException(409, "no live process for this run")
    return {"ok": True}
