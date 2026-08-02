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
import shutil
import uuid
from typing import Annotated

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import conversations as conv_mod
from .. import registry
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, load_routine, write_tuning
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json
from . import artifacts
from .api_background import list_background_rows, teardown_background
from .api_routine_edit import (
    PermissionsBody,
    TraitsBody,
    apply_trait_edit,
    resolve_permission_layers,
)
from .routines_common import active_run_dir, guard_not_active, permission_layers_detail

router = APIRouter(tags=["conversations"])

_autolabel_tasks: set[asyncio.Task] = set()   # strong refs for fire-and-forget autolabel tasks

from .conversations_common import (  # noqa: E402
    _home,
    _item,
    _save_attachments,
    conversation_info,
)


@router.get("/conversations")
def list_conversations(request: Request) -> list[dict]:
    catalog = registry.scan(request.app.state.server, _home(request))
    items = [_item(info) for info in catalog.values()]
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


@router.post("/conversations")
async def create_conversation(request: Request, text: Annotated[str, Form()] = "",  # noqa: PLR0913 — one Form field per composer knob

                              workdir: Annotated[str, Form()] = "",
                              model: Annotated[str, Form()] = "",
                              playbook: Annotated[str, Form()] = "",
                              max_turns: Annotated[str, Form()] = "",
                              max_total_turns: Annotated[str, Form()] = "",
                              max_wall_clock_min: Annotated[str, Form()] = "",
                              max_total_tokens: Annotated[str, Form()] = "",
                              deliberation: Annotated[str, Form()] = "",
                              permissions: Annotated[str, Form()] = "",
                              files: Annotated[list[UploadFile] | None, File()] = None) -> dict:
    server = request.app.state.server
    text = text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    if not text.strip() and not playbook.strip():
        raise HTTPException(400, "empty message — write the first message or pick a playbook")
    # Optional pre-start budgets: per-REPLY ceilings (turns / minutes / tokens) plus
    # max_total_turns, the cumulative cap over the WHOLE conversation (-1 = unlimited
    # where applicable). Blank = leave the default.
    budgets: dict[str, int] = {}
    for key, raw_val in (("max_turns", max_turns), ("max_total_turns", max_total_turns),
                         ("max_wall_clock_min", max_wall_clock_min),
                         ("max_total_tokens", max_total_tokens)):
        if raw_val.strip():
            try:
                budgets[key] = int(raw_val)
            except ValueError:
                raise HTTPException(400, f"{key} must be a whole number (-1 = unlimited)") from None
    if deliberation.strip() and deliberation.strip() not in DELIBERATION_LEVELS:
        raise HTTPException(400, f"unknown deliberation level {deliberation.strip()!r} "
                                 f"(expected one of {DELIBERATION_LEVELS})")
    # Pre-start permission layers: the composer's ⚙ panel sends the same {active,
    # capabilities} payload the header panel saves — resolved through the same
    # validate + cascade + floor, so reply #1 already runs under the chosen surface.
    active_perms: list[str] | None = None
    caps_override: dict | None = None
    if permissions.strip():
        try:
            body = PermissionsBody.model_validate_json(permissions)
        except ValueError as exc:
            raise HTTPException(400, f"invalid permissions payload: {exc}") from None
        active_perms, caps_override = resolve_permission_layers(server, body, {})
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
                                                permissions=active_perms,
                                                capabilities=caps_override,
                                                deliberation=deliberation.strip(),
                                                playbook_slug=playbook.strip(),
                                                budgets=budgets or None)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"the library has no '{conv_mod.CONVERSE_WORKFLOW}' workflow "
                                 f"— restart the daemon to seed it ({exc})") from exc
    try:
        rels = await _save_attachments(conv_dir, files or [])
    except HTTPException:
        shutil.rmtree(conv_dir, ignore_errors=True)   # no orphan conversation on a 413
        raise
    if rels:
        instruction = (conv_dir / "instruction.md").read_text(encoding="utf-8")
        (conv_dir / "instruction.md").write_text(
            instruction.rstrip() + conv_mod.attachment_note(rels) + "\n", encoding="utf-8")
        # the engine reads this at boot to auto-attach the first message's image/PDF
        # attachments to the kickoff (later messages carry attachments through the inbox)
        atomic_write_json(conv_dir / "state" / "pending-media.json", {"attachments": rels})
    cfg, _ = load_routine(conv_dir)
    # D66: the composer's Admin toggle sends x-admin-token ON CREATE too. Reply #1 fires
    # here, so the one-shot admin marker must be planted on the created run dir BEFORE its
    # engine boots — the resume path (write-then-resume an existing dir) cannot apply, a fresh
    # fire has no prior run dir to mark. Same web-layer-only token check as /message and
    # /runs/{id}/converse; the token never reaches the engine.
    from ..engine.admin import ADMIN_HEADER, admin_token_valid, write_admin_marker
    admin_ok = admin_token_valid(request.headers.get(ADMIN_HEADER))
    rid = await request.app.state.runner.fire(cfg, reason="conversation")
    if rid is None:
        raise HTTPException(409, "could not start the conversation (daemon draining?)")
    if admin_ok:
        # No await between fire() returning and this write, so the marker lands before the
        # runner's supervisor task spawns the engine subprocess that reads it at loop init.
        write_admin_marker(conv_dir / "runs" / rid.split(":", 1)[1])
    # title + tags off the reply path — best-effort, never blocks the response (the strong
    # ref keeps the task from being GC'd mid-flight)
    task = asyncio.create_task(asyncio.to_thread(conv_mod.autolabel, server, conv_dir, text))
    _autolabel_tasks.add(task)
    task.add_done_callback(_autolabel_tasks.discard)
    return {"slug": slug, "run_id": rid}


@router.get("/conversations/defaults")
def conversation_defaults(request: Request) -> dict:
    """What a NEW conversation starts with — the permission layers (conversation defaults
    active, routine-only docs greyed), budgets, and deliberation. The composer renders the
    same ⚙ capabilities & budgets surface as the header panel from this, BEFORE create:
    the first reply fires on create, so a post-hoc toggle would miss it. Registered above
    the /conversations/{slug} routes so "defaults" never resolves as a slug.
    """
    from types import SimpleNamespace

    from .. import library_docs
    from ..grants import capabilities_for, floor_capabilities, read_library_requires

    server = request.app.state.server
    available = set(library_docs.slugs(server.permissions_home))
    active = [p for p in conv_mod.CONVERSATION_PERMISSIONS if p in available]
    # floored like the create path — the preview must show what will actually persist
    lib = read_library_requires(server.permissions_home)
    caps = floor_capabilities(active, lib, capabilities_for(active, lib))
    permissions, capabilities = permission_layers_detail(
        server, SimpleNamespace(permissions=active, capabilities=caps),
        routine_only=conv_mod.ROUTINE_ONLY_PERMISSIONS)
    return {"permissions": permissions, "capabilities": capabilities,
            "budgets": dict(conv_mod.CONVERSATION_BUDGETS),
            "deliberation": conv_mod.CONVERSATION_DELIBERATION}


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
    return command_catalog(policy, utils_lib.list_utils(server.libraries_home))


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
    text = text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    if not text.strip():
        raise HTTPException(400, "empty message")
    conv_dir = info.cfg.dir
    is_command = bool(command.strip())
    last = info.last_run
    is_mid_run = bool(last and last.state not in registry.TERMINAL_STATES)
    # R81: a terminal/new conversation must be WOKEN (resume_terminal / fire), and both refuse
    # while the daemon is draining for a self-update restart — with nothing re-driving a pending
    # inbox after relaunch. Filing first then failing the wake strands the message and returns a
    # 409 that reads as total failure, so the operator blind-resends (the observed duplicate
    # spam). Refuse up front, BEFORE filing, unless the run is live (mid-run drains its own inbox
    # at the next turn boundary; draining does not kill an already-running run).
    if not is_mid_run and getattr(request.app.state.runner, "draining", False):
        raise HTTPException(
            503, "the server is restarting — your message was NOT saved. Resend it once, in a "
                 "moment, after the server is back (repeated resends only pile up duplicates).")
    rels = await _save_attachments(conv_dir, files or [])
    full = text.rstrip() + conv_mod.attachment_note(rels)
    atomic_write_json(conv_dir / "inbox"
                      / f"msg-{now_iso().replace(':', '')}-{uuid.uuid4().hex[:8]}.json",
                      {"text": full, "ts": now_iso(), "via": "conversation",
                       **({"command": True} if command.strip() else {}),
                       **({"attachments": rels} if rels else {})})
    if last and last.state not in registry.TERMINAL_STATES:
        return {"ok": True, "delivery": "mid-run", "run_id": last.run_id,
                "command": is_command}
    runner = request.app.state.runner
    # D62/D63: an ADMIN resume from the Conversations composer — the SAME web-layer-only token
    # check as /runs/{id}/converse. On a valid token, unlock capability gating for THIS resumed
    # leg via the one-shot marker (never persisted, never inherited by a sub-workflow). Scoped to
    # a resume of an existing terminal conversation — a fresh fire has no run dir to mark yet, and
    # the mid-run branch already returned above. The marker is cleared again if the wake fails, so
    # a stale marker can never grant admin to a LATER, tokenless resume of the same run.
    from ..engine.admin import (
        ADMIN_HEADER,
        admin_token_valid,
        clear_admin_marker,
        write_admin_marker,
    )
    admin_run_dir = None
    if last and admin_token_valid(request.headers.get(ADMIN_HEADER)):
        admin_run_dir = conv_dir / "runs" / last.run_id.split(":", 1)[1]
        write_admin_marker(admin_run_dir)
    # A command wakes the engine to EXECUTE it and return to idle without a reply (the loop's
    # command-only gate) — same resume, but the model never takes a turn.
    rid = (await runner.resume_terminal(info.cfg, reason="converse") if last
           else await runner.fire(info.cfg, reason="conversation"))
    if not rid:
        if admin_run_dir is not None:
            clear_admin_marker(admin_run_dir)
        raise HTTPException(
            409, "could not wake the conversation (draining, or a reply just started)")
    return {"ok": True, "delivery": "command" if is_command else "resumed",
            "run_id": rid, "command": is_command}


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
        # OAuth connection bindings {provider: account} — a conversation binds connections
        # exactly like a routine (it is routine-shaped; the engine injects the token from
        # routine.yaml `connections:` either way). The picker's options come from
        # GET /api/settings/oauth. D55: closes R70 (a conversation could not bind Google).
        "connections": dict(info.cfg.connections),
        "permissions": permissions,
        "capabilities": capabilities,
        "traits": traits,
        "budgets": info.cfg.budgets,
        "deliberation": info.cfg.deliberation,
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
    connections: dict | None = None   # {provider: account} — bound OAuth connections (D55)
    deliberation: str | None = None   # DELIBERATION_LEVELS — applies at the next reply


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
    if "connections" in updates:
        # Same validation as a routine (api_routine_edit): known provider, non-empty account
        # label; REPLACE wholesale (blanking a provider clears it). Existence of the connected
        # account is NOT required — a conversation may bind ahead of connecting; the engine
        # injects nothing until the account is connected. D55: closes R70.
        from ..oauth.providers import PROVIDERS
        for prov, account in (updates["connections"] or {}).items():
            if prov not in PROVIDERS:
                raise HTTPException(400, f"unknown connection provider {prov!r}")
            if not isinstance(account, str) or not account:
                raise HTTPException(400, f"connections.{prov}: must be an account label")
        raw["connections"] = updates["connections"]
    if "deliberation" in updates:   # tuning, not config — lands in tuning.yaml
        if updates["deliberation"] not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level "
                                     f"{updates['deliberation']!r}")
        write_tuning(info.cfg.dir, {"deliberation": updates["deliberation"]})
    if set(updates) - {"deliberation"}:   # a tuning-only patch never rewrites routine.yaml
        atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return {"ok": True, "updated": list(updates)}


@router.post("/conversations/{slug}/traits")
def set_conversation_traits(request: Request, slug: str, body: TraitsBody) -> dict:
    """Add/remove this conversation's practice modules — the same implementation routines
    use. A conversation is where this matters most: the work shifts topic mid-thread, and
    an added module reaches the reply already in flight (control.json) as well as every
    reply after it.
    """
    info = conversation_info(request, slug)
    return apply_trait_edit(request, info.cfg.dir, body, active_run_dir(info))


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
