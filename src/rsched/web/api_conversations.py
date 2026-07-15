"""Conversations API: list/create/detail/message/delete, config edits, artifact +
attachment serving.

A conversation is a routine-shaped dir under conversations_home (see conversations.py);
its ONE run is continued in place — a message to a live run is an ordinary injection, a
message to a finished run resumes it (converse semantics). Transcript/SSE/abort ride the
existing /api/runs endpoints (run resolution is home-aware). Attachments upload as
multipart files into <conv>/attachments/ and travel as an `[attached files]` block in the
message text; deliverables the model writes into <conv>/artifacts/ are listed and served
here for the chat's artifact panel."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import conversations as conv_mod
from ..config import MODEL_KINDS, load_routine
from ..daemon import registry
from ..daemon.runner import abort_process
from ..ids import background_task_id, now_iso, run_ts
from ..paths import atomic_write_json, read_json, resolve_rel
from .api_routines import PermissionsBody, resolve_permission_layers
from .sse import TERMINAL_STATES

router = APIRouter(tags=["conversations"])

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_SERVABLE = ("artifacts/", "attachments/")


def _home(request: Request) -> Path:
    return request.app.state.server.conversations_home


def _info(request: Request, slug: str) -> registry.RoutineInfo:
    d = _home(request) / slug
    if not (d / "routine.yaml").exists():
        raise HTTPException(404, f"no conversation {slug!r}")
    cfg, problems = load_routine(d)
    if cfg is None:
        raise HTTPException(500, "; ".join(problems))
    return registry.RoutineInfo(cfg=cfg, problems=problems,
                                runs=registry.run_index(d, cfg.slug),
                                open_questions=[])


def _guard_not_active(request: Request, info: registry.RoutineInfo) -> None:
    if info.active_run or request.app.state.runner.is_active(info.slug):
        raise HTTPException(409, f"conversation {info.slug!r} has an active reply — try again after it")


async def _save_attachments(conv_dir: Path, files: list[UploadFile]) -> list[str]:
    """Store uploads under attachments/ (timestamped, safe basenames); returns the
    conversation-relative paths for the message's attachment block."""
    rels: list[str] = []
    stamp = run_ts()
    for i, f in enumerate(files or []):
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(f.filename or f"file-{i}").name).strip("-.") \
            or f"file-{i}"
        rel = f"attachments/{stamp}-{base}"
        data = await f.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, f"attachment {base!r} exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB")
        (conv_dir / "attachments").mkdir(exist_ok=True)
        (conv_dir / rel).write_bytes(data)
        rels.append(rel)
    return rels


def _snippet(info: registry.RoutineInfo) -> str:
    last = info.last_run
    return (last.summary if last else "").strip().splitlines()[0][:160] if last and last.summary else ""


def _item(info: registry.RoutineInfo) -> dict:
    last = info.last_run
    return {
        "slug": info.slug,
        "title": info.cfg.name,
        "tags": info.cfg.tags,
        "state": last.state if last else "new",
        "updated": (last.updated or last.ts) if last else "",
        "snippet": _snippet(info),
        "run_id": last.run_id if last else None,
        "turns": last.turn if last else 0,
        "usage": last.usage if last else {},
        "question": bool(last and last.question),
    }


@router.get("/conversations")
def list_conversations(request: Request) -> list[dict]:
    catalog = registry.scan(request.app.state.server, _home(request))
    items = [_item(info) for info in catalog.values()]
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


@router.post("/conversations")
async def create_conversation(request: Request, text: str = Form(""),
                              workdir: str = Form(""), endpoint: str = Form(""),
                              model: str = Form(""), effort: str = Form(""),
                              shell: str = Form(""), playbook: str = Form(""),
                              max_turns: str = Form(""), max_total_turns: str = Form(""),
                              files: list[UploadFile] = File(default=[])) -> dict:
    server = request.app.state.server
    if not text.strip() and not playbook.strip():
        raise HTTPException(400, "empty message — write the first message or pick a playbook")
    # Optional pre-start budgets: max_turns = turns per REPLY, max_total_turns = cumulative
    # cap over the WHOLE conversation (both -1 = unlimited). Blank = leave the default.
    budgets: dict[str, int] = {}
    for key, raw_val in (("max_turns", max_turns), ("max_total_turns", max_total_turns)):
        if raw_val.strip():
            try:
                budgets[key] = int(raw_val)
            except ValueError:
                raise HTTPException(400, f"{key} must be a whole number (-1 = unlimited)") from None
    permissions = (conv_mod.CONVERSATION_PERMISSIONS + ["shell"]) if shell.strip() else None
    models = None
    if endpoint or model:
        if endpoint not in server.endpoints or not model.strip():
            raise HTTPException(400, "model override needs a configured endpoint and a model id")
        ref = {"endpoint": endpoint, "model": model.strip()}
        if effort.strip():
            ref["effort"] = effort.strip()
        models = {"main": ref, "subroutine": dict(ref), "tool_call": dict(ref)}
    server.conversations_home.mkdir(parents=True, exist_ok=True)
    slug = conv_mod.new_slug(server.conversations_home)
    try:
        conv_dir = conv_mod.create_conversation(server, slug=slug, first_message=text,
                                                workdir=workdir, models=models,
                                                permissions=permissions,
                                                playbook_slug=playbook.strip(),
                                                budgets=budgets or None)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"the library has no '{conv_mod.CONVERSE_WORKFLOW}' workflow "
                                 f"— restart the daemon to seed it ({exc})") from exc
    rels = await _save_attachments(conv_dir, files)
    if rels:
        instruction = (conv_dir / "instruction.md").read_text(encoding="utf-8")
        (conv_dir / "instruction.md").write_text(
            instruction.rstrip() + conv_mod.attachment_note(rels) + "\n", encoding="utf-8")
        # the engine reads this at boot to auto-attach the first message's image/PDF
        # attachments to the kickoff (later messages carry attachments through the inbox)
        atomic_write_json(conv_dir / "state" / "pending-media.json", {"attachments": rels})
    cfg, _ = load_routine(conv_dir)
    rid = await request.app.state.runner.fire(cfg, reason="conversation")
    if rid is None:
        raise HTTPException(409, "could not start the conversation (daemon draining?)")
    # title + tags off the reply path — best-effort, never blocks the response
    asyncio.create_task(asyncio.to_thread(conv_mod.autolabel, server, conv_dir, text))
    return {"slug": slug, "run_id": rid}


@router.post("/conversations/{slug}/message")
async def message(request: Request, slug: str, text: str = Form(...),
                  files: list[UploadFile] = File(default=[])) -> dict:
    """Append a user message (with optional attachments): a live reply picks it up at the
    next turn boundary; a finished conversation is resumed in place."""
    info = _info(request, slug)
    if not text.strip():
        raise HTTPException(400, "empty message")
    conv_dir = info.cfg.dir
    rels = await _save_attachments(conv_dir, files)
    full = text.rstrip() + conv_mod.attachment_note(rels)
    atomic_write_json(conv_dir / "inbox" / f"msg-{now_iso().replace(':', '')}.json",
                      {"text": full, "ts": now_iso(), "via": "conversation",
                       **({"attachments": rels} if rels else {})})
    last = info.last_run
    if last and last.state not in TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run", "run_id": last.run_id}
    runner = request.app.state.runner
    rid = (await runner.resume(info.cfg, last.ts, reason="converse") if last
           else await runner.fire(info.cfg, reason="conversation"))
    if not rid:
        raise HTTPException(409, "could not wake the conversation (draining, or a reply just started)")
    return {"ok": True, "delivery": "resumed", "run_id": rid}


@router.get("/conversations/{slug}")
def detail(request: Request, slug: str) -> dict:
    from .. import library_docs

    info = _info(request, slug)
    server = request.app.state.server
    from ..grants import EMPTY_CAPABILITIES, GATED_KINDS

    all_perms = library_docs.list_docs(server.permissions_home)
    held = set(info.cfg.permissions)
    permissions = [{"slug": p["slug"], "summary": p["summary"], "title": p["title"],
                    "requires": p["requires"], "active": p["slug"] in held,
                    "routine_only": p["slug"] in conv_mod.ROUTINE_ONLY_PERMISSIONS}
                   for p in all_perms]
    own_caps = info.cfg.capabilities or {}
    reservable = sorted({u for p in all_perms for u in (p["requires"].get("utils") or [])}
                        | set(own_caps.get("utils") or []))
    capabilities = {"active": {**EMPTY_CAPABILITIES, **own_caps},
                    "vocabulary": {"actions": list(GATED_KINDS), "utils": reservable}}
    traits_dir = info.cfg.dir / "traits"
    traits = sorted(p.stem for p in traits_dir.glob("*.md")) if traits_dir.is_dir() else []
    sm = server.system_model
    return {
        **_item(info),
        "description": info.cfg.description,
        "instruction": (info.cfg.dir / "instruction.md").read_text(encoding="utf-8")
        if (info.cfg.dir / "instruction.md").exists() else "",
        "workdir": str(info.cfg.fs_write_roots[0]) if info.cfg.fs_write_roots else "",
        "playbook": info.cfg.playbook_slug or None,   # bound source playbook → Update-playbook button
        "models": {k: ({"endpoint": r.endpoint, "model": r.model, "effort": r.effort}
                       if (r := info.cfg.models.get(k)) else None) for k in MODEL_KINDS},
        "system_model": {"endpoint": sm.endpoint, "model": sm.model} if sm else None,
        "endpoints": list(server.endpoints.keys()),
        "permissions": permissions,
        "capabilities": capabilities,
        "traits": traits,
        "budgets": info.cfg.budgets,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state} for r in info.runs],
        "background": _list_background_rows(request, slug),
        "problems": info.problems,
    }


class ConversationPatch(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    workdir: str | None = None
    budgets: dict | None = None
    models: dict | None = None


@router.patch("/conversations/{slug}")
def patch_conversation(request: Request, slug: str, patch: ConversationPatch) -> dict:
    """Unlike routine config edits (409 while a run is active), conversation edits apply
    at the NEXT reply: the engine reads routine.yaml only at run boot, each reply is its
    own boot, and a conversation dir has no git commit to race — so blocking on a live
    reply would only add friction."""
    info = _info(request, slug)
    updates = patch.model_dump(exclude_none=True)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "title" in updates:
        raw["name"] = raw["description"] = updates["title"].strip() or info.cfg.name
    if "tags" in updates:
        raw["tags"] = [t.strip() for t in updates["tags"] if t.strip()]
    if "workdir" in updates:
        wd = updates["workdir"].strip()
        raw["fs_read_roots"] = raw["fs_write_roots"] = [wd] if wd else []
    if "budgets" in updates:
        raw.setdefault("budgets", {}).update({k: int(v) for k, v in updates["budgets"].items()})
    if "models" in updates:
        server = request.app.state.server
        for kind, spec in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(400, f"unknown model kind {kind!r}")
            if not isinstance(spec, dict) or spec.get("endpoint") not in server.endpoints:
                raise HTTPException(400, f"models.{kind}: 'endpoint' must be a configured endpoint")
        raw["models"] = updates["models"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "updated": list(updates)}


@router.put("/conversations/{slug}/permissions")
def set_permissions(request: Request, slug: str, body: PermissionsBody) -> dict:
    # No active-reply guard: like the budget PATCH above, a conversation reads routine.yaml
    # only at each reply's boot, so a permission/capability edit simply lands on the NEXT
    # reply — blocking on a live reply would only add friction (the user can retune anytime).
    info = _info(request, slug)
    active, caps = resolve_permission_layers(request.app.state.server, body,
                                             info.cfg.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["permissions"] = active
    raw["capabilities"] = caps
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "active": active, "capabilities": caps}


@router.delete("/conversations/{slug}")
async def delete_conversation(request: Request, slug: str) -> dict:
    """A conversation is unversioned by design — delete means gone. Also cancels + removes any
    detached background tasks it launched (the manager's 'owner missing at delivery' branch is
    the safety net, but tearing them down here frees the pool and stops wasted compute)."""
    info = _info(request, slug)
    _guard_not_active(request, info)
    await _teardown_background(request, slug)
    shutil.rmtree(info.cfg.dir)
    return {"ok": True}


# --- detached background tasks (the `detach` action) ---------------------------------------


def _background_tasks(request: Request, owner_slug: str) -> list[tuple[str, registry.RoutineInfo]]:
    """(taskid, info) for every detached task owned by this conversation."""
    server = request.app.state.server
    catalog = registry.scan(server, server.background_home)
    return [(tid, ti) for tid, ti in catalog.items()
            if (ti.cfg.owner or {}).get("slug") == owner_slug]


def _list_background_rows(request: Request, slug: str) -> list[dict]:
    out = []
    for taskid, ti in _background_tasks(request, slug):
        last = ti.last_run
        out.append({"taskid": taskid, "label": ti.cfg.name or taskid,
                    "state": last.state if last else "pending",
                    "run_id": last.run_id if last else "",
                    "summary": (last.summary[:200] if last else ""),
                    "delivered": (ti.cfg.dir / "delivered.json").exists()})
    out.sort(key=lambda r: r["run_id"])
    return out


@router.get("/conversations/{slug}/background")
def list_background(request: Request, slug: str) -> list[dict]:
    """The detached tasks this conversation launched — for the run-view rail and monitoring."""
    _info(request, slug)   # 404 if the conversation is gone
    return _list_background_rows(request, slug)


@router.post("/conversations/{slug}/background")
def launch_background(request: Request, slug: str, prompt: str = Form(...),
                      workflow: str = Form(""), label: str = Form("")) -> dict:
    """Drop a detached-task intent for the DetachedManager to pick up next tick. Mirrors what
    the engine `detach` action does — exposed so a human (or a test) can launch one directly."""
    info = _info(request, slug)
    if not prompt.strip():
        raise HTTPException(400, "empty prompt")
    server = request.app.state.server
    taskid = background_task_id(slug)
    reqs = server.background_home / ".requests"
    reqs.mkdir(parents=True, exist_ok=True)
    atomic_write_json(reqs / f"{taskid}.json",
                      {"taskid": taskid, "prompt": prompt.strip(),
                       "workflow": (workflow.strip() or "general-task"),
                       "label": (label.strip() or "background task"),
                       "owner": {"slug": slug, "dir": str(info.cfg.dir)}})
    return {"ok": True, "taskid": taskid}


@router.post("/conversations/{slug}/background/{taskid}/cancel")
async def cancel_background(request: Request, slug: str, taskid: str) -> dict:
    """Abort a running detached task. Falls back to signalling the recorded pid for a task that
    survived a daemon restart (no longer in the runner's active set), mirroring the run abort."""
    server = request.app.state.server
    task_dir = server.background_home / taskid
    cfg, _ = load_routine(task_dir) if (task_dir / "routine.yaml").exists() else (None, [])
    if cfg is None or (cfg.owner or {}).get("slug") != slug:
        raise HTTPException(404, f"no background task {taskid!r} for conversation {slug!r}")
    runner = request.app.state.runner
    ok = await runner.abort(taskid)
    if not ok:  # not daemon-owned (survived a restart) — signal the last run's recorded pid
        last = registry.run_index(task_dir, taskid)
        st = read_json(last[0].dir / "status.json") if last else None
        pid = st.get("pid") if isinstance(st, dict) else None
        ok = await abort_process(pid, last[0].dir, last[0].run_id) if last else False
    return {"ok": True, "cancelled": ok}


async def _teardown_background(request: Request, slug: str) -> None:
    """On conversation delete: abort + remove its detached tasks (pid fallback for a task that
    outlived a restart), reusing the run abort path."""
    runner = request.app.state.runner
    for taskid, ti in _background_tasks(request, slug):
        if not await runner.abort(taskid):
            last = ti.last_run
            st = read_json(last.dir / "status.json") if last else None
            pid = st.get("pid") if isinstance(st, dict) else None
            if last and pid:
                await abort_process(pid, last.dir, last.run_id)
        shutil.rmtree(ti.cfg.dir, ignore_errors=True)


@router.post("/conversations/{slug}/playbook")
def save_playbook(request: Request, slug: str) -> dict:
    """Distil this conversation (its intent + the procedure that satisfied it) into a NEW library
    playbook via the system model, committed to the library. Always creates a new playbook (slug
    suffixed on collision) — use PUT to refine the one a conversation was seeded from. A sync def:
    FastAPI runs it in a worker thread, so the blocking inference never stalls the event loop."""
    from .. import playbook_distill, playbooks
    from ..workflows import library

    info = _info(request, slug)
    server = request.app.state.server
    home = server.library_home
    try:
        pb = playbook_distill.distill_playbook(server, info.cfg.dir)
    except Exception as exc:  # noqa: BLE001 — no endpoint / bad output → a clean 502
        raise HTTPException(502, f"could not distil a playbook: {exc}") from exc
    pb["slug"] = playbooks.unique_slug(home, pb["slug"])
    main_text, details = playbook_distill.materialize(pb)
    playbooks.write_playbook(home, pb["slug"], main=main_text, details=details)
    library.git_commit(home, f"save playbook {pb['slug']} (from conversation {slug})")
    return {"ok": True, "slug": pb["slug"], "title": pb["title"], "when": pb["when"],
            "axis": pb["axis"]}


@router.put("/conversations/{slug}/playbook")
def update_playbook(request: Request, slug: str) -> dict:
    """Revise the playbook this conversation was SEEDED from, folding in the deltas the user made
    by adjusting/intervening in the conversation (committed). 400 if the conversation has no bound
    playbook; 404 if that playbook was since deleted (Save a new one instead)."""
    from .. import playbook_distill, playbooks
    from ..workflows import library

    info = _info(request, slug)
    server = request.app.state.server
    home = server.library_home
    bound = info.cfg.playbook_slug
    if not bound:
        raise HTTPException(400, "this conversation was not created from a playbook")
    existing = playbooks.read_playbook(home, bound)
    if existing is None:
        raise HTTPException(404, f"playbook {bound!r} no longer exists — use Save as playbook instead")
    try:
        pb = playbook_distill.revise_playbook(server, info.cfg.dir, existing["content"], bound)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not revise the playbook: {exc}") from exc
    main_text, details = playbook_distill.materialize(pb)
    playbooks.write_playbook(home, bound, main=main_text, details=details)
    library.git_commit(home, f"update playbook {bound} (from conversation {slug})")
    return {"ok": True, "slug": bound, "title": pb["title"], "axis": pb["axis"]}


@router.get("/conversations/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The conversation's lifecycle graph (working ⇄ waiting for you) with the CURRENT node
    lit from the live run state — same shape as the routines endpoint so the artifact rail
    renders it. A conversation is a loop, so its state IS its reply cycle, not the single
    converse workflow phase (which is never written to phase.json, so the generic routine
    state graph would never highlight a node)."""
    info = _info(request, slug)
    last = info.last_run
    return {"states": [dict(s) for s in conv_mod.CONVERSATION_STATES],
            "current": conv_mod.conversation_phase(last.state if last else None)}


@router.get("/conversations/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    info = _info(request, slug)
    art = info.cfg.dir / "artifacts"
    out = []
    if art.is_dir():
        for p in art.rglob("*"):
            if p.is_file():
                st = p.stat()
                out.append({"path": str(p.relative_to(info.cfg.dir)), "name": p.name,
                            "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


@router.get("/conversations/{slug}/file")
def get_file(request: Request, slug: str, path: str):
    """Serve one artifact or attachment (the chat panel fetches these with the auth header
    and renders from blob URLs). Only artifacts/ and attachments/ are servable."""
    from ..paths import within

    info = _info(request, slug)
    rel = path.lstrip("/")
    try:
        p = resolve_rel(info.cfg.dir, rel)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    # the check runs on the RESOLVED path — 'artifacts/../routine.yaml' must not pass
    if not any(within(info.cfg.dir / sub.rstrip("/"), p) for sub in _SERVABLE):
        raise HTTPException(400, "only artifacts/ and attachments/ files are served")
    if not p.is_file():
        raise HTTPException(404, f"no file {path!r}")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, filename=p.name)
