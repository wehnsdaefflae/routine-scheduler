"""Run access: index, transcripts (paged + SSE live tail), intervention
(inject / pause / resume / abort).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from .. import registry
from ..config import DELIBERATION_LEVELS, load_routine
from ..daemon.runner import abort_process
from ..engine.transcript import read_events
from ..ids import now_iso, parse_run_id
from ..paths import atomic_write_json, read_json
from ..registry import TERMINAL_STATES
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
    for home in registry.all_homes(server):
        run_dir = home / slug / "runs" / ts
        if run_dir.is_dir():
            return slug, run_dir
    raise HTTPException(404, f"no run {run_id!r}")


def merge_control(run_dir: Path, updates: dict) -> None:
    """Merge `updates` into the run's web-owned control.json (read-modify-write, atomic).
    ONE writer path for every mid-run signal — pause, switch_model, set_deliberation,
    add_traits — so no endpoint can drop a sibling's pending signal.
    """
    ctrl = read_json(run_dir / "control.json")
    ctrl = dict(ctrl) if isinstance(ctrl, dict) else {}
    ctrl.update(updates)
    atomic_write_json(run_dir / "control.json", ctrl)


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
    return EventSourceResponse(run_stream(run_dir, offset))


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
    return {"files": file_activity(run_dir)}


@router.get("/runs/{run_id}/tree")
def run_tree(request: Request, run_id: str) -> dict:
    """The recursive task tree: this run's sequential subtasks + parallel subruns, each a node
    with mode / state / live turns / allotted budget and its own children nested. A read-model
    over the on-disk sub/ transcripts (nothing is written) — the rail's decomposition view.
    """
    from ..readmodels.tasktree import build_tree

    _, run_dir = _run_dir(request, run_id)
    return {"tree": build_tree(run_dir)}


class Inject(BaseModel):
    text: str


@router.post("/runs/{run_id}/inject")
def inject(request: Request, run_id: str, body: Inject) -> dict:
    _, run_dir = _run_dir(request, run_id)
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    from . import wizard_store

    inbox = wizard_store.session_inbox_dir(request.app.state.server, run_dir)
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    atomic_write_json(inbox / f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json",
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
    from . import wizard_store

    inbox = wizard_store.session_inbox_dir(request.app.state.server, run_dir)
    atomic_write_json(inbox / f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json",
                      {"text": body.text, "ts": now_iso(), "via": "web-converse"})
    st = read_json(run_dir / "status.json")
    state = st.get("state") if isinstance(st, dict) else None
    if state not in TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run"}
    from ..config import load_routine
    from .routines_common import guard_template
    guard_template(slug, "clarify sessions are driven by the wizard, never resumed directly")
    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    rid = await request.app.state.runner.resume_terminal(cfg, run_dir.name, reason="converse")
    if not rid:
        raise HTTPException(409, "could not resume — another run of this routine is active, "
                                 "or the daemon is draining")
    return {"ok": True, "delivery": "resumed", "run_id": rid}


def _revise_message(instruction: str) -> str:
    """The framed directive injected into the resumed run — it pivots the orchestrator from
    its finished task to editing its own recipe, and routes config-shaped asks to ask_user.
    """
    return (
        "REVISE YOUR OWN RECIPE — this message asks you to change THIS routine's recipe "
        "files, not to continue its task.\n\n"
        f"The user wants:\n{instruction}\n\n"
        "Your recipe is main.md, the stages/ modules, traits/, and tuning.yaml, in this "
        "routine's own directory. Read the relevant file(s) (and this run's own transcript "
        "for context), make the change with edit_file/write_file, verify by reading it back, "
        "add a one-line note to LEDGER.md, then finish with a short summary of what you "
        "changed. Change nothing else.\n\n"
        "If the request is actually about CONFIG — the schedule, budgets, models, "
        "permissions/capabilities, or filesystem roots (i.e. routine.yaml) — you CANNOT edit "
        "that here; instead call ask_user with a config_patch: the exact change as a "
        'PATCH /routines body (e.g. {"budgets": {"max_turns": 100}}), so the user can one-click '
        "apply it from the Decisions page.")


@router.post("/runs/{run_id}/revise")
async def revise(request: Request, run_id: str, body: Inject) -> dict:
    """Revise this routine's OWN recipe from the run view ("Revise recipe"): inject the framed
    instruction and resume the finished run with a run-scoped recipe self-write grant
    (engine/revise.py) so the orchestrator can edit main.md / stages / traits / tuning.yaml.
    Routines only (a conversation's recipe is the fixed converse workflow; background tasks are
    ephemeral), and only once the run has finished.
    """
    slug, run_dir = _run_dir(request, run_id)
    if not body.text.strip():
        raise HTTPException(400, "empty revision request")
    from ..paths import within

    server = request.app.state.server
    routine_dir = run_dir.parent.parent
    if not within(server.routines_home, routine_dir):
        raise HTTPException(400, "revise-recipe applies to routines only")
    from .routines_common import guard_template
    guard_template(slug, "the clarification template's recipe is fixed")
    st = read_json(run_dir / "status.json")
    if (st.get("state") if isinstance(st, dict) else None) not in TERMINAL_STATES:
        raise HTTPException(409, "revise the recipe once the run has finished")
    from ..config import load_routine
    from ..engine.revise import write_revise_marker
    from . import wizard_store

    cfg, _ = load_routine(routine_dir)
    if cfg is None:
        raise HTTPException(404, f"routine {slug!r} not found")
    write_revise_marker(run_dir, body.text.strip())
    inbox = wizard_store.session_inbox_dir(server, run_dir)
    atomic_write_json(inbox / f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json",
                      {"text": _revise_message(body.text.strip()), "ts": now_iso(),
                       "via": "web-revise"})
    rid = await request.app.state.runner.resume_terminal(cfg, run_dir.name, reason="revise")
    if not rid:
        raise HTTPException(409, "could not start the revision — another run of this routine "
                                 "is active, or the daemon is draining")
    return {"ok": True, "run_id": rid}


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
    merge_control(run_dir, {"pause": value, "ts": now_iso()})
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
    merge_control(run_dir, {"switch_model": {body.kind: body.model, "ts": now_iso()}})
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
    st = read_json(run_dir / "status.json")
    if (st.get("state") if isinstance(st, dict) else None) in TERMINAL_STATES:
        raise HTTPException(409, "run is not active; nothing to switch")
    merge_control(run_dir, {"set_deliberation": {"level": body.level, "ts": now_iso()}})
    return {"ok": True, "switch": f"deliberation → {body.level}"}


@router.post("/runs/{run_id}/resume-run")
async def resume_run(request: Request, run_id: str) -> dict:
    """Resume an interrupted run in place: re-spawn the engine on the SAME run dir, rehydrating its
    transcript so it continues where it left off (fresh budget window). Only terminal runs.
    """
    slug, run_dir = _run_dir(request, run_id)
    from .routines_common import guard_template
    guard_template(slug, "clarify sessions are driven by the wizard, never resumed directly")
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


async def abort_with_fallback(runner, slug: str, run_dir: Path, run_id: str) -> bool:
    """Abort via the runner (daemon-owned runs) with a recorded-pid fallback for runs the
    daemon doesn't track (a CLI run, a pre-restart orphan) — the ONE abort sequence the
    run, conversation, and background endpoints all share.
    """
    if await runner.abort(slug):
        return True
    st = read_json(run_dir / "status.json")
    pid = st.get("pid") if isinstance(st, dict) else None
    return await abort_process(pid, run_dir, run_id)


@router.post("/runs/{run_id}/abort")
async def abort(request: Request, run_id: str) -> dict:
    slug, run_dir = _run_dir(request, run_id)
    if not await abort_with_fallback(request.app.state.runner, slug, run_dir, run_id):
        raise HTTPException(409, "no live process for this run")
    return {"ok": True}
