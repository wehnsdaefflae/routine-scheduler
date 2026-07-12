"""The new-routine wizard routes: a clarify-instruction engine run in a dot-hidden
pseudo-routine dir (identical engine path, invisible to the registry), then suggest →
finalize/scaffold. Session persistence and snapshots live in wizard_store."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from ..daemon.runner import abort_process
from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from ..workflows.generate import generate
from ..workflows.scaffold import scaffold
from ..workflows.suggest import normalize_tags, suggest_tags
from . import wizard_store
from .sse import run_stream

router = APIRouter(tags=["wizard"])


def _wizard_dir(request: Request, wid: str) -> Path:
    d = request.app.state.server.routines_home / wid
    if not wid.startswith(".wizard-") or not d.is_dir():
        raise HTTPException(404, f"no wizard session {wid!r}")
    return d


@router.get("/wizard")
def wizard_list(request: Request) -> list[dict]:
    """Every in-flight new-routine session (the hidden .wizard-* dirs), newest first — so the UI
    can surface + resume them instead of only tracking one in memory."""
    return wizard_store.list_sessions(request.app.state)


@router.get("/wizard/{wid}")
def wizard_detail(request: Request, wid: str) -> dict:
    return wizard_store.snapshot(request.app.state, _wizard_dir(request, wid))


@router.delete("/wizard/{wid}")
async def wizard_cancel(request: Request, wid: str) -> dict:
    """Cancel a session: stop the clarify engine process and move the dir out of the way so it
    stops showing as in-flight (mirrors finalize's archive move — no dangling process or dir)."""
    d = _wizard_dir(request, wid)
    sess = wizard_store.sessions(request.app.state).pop(wid, None)
    proc = (sess or {}).get("proc")
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
    ts = (sess or {}).get("run_ts") or wizard_store.latest_run_ts(d)
    if ts and (d / "runs" / ts).is_dir():
        st = read_json(d / "runs" / ts / "status.json")
        await abort_process(st.get("pid") if isinstance(st, dict) else None, d / "runs" / ts, f"{wid}:{ts}")
    await asyncio.to_thread(wizard_store.archive_session, request.app.state.server.routines_home,
                            d, f"{wid.lstrip('.')}-canceled")
    return {"ok": True}


class StartBody(BaseModel):
    draft: str


@router.post("/wizard/start")
async def start(request: Request, body: StartBody) -> dict:
    if not body.draft.strip():
        raise HTTPException(400, "empty draft instruction")
    server = request.app.state.server
    # session creation is all disk writes plus a full library read (candidates) — off the loop
    wid, ts, d = await asyncio.to_thread(wizard_store.create_session, server, body.draft)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rsched.cli", "engine-run", str(d), "--run-ts", ts,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True, cwd=str(d))
    wizard_store.sessions(request.app.state)[wid] = {"proc": proc, "run_ts": ts, "dir": d}
    return {"wid": wid, "run_ts": ts}


def _clarify_run_dir(request: Request, wid: str) -> Path:
    d = _wizard_dir(request, wid)
    sess = wizard_store.sessions(request.app.state).get(wid) or {}
    ts = sess.get("run_ts") or wizard_store.latest_run_ts(d)
    if ts is None:
        raise HTTPException(404, "wizard session has no run")
    return d / "runs" / ts


@router.get("/wizard/{wid}/events")
async def events(request: Request, wid: str, offset: int = 0):
    return EventSourceResponse(run_stream(_clarify_run_dir(request, wid), offset))


@router.get("/wizard/{wid}/transcript")
def wizard_transcript(request: Request, wid: str, offset: int = 0) -> dict:
    """Paged clarify-chat transcript (mirrors /runs/{id}/transcript) — the byte offset it
    returns is what the UI resumes its SSE tail from after a dropped connection."""
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


@router.post("/wizard/{wid}/suggest")
def wizard_suggest(request: Request, wid: str) -> dict:
    from ..workflows.suggest import suggest_traits_permissions

    d = _wizard_dir(request, wid)
    result = wizard_store.read_result(d)
    if not isinstance(result, dict) or not result.get("refined_instruction"):
        raise HTTPException(409, "the clarify run has not produced state/wizard_result.json yet")
    server = request.app.state.server
    suggested_tags = suggest_tags(server, result["refined_instruction"])
    # The clarifier already suggested a pattern (it read the candidates and married the task to one).
    # Lead the pick list with its choice so the wizard pre-selects it; the rest are override options.
    choice = result.get("workflow_choice") if isinstance(result.get("workflow_choice"), dict) else {}
    chosen = str(choice.get("slug") or "")
    suggestions = [{"slug": w["slug"],
                    "confidence": 1.0 if w["slug"] == chosen else 0.5,
                    "reason": "chosen by the clarifier" if w["slug"] == chosen else w.get("description", "")}
                   for w in wizard_store.candidate_patterns(server)]
    suggestions.sort(key=lambda s: -s["confidence"])
    none_fit = bool(choice.get("generate"))
    # Preselect the routine's traits (practice modules, adapted in at creation) and
    # permissions (engine-enforced) from the refined task + the chosen pattern — shown on
    # the create page as an editable preselection.
    tp = suggest_traits_permissions(server, result["refined_instruction"], chosen)
    return {"wizard_result": result, "suggested_tags": suggested_tags, "suggestions": suggestions,
            "suggested_traits": tp["traits"], "suggested_permissions": tp["permissions"],
            "none_fit": none_fit, "new_workflow_hint": str(choice.get("hint") or "")}


class GenerateBody(BaseModel):
    hint: str = ""


@router.post("/wizard/{wid}/generate-workflow")
def wizard_generate(request: Request, wid: str, body: GenerateBody) -> dict:
    d = _wizard_dir(request, wid)
    result = wizard_store.read_result(d)
    if not isinstance(result, dict):
        raise HTTPException(409, "no wizard result yet")
    try:
        slug, note = generate(request.app.state.server, result["refined_instruction"], body.hint)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"workflow_slug": slug, "note": note, "status": "draft"}


class FinalizeBody(BaseModel):
    slug: str
    name: str
    workflow_slug: str
    instruction: str = ""         # user-edited refined instruction; empty = the clarifier's verbatim
    description: str = ""         # one-line UI summary; defaults to the clarifier's, then the name
    models: dict | None = None    # {main|subroutine|tool_call: {endpoint, model}} picked in the wizard
    friendly: dict = {}          # friendly schedule spec → cron + server tz
    params: dict = {}
    tags: list[str] = []         # >=3 tags, suggested (reuse-first) then user-editable
    traits: list[str] | None = None       # practice modules to adapt in (None → workflow defaults)
    permissions: list[str] | None = None  # engine-enforced capabilities (None → defaults)
    run_now: bool = False


@router.post("/wizard/{wid}/finalize")
async def finalize(request: Request, wid: str, body: FinalizeBody) -> dict:
    """Kick off the routine build in the BACKGROUND and return immediately — building calls
    decompose(), a blocking LLM step that can take a minute or two. Progress is tracked in
    state/finalize.json (building | done | error) so the client (or a reloaded one) can poll
    /wizard/{wid}; a bus event announces completion. Fast, obvious errors are still returned here."""
    from .. import schedule

    d = _wizard_dir(request, wid)
    server = request.app.state.server
    result = wizard_store.read_result(d)
    if not isinstance(result, dict) or not result.get("refined_instruction"):
        raise HTTPException(409, "no refined instruction to finalize")
    if (server.routines_home / body.slug).exists():
        raise HTTPException(409, f"a routine {body.slug!r} already exists — pick another slug")
    try:
        schedule.friendly_to_cron(body.friendly or {"frequency": "manual"})
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"invalid schedule: {exc}") from exc
    atomic_write_json(d / "state" / "finalize.json", {"state": "building", "slug": body.slug})
    wizard_store.sessions(request.app.state).pop(wid, None)  # the clarify process is done
    asyncio.create_task(_build_routine(request.app.state, wid, d, body, result))
    return {"building": True, "slug": body.slug, "wid": wid}


async def _build_routine(app_state, wid: str, d: Path, body: "FinalizeBody", result: dict) -> None:
    """Background: scaffold the routine (the slow decompose call), fire the first run if asked, and
    record the outcome to state/finalize.json + a bus event. Errors leave the session recoverable."""
    from .. import schedule
    from ..config import load_routine

    server, scheduler, runner, bus = (app_state.server, app_state.scheduler,
                                      app_state.runner, app_state.bus)
    status_path = d / "state" / "finalize.json"
    try:
        cron = schedule.friendly_to_cron(body.friendly or {"frequency": "manual"})
        steps = result.get("steps") if isinstance(result.get("steps"), dict) else None
        description = body.description.strip() or str(result.get("description") or "").strip()
        params = body.params or (result.get("params") if isinstance(result.get("params"), dict) else {})
        routine_dir = await asyncio.to_thread(
            scaffold, server, slug=body.slug, name=body.name,
            instruction=body.instruction.strip() or result["refined_instruction"],
            workflow_slug=body.workflow_slug, cron=cron,
            tz=schedule.server_tz(), params=params, steps=steps, description=description,
            models=body.models, tags=normalize_tags(body.tags) or None,
            traits=body.traits, permissions=body.permissions)
    except Exception as exc:   # scaffold/decompose failure — the session stays so the user can retry
        partial = server.routines_home / body.slug   # clean up a half-built dir so the retry isn't blocked
        if partial.is_dir() and not (partial / "routine.yaml").exists():
            shutil.rmtree(partial, ignore_errors=True)
        atomic_write_json(status_path, {"state": "error", "slug": body.slug, "error": str(exc)[:300]})
        bus.publish({"event": "routine_failed", "wid": wid, "slug": body.slug, "error": str(exc)[:300]})
        return

    def keep_provenance() -> None:
        # the clarify conversation stays inside the new routine (transcripts can be large — off-loop)
        provenance = routine_dir / "state" / "wizard"
        provenance.mkdir(parents=True, exist_ok=True)
        ts = wizard_store.latest_run_ts(d)
        if ts and (d / "runs" / ts / "transcript.jsonl").exists():
            (provenance / "clarify-transcript.jsonl").write_bytes(
                (d / "runs" / ts / "transcript.jsonl").read_bytes())

    await asyncio.to_thread(keep_provenance)
    scheduler.rescan()
    run_id = None
    if body.run_now:
        cfg, _ = load_routine(routine_dir)
        if cfg:
            run_id = await runner.fire(cfg, reason="wizard")
    atomic_write_json(status_path, {"state": "done", "slug": body.slug, "run_id": run_id})
    bus.publish({"event": "routine_created", "wid": wid, "slug": body.slug, "run_id": run_id})
    # archive the finished session (also excluded from the in-flight list via its 'done' state)
    await asyncio.to_thread(wizard_store.archive_session, server.routines_home, d, wid.lstrip("."))
