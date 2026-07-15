"""Conversations API: list/create/detail/message/delete, config edits, artifact +
attachment serving.

A conversation is a routine-shaped dir under conversations_home (see conversations.py);
its ONE run is continued in place — a message to a live run is an ordinary injection, a
message to a finished run resumes it (converse semantics). Transcript/SSE/abort ride the
existing /api/runs endpoints (run resolution is home-aware). Attachments upload as
multipart files into <conv>/attachments/ and travel as an `[attached files]` block in the
message text; deliverables the model writes into <conv>/artifacts/ are listed and served
here for the chat's artifact panel. Its detached background tasks live in api_background.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import conversations as conv_mod
from ..config import MODEL_KINDS, load_routine
from ..daemon import registry
from ..ids import now_iso, run_ts
from ..paths import atomic_write, atomic_write_json
from . import artifacts
from .api_background import list_background_rows, teardown_background
from .api_routines import (
    PermissionsBody,
    guard_not_active,
    permission_layers_detail,
    resolve_permission_layers,
)

router = APIRouter(tags=["conversations"])

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_autolabel_tasks: set[asyncio.Task] = set()   # strong refs for fire-and-forget autolabel tasks


def _home(request: Request) -> Path:
    return request.app.state.server.conversations_home


def conversation_info(request: Request, slug: str) -> registry.RoutineInfo:
    d = _home(request) / slug
    if not (d / "routine.yaml").exists():
        raise HTTPException(404, f"no conversation {slug!r}")
    cfg, problems = load_routine(d)
    if cfg is None:
        raise HTTPException(500, "; ".join(problems))
    return registry.RoutineInfo(cfg=cfg, problems=problems,
                                runs=registry.run_index(d, cfg.slug),
                                open_questions=[])


async def _save_attachments(conv_dir: Path, files: list[UploadFile]) -> list[str]:
    """Store uploads under attachments/ (timestamped, safe basenames); returns the
    conversation-relative paths for the message's attachment block.
    """
    rels: list[str] = []
    stamp = run_ts()
    for i, f in enumerate(files or []):
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(f.filename or f"file-{i}").name).strip("-.") \
            or f"file-{i}"
        rel = f"attachments/{stamp}-{base}"
        data = await f.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                413, f"attachment {base!r} exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB")
        (conv_dir / "attachments").mkdir(exist_ok=True)
        (conv_dir / rel).write_bytes(data)
        rels.append(rel)
    return rels


def _snippet(info: registry.RoutineInfo) -> str:
    last = info.last_run
    return (last.summary.strip().splitlines()[0][:160]
            if last and last.summary else "")


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
async def create_conversation(request: Request, text: Annotated[str, Form()] = "",
                              workdir: Annotated[str, Form()] = "",
                              model: Annotated[str, Form()] = "",
                              shell: Annotated[str, Form()] = "",
                              playbook: Annotated[str, Form()] = "",
                              max_turns: Annotated[str, Form()] = "",
                              max_total_turns: Annotated[str, Form()] = "",
                              files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
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
    permissions = [*conv_mod.CONVERSATION_PERMISSIONS, "shell"] if shell.strip() else None
    models = None
    if model.strip():   # a picked catalog model name → all three roles (else system_model fallback)
        if model.strip() not in server.models:
            raise HTTPException(
                400, f"unknown model {model.strip()!r} — add it to the catalog first")
        models = {k: model.strip() for k in ("main", "subroutine", "tool_call")}
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
    rels = await _save_attachments(conv_dir, files or [])
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
    # title + tags off the reply path — best-effort, never blocks the response (the strong
    # ref keeps the task from being GC'd mid-flight)
    task = asyncio.create_task(asyncio.to_thread(conv_mod.autolabel, server, conv_dir, text))
    _autolabel_tasks.add(task)
    task.add_done_callback(_autolabel_tasks.discard)
    return {"slug": slug, "run_id": rid}


@router.get("/conversations/{slug}/commands")
def commands(request: Request, slug: str) -> dict:
    """The chat composer's command reference + autocomplete feed: the slash-command kinds
    this conversation's capability surface allows (the engine still enforces exactly at
    execution) and the util catalog (name + summary + usage).
    """
    from .. import utils_lib
    from ..engine.commands import command_catalog
    from ..grants import load_policy

    info = conversation_info(request, slug)
    server = request.app.state.server
    policy = load_policy(server.permissions_home, info.cfg.permissions,
                         info.cfg.capabilities)
    return command_catalog(policy, utils_lib.list_utils(server.utils_home))


@router.post("/conversations/{slug}/message")
async def message(request: Request, slug: str, text: Annotated[str, Form()],
                  command: Annotated[str, Form()] = "",
                  files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    """Append a user message (with optional attachments): a live reply picks it up at the
    next turn boundary; a finished conversation is resumed in place. `command` marks a
    slash command — the engine EXECUTES it as a user-authored action instead of handing
    it to the model as prose.
    """
    info = conversation_info(request, slug)
    if not text.strip():
        raise HTTPException(400, "empty message")
    conv_dir = info.cfg.dir
    rels = await _save_attachments(conv_dir, files or [])
    full = text.rstrip() + conv_mod.attachment_note(rels)
    atomic_write_json(conv_dir / "inbox" / f"msg-{now_iso().replace(':', '')}.json",
                      {"text": full, "ts": now_iso(), "via": "conversation",
                       **({"command": True} if command.strip() else {}),
                       **({"attachments": rels} if rels else {})})
    last = info.last_run
    if last and last.state not in registry.TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run", "run_id": last.run_id}
    runner = request.app.state.runner
    rid = (await runner.resume_terminal(info.cfg, reason="converse") if last
           else await runner.fire(info.cfg, reason="conversation"))
    if not rid:
        raise HTTPException(
            409, "could not wake the conversation (draining, or a reply just started)")
    return {"ok": True, "delivery": "resumed", "run_id": rid}


@router.get("/conversations/{slug}")
def detail(request: Request, slug: str) -> dict:
    info = conversation_info(request, slug)
    server = request.app.state.server
    permissions, capabilities = permission_layers_detail(
        server, info.cfg, routine_only=conv_mod.ROUTINE_ONLY_PERMISSIONS)
    traits_dir = info.cfg.dir / "traits"
    traits = sorted(p.stem for p in traits_dir.glob("*.md")) if traits_dir.is_dir() else []
    return {
        **_item(info),
        "description": info.cfg.description,
        "instruction": (info.cfg.dir / "instruction.md").read_text(encoding="utf-8")
        if (info.cfg.dir / "instruction.md").exists() else "",
        "workdir": str(info.cfg.fs_write_roots[0]) if info.cfg.fs_write_roots else "",
        "playbook": info.cfg.playbook_slug or None,   # bound source → Update-playbook button
        # Model roles are catalog model NAMES (null → system_model fallback);
        # `catalog` = the picker.
        "models": {k: (info.cfg.models.get(k) or None) for k in MODEL_KINDS},
        "system_model": server.system_model or None,
        "catalog": list(server.models.keys()),
        "permissions": permissions,
        "capabilities": capabilities,
        "traits": traits,
        "budgets": info.cfg.budgets,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state} for r in info.runs],
        "background": list_background_rows(request, slug),
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
    reply would only add friction.
    """
    info = conversation_info(request, slug)
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
        for kind, name in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(400, f"unknown model kind {kind!r}")
            if not isinstance(name, str) or name not in server.models:
                raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        raw["models"] = updates["models"]
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return {"ok": True, "updated": list(updates)}


@router.put("/conversations/{slug}/permissions")
def set_permissions(request: Request, slug: str, body: PermissionsBody) -> dict:
    # No active-reply guard: like the budget PATCH above, a conversation reads routine.yaml
    # only at each reply's boot, so a permission/capability edit simply lands on the NEXT
    # reply — blocking on a live reply would only add friction (the user can retune anytime).
    info = conversation_info(request, slug)
    active, caps = resolve_permission_layers(request.app.state.server, body,
                                             info.cfg.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["permissions"] = active
    raw["capabilities"] = caps
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return {"ok": True, "active": active, "capabilities": caps}


@router.delete("/conversations/{slug}")
async def delete_conversation(request: Request, slug: str) -> dict:
    """A conversation is unversioned by design — delete means gone. Also cancels + removes any
    detached background tasks it launched (the manager's 'owner missing at delivery' branch is
    the safety net, but tearing them down here frees the pool and stops wasted compute).
    """
    info = conversation_info(request, slug)
    guard_not_active(request, info, noun="conversation")
    await teardown_background(request, slug)
    shutil.rmtree(info.cfg.dir)
    return {"ok": True}


@router.post("/conversations/{slug}/playbook")
def save_playbook(request: Request, slug: str) -> dict:
    """Distil this conversation (its intent + the procedure that satisfied it) into a NEW library
    playbook via the system model, committed to the library. Always creates a new playbook (slug
    suffixed on collision) — use PUT to refine the one a conversation was seeded from. A sync def:
    FastAPI runs it in a worker thread, so the blocking inference never stalls the event loop.
    """
    from .. import playbook_distill, playbooks
    from ..workflows import library

    info = conversation_info(request, slug)
    server = request.app.state.server
    home = server.library_home
    try:
        pb = playbook_distill.distill_playbook(server, info.cfg.dir)
    except Exception as exc:
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
    playbook; 404 if that playbook was since deleted (Save a new one instead).
    """
    from .. import playbook_distill, playbooks
    from ..workflows import library

    info = conversation_info(request, slug)
    server = request.app.state.server
    home = server.library_home
    bound = info.cfg.playbook_slug
    if not bound:
        raise HTTPException(400, "this conversation was not created from a playbook")
    existing = playbooks.read_playbook(home, bound)
    if existing is None:
        raise HTTPException(
            404, f"playbook {bound!r} no longer exists — use Save as playbook instead")
    try:
        pb = playbook_distill.revise_playbook(server, info.cfg.dir, existing["content"], bound)
    except Exception as exc:
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
    state graph would never highlight a node).
    """
    info = conversation_info(request, slug)
    last = info.last_run
    return {"states": [dict(s) for s in conv_mod.CONVERSATION_STATES],
            "current": conv_mod.conversation_phase(last.state if last else None)}


@router.get("/conversations/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    info = conversation_info(request, slug)
    return artifacts.list_artifacts(info.cfg.dir)


@router.get("/conversations/{slug}/file")
def get_file(request: Request, slug: str, path: str):
    """Serve one artifact or attachment (the chat panel fetches these with the auth header
    and renders from blob URLs). Only artifacts/ and attachments/ are servable.
    """
    info = conversation_info(request, slug)
    return artifacts.serve_file(info.cfg.dir, path, subdirs=("artifacts", "attachments"))
