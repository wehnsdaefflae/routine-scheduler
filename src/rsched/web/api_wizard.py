"""The new-routine wizard: a clarify-instruction engine run in a dot-hidden pseudo-routine
dir (identical engine path, invisible to the registry), then suggest → finalize/scaffold."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..daemon.runner import _pid_alive, abort_process
from ..ids import now_iso, run_ts as make_run_ts
from ..paths import atomic_write_json, read_json
from ..schema_guard import loads_tolerant
from ..workflows.adapt import materialize
from ..workflows.generate import generate
from ..workflows.scaffold import GITIGNORE, scaffold
from ..workflows.suggest import normalize_tags, suggest_tags
from .sse import run_stream, sse_response

router = APIRouter(tags=["wizard"])

WIZARD_BUDGETS = {"max_turns": 25, "max_wall_clock_min": 30, "max_total_tokens": 200_000,
                  "max_subruns": 0, "max_subrun_depth": 0, "ask_timeout_h": 2}

TERMINAL = ("finished", "failed", "aborted")


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


def _wizard_meta(d: Path) -> dict:
    obj = read_json(d / "state" / "wizard_meta.json")
    return obj if isinstance(obj, dict) else {}


def _draft_preview(d: Path) -> str:
    try:
        text = (d / "instruction.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:140] + ("…" if len(text) > 140 else "")


def _snapshot(request: Request, d: Path) -> dict:
    """Reconstruct a wizard session's live state from disk — works in a fresh process that never
    saw it (after a reload / daemon restart). The stage is derived, not stored, so it is always
    consistent with what is actually on disk:
      chat    → clarify run is live and has not produced a result yet
      suggest → state/wizard_result.json holds a refined instruction (ready to pick + create)
      error   → the clarify run reached a terminal state without ever producing a result
    """
    meta = _wizard_meta(d)
    result = _read_wizard_result(d)
    has_result = isinstance(result, dict) and bool(result.get("refined_instruction"))
    ts = (_wizards(request).get(d.name) or {}).get("run_ts") or meta.get("run_ts") or _latest_run_ts(d)
    run = registry.read_run(d / "runs" / ts, d.name.lstrip(".")) if ts and (d / "runs" / ts).is_dir() else None
    state = run.state if run else "unknown"
    stage = "suggest" if has_result else ("error" if state in TERMINAL else "chat")
    # only meaningful while clarifying — a dead pid there means the session is stuck (needs cancel)
    alive = None if (stage != "chat" or run is None) else _pid_alive(run.pid)
    return {"wid": d.name, "run_ts": ts, "created": meta.get("created", ""),
            "fragments": meta.get("fragments", []), "draft": _draft_preview(d),
            "stage": stage, "state": state, "has_result": has_result,
            "question": run.question if run else None, "alive": alive}


@router.get("/wizard")
def wizard_list(request: Request) -> list[dict]:
    """Every in-flight new-routine session (the hidden .wizard-* dirs), newest first — so the UI
    can surface + resume them instead of only tracking one in memory."""
    home = request.app.state.server.routines_home
    out: list[dict] = []
    if home.is_dir():
        for d in sorted(home.glob(".wizard-*"), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            try:
                out.append(_snapshot(request, d))
            except Exception:  # a half-written dir must never break the list
                continue
    return out


@router.get("/wizard/{wid}")
def wizard_detail(request: Request, wid: str) -> dict:
    return _snapshot(request, _wizard_dir(request, wid))


@router.delete("/wizard/{wid}")
async def wizard_cancel(request: Request, wid: str) -> dict:
    """Cancel a session: stop the clarify engine process and move the dir out of the way so it
    stops showing as in-flight (mirrors finalize's archive move — no dangling process or dir)."""
    d = _wizard_dir(request, wid)
    sess = _wizards(request).pop(wid, None)
    proc = (sess or {}).get("proc")
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
    ts = (sess or {}).get("run_ts") or _latest_run_ts(d)
    if ts and (d / "runs" / ts).is_dir():
        st = read_json(d / "runs" / ts / "status.json")
        await abort_process(st.get("pid") if isinstance(st, dict) else None, d / "runs" / ts, f"{wid}:{ts}")
    archive = request.app.state.server.routines_home / ".archive"
    archive.mkdir(exist_ok=True)
    dest = archive / f"{wid.lstrip('.')}-canceled"
    if dest.exists():
        dest = archive / f"{wid.lstrip('.')}-canceled-{make_run_ts()}"
    shutil.move(str(d), str(dest))
    return {"ok": True}


class StartBody(BaseModel):
    draft: str
    fragments: list[str] = []   # standards chosen on the draft page; persisted for resume + finalize


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
    # Clarify is a normal LIBRARY workflow (clarify-instruction). Materialize it — no decompose LLM,
    # just the whole workflow as main.md — so the wizard runs the ordinary engine path. Its
    # frontmatter `tools:` allowlist is what keeps the clarifier to ask/read/write/finish.
    try:
        main_content, prov = materialize(server.library_home, "clarify-instruction")
        commit = prov.get("commit", "")
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(503, f"clarify-instruction workflow not in the library: {exc}") from exc
    (d / "main.md").write_text(main_content, encoding="utf-8")
    (d / "instruction.md").write_text(body.draft.rstrip() + "\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER — wizard session\n", encoding="utf-8")
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "name": "New-routine wizard", "slug": slug, "enabled": False,
        "description": "New-routine clarification wizard session.",
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "clarify-instruction", "library_commit": commit},
        "budgets": WIZARD_BUDGETS,
        "fragments": ["ask-policy"],
    }, sort_keys=False), encoding="utf-8")
    # Persist the session's meta so it survives a daemon/container restart: /api/wizard can list it
    # and finalize can recover the chosen standards without depending on the client or in-memory state.
    atomic_write_json(d / "state" / "wizard_meta.json",
                      {"wid": wid, "run_ts": ts, "created": now_iso(), "fragments": body.fragments})
    _write_candidates(server, d)   # the workflow patterns the clarifier suggests + marries against

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rsched.cli", "engine-run", str(d), "--run-ts", ts,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True, cwd=str(d))
    _wizards(request)[wid] = {"proc": proc, "run_ts": ts, "dir": d}
    return {"wid": wid, "run_ts": ts}


def _candidate_patterns(server) -> list[dict]:
    from ..workflows import library
    return [w for w in library.list_workflows(server.library_home)
            if w.get("status") == "stable" and "meta" not in (w.get("tags") or [])]


def _write_candidates(server, d: Path) -> None:
    """Write the workflow patterns the clarifier chooses from into the session's state/, so it can
    suggest one (and marry the task to it) by reading a single file — its `tools` allowlist permits
    read_file but not library discovery. Each pattern is inlined with its full control flow."""
    from ..workflows import library

    parts = ["# Candidate workflow patterns", "",
             "Pick the ONE whose control flow best fits this task (that is your suggestion), or choose",
             "to generate a new one. A pattern's parameter contract is its dummy imports.", ""]
    for w in _candidate_patterns(server):
        try:
            _, _, raw = library.read_workflow(server.library_home, w["slug"])
        except FileNotFoundError:
            continue
        lang = "python" if w["file"].endswith(".py") else "markdown"
        parts += [f"## {w['slug']} — {w['description']}", f"when_to_use: {w['when_to_use']}", "",
                  f"```{lang}", raw.strip(), "```", ""]
    (d / "state" / "candidates.md").write_text("\n".join(parts), encoding="utf-8")


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
    suggested_tags = suggest_tags(server, result["refined_instruction"])
    # The clarifier already suggested a pattern (it read the candidates and married the task to one).
    # Lead the pick list with its choice so the wizard pre-selects it; the rest are override options.
    choice = result.get("workflow_choice") if isinstance(result.get("workflow_choice"), dict) else {}
    chosen = str(choice.get("slug") or "")
    suggestions = [{"slug": w["slug"],
                    "confidence": 1.0 if w["slug"] == chosen else 0.5,
                    "reason": "chosen by the clarifier" if w["slug"] == chosen else w.get("description", "")}
                   for w in _candidate_patterns(server)]
    suggestions.sort(key=lambda s: -s["confidence"])
    none_fit = bool(choice.get("generate"))
    return {"wizard_result": result, "suggested_tags": suggested_tags, "suggestions": suggestions,
            "none_fit": none_fit, "new_workflow_hint": str(choice.get("hint") or "")}


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
    description: str = ""         # one-line UI summary; defaults to the clarifier's, then the name
    models: dict | None = None    # {main|subroutine|tool_call: {endpoint, model}} picked in the wizard
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
        steps = result.get("steps") if isinstance(result.get("steps"), dict) else None
        # Always end up with a description: the wizard's chosen text, else the clarifier's
        # one-liner, else the name (scaffold's own final fallback).
        description = body.description.strip() or str(result.get("description") or "").strip()
        # Standards: prefer what the client sent, else recover them from the session meta on disk
        # (so a resumed-after-restart finalize still applies the fragments chosen on the draft page).
        fragments = body.fragments if body.fragments is not None else (_wizard_meta(d).get("fragments") or None)
        # Parameters the clarifier fixed with the user (the pattern's parameter contract) — pass them
        # to decompose so it tailors the routine's steps to the resolved values.
        params = body.params or (result.get("params") if isinstance(result.get("params"), dict) else {})
        # scaffold() calls decompose(), which makes a BLOCKING LLM call (up to 180s). Run it off
        # the event loop or it freezes the whole web server until the model responds.
        routine_dir = await asyncio.to_thread(
            scaffold, server, slug=body.slug, name=body.name,
            instruction=result["refined_instruction"],
            workflow_slug=body.workflow_slug, cron=cron,
            tz=schedule.server_tz(), params=params, steps=steps,
            description=description, models=body.models,
            tags=normalize_tags(body.tags) or None, fragments=fragments)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    # keep the wizard conversation as provenance inside the new routine
    provenance = routine_dir / "state" / "wizard"
    provenance.mkdir(parents=True, exist_ok=True)
    ts = (_wizards(request).pop(wid, None) or {}).get("run_ts") or _latest_run_ts(d)
    if ts and (d / "runs" / ts / "transcript.jsonl").exists():
        (provenance / "clarify-transcript.jsonl").write_bytes(
            (d / "runs" / ts / "transcript.jsonl").read_bytes())
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
