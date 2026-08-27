"""Run access: index, transcripts (paged + SSE live tail), intervention
(inject / pause / resume / abort).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from sse_starlette import EventSourceResponse

from .. import registry
from ..config import load_routine
from ..engine.transcript import read_events
from ..ids import parse_run_id
from ..paths import read_json
from .sse import traced_run_stream

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
    for home in registry.all_homes(server):
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
                     for home in registry.all_homes(server)
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
    if not model:
        # pre-engine boot stub: status.json has no model yet — report the routine's
        # CONFIGURED main model instead of nothing (the run page's widget showed the
        # catalog's first entry as if it were the setting; F166, operator note 2026-07-23)
        cfg, _ = load_routine(run_dir.parent.parent)
        model = (cfg.models.get("main") or "") if cfg is not None else ""
    deliberation = st.get("deliberation") if isinstance(st, dict) else ""
    server = request.app.state.server
    owner = run_dir.parent.parent.parent  # run_dir = <home>/<slug>/runs/<ts>
    home = ("conversation" if owner == server.conversations_home
            else "background" if owner == server.background_home else "routine")
    return {"run_id": info.run_id, "routine": slug, "ts": info.ts, "state": info.state,
            "turn": info.turn, "usage": info.usage, "elapsed_s": info.elapsed_s,
            "question": info.question, "model": model, "deliberation": deliberation or "",
            "summary": info.summary, "updated": info.updated, "subruns": subs,
            "home": home}


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
    return EventSourceResponse(traced_run_stream(run_dir, offset, request.app.state.server))


@router.get("/runs/{run_id}/phases")
def run_phases(request: Request, run_id: str) -> dict:
    """Per-phase instrumentation (turns / tokens / cost / wall-clock) derived from the
    run's transcript — the state-graph rail's numbers.
    """
    from ..readmodels.statemap import phase_stats

    _, run_dir = _run_dir(request, run_id)
    return {"phases": phase_stats(run_dir)}


@router.get("/runs/{run_id}/files")
def run_files(request: Request, run_id: str) -> dict:
    """Which files the run read and wrote — per-path counts derived from the transcript
    (subruns and user slash commands included) — the rail's file-activity card.
    """
    from ..readmodels.fileactivity import file_activity

    _, run_dir = _run_dir(request, run_id)
    hist = run_dir / "history"
    history = (sorted(p.name for p in hist.iterdir() if p.is_file())
               if hist.is_dir() else [])
    return {"files": file_activity(run_dir), "history": history}


@router.get("/runs/{run_id}/file")
def run_file(request: Request, run_id: str, path: str):
    """Serve ONE file from the files card / history list raw — the rail fetches it with
    the auth header and renders/downloads from a blob URL (the artifact panels' pattern).
    Scope: the RUN dir (history/, sub/, result.md) and its owning routine/conversation
    dir — the two trees a card row's relative path can resolve against. A row naming a
    path outside both (files a run touched under an fs-root grant) is listed but not
    served: the 400 names the boundary instead of opening an arbitrary-file read
    through the web tier.
    """
    import mimetypes

    from fastapi.responses import FileResponse

    from ..paths import within

    _, run_dir = _run_dir(request, run_id)
    routine_dir = run_dir.parent.parent
    rel = Path(path)
    candidates = [rel] if rel.is_absolute() else [routine_dir / rel, run_dir / rel]
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if not (within(run_dir, resolved) or within(routine_dir, resolved)):
            continue
        if resolved.is_file():
            media = mimetypes.guess_type(resolved.name)[0] or "text/plain"
            return FileResponse(resolved, media_type=media, filename=resolved.name)
    if rel.is_absolute():
        raise HTTPException(400, "only files under the run and its routine directory "
                                 f"are served — {path!r} is outside both")
    raise HTTPException(404, f"no file {path!r} under the run or its routine directory")


@router.get("/runs/{run_id}/tree")
def run_tree(request: Request, run_id: str) -> dict:
    """The recursive task tree: this run's sequential subtasks + parallel subruns, each a node
    with mode / state / live turns / allotted budget and its own children nested. A read-model
    over the on-disk sub/ transcripts (nothing is written) — the rail's decomposition view.
    """
    from ..readmodels.tasktree import build_tree

    _, run_dir = _run_dir(request, run_id)
    return {"tree": build_tree(run_dir)}


@router.get("/runs/{run_id}/plan")
def run_plan(request: Request, run_id: str) -> dict:
    """The run's WORKING PLAN (state/plan.md) — the living decomposition a run maintains,
    surfaced as an always-visible strip on the run view. This is the SAME store the engine
    inlines into the prompt (engine/composer.py); the strip reuses it rather than adding a
    contract. Read fresh each call so the strip tracks the run's edits. Empty when the run
    keeps no plan — a scheduled routine whose spine is its compiled recipe, or a plan the
    run deleted at finish. The owning routine/conversation dir is run_dir.parent.parent.
    """
    _, run_dir = _run_dir(request, run_id)
    plan = run_dir.parent.parent / "state" / "plan.md"
    text = plan.read_text(encoding="utf-8").strip() if plan.is_file() else ""
    return {"plan": text}


