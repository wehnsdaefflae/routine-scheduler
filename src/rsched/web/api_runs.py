"""Run access: index, transcripts (paged + SSE live tail), intervention
(inject / pause / resume / abort)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..daemon.runner import abort_process
from ..engine.transcript import read_events
from ..ids import now_iso, parse_run_id
from ..paths import atomic_write_json, read_json
from .sse import TERMINAL_STATES, run_stream, sse_response

router = APIRouter(tags=["runs"])


def _run_dir(request: Request, run_id: str) -> tuple[str, Path]:
    try:
        slug, ts = parse_run_id(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    run_dir = request.app.state.server.routines_home / slug / "runs" / ts
    if not run_dir.is_dir():
        raise HTTPException(404, f"no run {run_id}")
    return slug, run_dir


@router.get("/runs")
def run_index(request: Request, routine: str | None = None, limit: int = 30) -> list[dict]:
    catalog = registry.scan(request.app.state.server)
    infos = ([catalog[routine]] if routine and routine in catalog else
             list(catalog.values()) if not routine else [])
    runs = [r for info in infos for r in info.runs]
    runs.sort(key=lambda r: r.ts, reverse=True)
    return [{"run_id": r.run_id, "routine": r.run_id.split(":", 1)[0], "ts": r.ts,
             "state": r.state, "turn": r.turn, "summary": r.summary[:200],
             "usage": r.usage, "updated": r.updated} for r in runs[:limit]]


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    info = registry.read_run(run_dir, slug)
    subs = sorted(int(p.name) for p in (run_dir / "sub").iterdir()
                  if p.name.isdigit()) if (run_dir / "sub").is_dir() else []
    return {"run_id": info.run_id, "routine": slug, "ts": info.ts, "state": info.state,
            "turn": info.turn, "usage": info.usage, "question": info.question,
            "summary": info.summary, "updated": info.updated, "subruns": subs}


@router.get("/runs/{run_id}/transcript")
def run_transcript(request: Request, run_id: str, offset: int = 0, sub: int | None = None) -> dict:
    _, run_dir = _run_dir(request, run_id)
    path = run_dir / "transcript.jsonl" if sub is None else run_dir / "sub" / str(sub) / "transcript.jsonl"
    events, new_offset = read_events(path, offset)
    return {"events": events, "offset": new_offset}


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str, offset: int = 0):
    _, run_dir = _run_dir(request, run_id)
    return sse_response(run_stream(run_dir, offset))


class Inject(BaseModel):
    text: str


@router.post("/runs/{run_id}/inject")
def inject(request: Request, run_id: str, body: Inject) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    routine_dir = request.app.state.server.routines_home / slug
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    atomic_write_json(routine_dir / "inbox" / f"msg-{now_iso().replace(':', '')}.json",
                      {"text": body.text, "ts": now_iso(), "via": "web"})
    return {"ok": True,
            "delivery": "mid-run" if state not in TERMINAL_STATES else "next-run"}


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
    atomic_write_json(run_dir / "control.json", {"pause": value, "ts": now_iso()})
    return {"ok": True, "pause": value}


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
