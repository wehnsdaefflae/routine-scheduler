"""The new-routine wizard: a clarify-instruction engine run in a dot-hidden pseudo-routine
dir (identical engine path, invisible to the registry), then suggest → finalize/scaffold."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import DEFAULT_SELF
from ..ids import now_iso, run_ts as make_run_ts
from ..paths import atomic_write_json
from ..schema_guard import loads_tolerant
from ..workflows.generate import generate
from ..workflows.scaffold import GITIGNORE, scaffold
from ..workflows.suggest import normalize_tags, suggest, suggest_tags
from .sse import run_stream, sse_response

router = APIRouter(tags=["wizard"])

WIZARD_BUDGETS = {"max_turns": 25, "max_wall_clock_min": 30, "max_total_tokens": 200_000,
                  "max_subruns": 0, "max_subrun_depth": 0, "ask_timeout_h": 2}


def _read_wizard_result(d: Path) -> dict | None:
    """wizard_result.json is LLM-authored — read it tolerantly (control chars in strings)."""
    try:
        obj = loads_tolerant((d / "state" / "wizard_result.json").read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def _wizards(request: Request) -> dict:
    if not hasattr(request.app.state, "wizards"):
        request.app.state.wizards = {}
    return request.app.state.wizards


def _wizard_dir(request: Request, wid: str) -> Path:
    d = request.app.state.server.routines_home / wid
    if not wid.startswith(".wizard-") or not d.is_dir():
        raise HTTPException(404, f"no wizard session {wid!r}")
    return d


class StartBody(BaseModel):
    draft: str


@router.post("/wizard/start")
async def start(request: Request, body: StartBody) -> dict:
    if not body.draft.strip():
        raise HTTPException(400, "empty draft instruction")
    server = request.app.state.server
    ts = make_run_ts()
    wid = f".wizard-{ts}"
    slug = f"wizard-{ts}"
    d = server.routines_home / wid
    (d / "state").mkdir(parents=True)
    (d / "inbox").mkdir()
    # Clarify is HARD-WIRED (not a library workflow) — internal machinery, tools disabled.
    main_content = (Path(__file__).resolve().parents[1] / "clarify.md").read_text(encoding="utf-8")
    (d / "main.md").write_text(main_content, encoding="utf-8")
    (d / "instruction.md").write_text(body.draft.rstrip() + "\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER — wizard session\n", encoding="utf-8")
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "name": "New-routine wizard", "slug": slug, "enabled": False,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "clarify-instruction", "library_commit": ""},
        "budgets": WIZARD_BUDGETS,
        "self": {k: False for k in DEFAULT_SELF},
    }, sort_keys=False), encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rsched.cli", "engine-run", str(d), "--run-ts", ts,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True, cwd=str(d))
    _wizards(request)[wid] = {"proc": proc, "run_ts": ts, "dir": d}
    return {"wid": wid, "run_ts": ts}


@router.get("/wizard/{wid}/events")
async def events(request: Request, wid: str):
    d = _wizard_dir(request, wid)
    sessions = _wizards(request)
    ts = (sessions.get(wid) or {}).get("run_ts") or _latest_run_ts(d)
    if ts is None:
        raise HTTPException(404, "wizard session has no run")
    return sse_response(run_stream(d / "runs" / ts))


class AnswerBody(BaseModel):
    qid: str
    text: str


@router.post("/wizard/{wid}/answer")
def answer(request: Request, wid: str, body: AnswerBody) -> dict:
    d = _wizard_dir(request, wid)
    atomic_write_json(d / "inbox" / f"answer-{body.qid}.json",
                      {"qid": body.qid, "text": body.text, "source": "wizard", "ts": now_iso()})
    return {"ok": True}


@router.post("/wizard/{wid}/suggest")
def wizard_suggest(request: Request, wid: str) -> dict:
    d = _wizard_dir(request, wid)
    result = _read_wizard_result(d)
    if not isinstance(result, dict) or not result.get("refined_instruction"):
        raise HTTPException(409, "the clarify run has not produced state/wizard_result.json yet")
    server = request.app.state.server
    ranking = suggest(server, result["refined_instruction"])
    suggested_tags = suggest_tags(server, result["refined_instruction"])
    return {"wizard_result": result, "suggested_tags": suggested_tags, **ranking}


class GenerateBody(BaseModel):
    hint: str = ""


@router.post("/wizard/{wid}/generate-workflow")
def wizard_generate(request: Request, wid: str, body: GenerateBody) -> dict:
    d = _wizard_dir(request, wid)
    result = _read_wizard_result(d)
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
    friendly: dict = {}          # friendly schedule spec → cron + server tz
    params: dict = {}
    tags: list[str] = []         # >=3 tags, suggested (reuse-first) then user-editable
    fragments: list[str] | None = None   # standards picked on the draft page (None → workflow defaults)
    run_now: bool = False


@router.post("/wizard/{wid}/finalize")
async def finalize(request: Request, wid: str, body: FinalizeBody) -> dict:
    from .. import schedule

    d = _wizard_dir(request, wid)
    server = request.app.state.server
    result = _read_wizard_result(d)
    if not isinstance(result, dict) or not result.get("refined_instruction"):
        raise HTTPException(409, "no refined instruction to finalize")
    try:
        cron = schedule.friendly_to_cron(body.friendly or {"frequency": "manual"})
        playbook = result.get("playbook") if isinstance(result.get("playbook"), dict) else None
        routine_dir = scaffold(server, slug=body.slug, name=body.name,
                               instruction=result["refined_instruction"],
                               workflow_slug=body.workflow_slug, cron=cron,
                               tz=schedule.server_tz(), params=body.params, playbook=playbook,
                               tags=normalize_tags(body.tags) or None, fragments=body.fragments)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    # keep the wizard conversation as provenance inside the new routine
    provenance = routine_dir / "state" / "wizard"
    provenance.mkdir(parents=True, exist_ok=True)
    ts = (_wizards(request).pop(wid, None) or {}).get("run_ts") or _latest_run_ts(d)
    if ts and (d / "runs" / ts / "transcript.jsonl").exists():
        (provenance / "clarify-transcript.jsonl").write_bytes(
            (d / "runs" / ts / "transcript.jsonl").read_bytes())
    import shutil

    archive = server.routines_home / ".archive"
    archive.mkdir(exist_ok=True)
    shutil.move(str(d), str(archive / wid.lstrip(".")))
    request.app.state.scheduler.rescan()
    run_id = None
    if body.run_now:
        from ..config import load_routine

        cfg, _ = load_routine(routine_dir)
        if cfg:
            run_id = await request.app.state.runner.fire(cfg, reason="wizard")
    return {"ok": True, "slug": body.slug, "run_id": run_id}


def _latest_run_ts(d: Path) -> str | None:
    runs = sorted((d / "runs").glob("*")) if (d / "runs").is_dir() else []
    return runs[-1].name if runs else None
