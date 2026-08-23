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
import json
import shutil
import uuid
from typing import Annotated

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from .. import conversations as conv_mod
from .. import registry
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, load_routine, write_tuning
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json
from . import artifacts
from .api_background import list_background_rows, teardown_background
from .api_routine_edit import (
    PermissionsBody,
    RulesBody,
    apply_rule_edit,
    resolve_permission_layers,
)
from .model_fit import model_window_problem, window_meta
from .routines_common import active_run_dir, guard_not_active, permission_layers_detail

router = APIRouter(tags=["conversations"])


class StoppingCondition(BaseModel):
    """One user-owned stopping condition (F334/D98) — prose the run must account for."""

    model_config = ConfigDict(extra="forbid")
    id: str = ""
    text: str
    status: str = "open"
    ts: str = ""


class StoppingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conditions: list[StoppingCondition]

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


def _parse_roots(raw: str, field: str) -> list[str]:
    """The composer's folder-access fields (D70): a JSON string array of server paths.
    Each must be absolute (or ~-anchored — the canonical form live configs carry);
    existence is NOT required, matching the routine page's roots editor. Returns the
    cleaned list; raises 400 on anything else.
    """
    import json

    if not raw.strip():
        return []
    try:
        vals = json.loads(raw)
    except ValueError:
        raise HTTPException(400, f"{field}: expected a JSON array of paths") from None
    if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
        raise HTTPException(400, f"{field}: expected a JSON array of paths")
    roots: list[str] = []
    for v in vals:
        p = v.strip().rstrip("/") or "/"
        if not p.startswith(("/", "~/")) or p == "~":
            raise HTTPException(
                400, f"{field}: {v!r} is not an absolute path (use /abs/path or ~/path)")
        if p not in roots:
            roots.append(p)
    return roots


def _resolve_create_models(server, model: str, models: str) -> dict[str, str] | None:
    """Model roles at create time. `model` is the shorthand — one picked catalog name seeds
    main + tool_call. `models` is the full per-role JSON map (main / tool_call / uncensored),
    so a conversation can START with a honeypot (uncensored) role already configured — the
    role the refusal machinery hands a refused request's essence to, otherwise unreachable
    before the first reply. The per-role map, when present, wins over the shorthand. Every
    name is validated against the catalog + window exactly like the PATCH path (R112/R128).
    """
    cfg: dict[str, str] | None = None
    if model.strip():
        if model.strip() not in server.models:
            raise HTTPException(
                400, f"unknown model {model.strip()!r} — add it to the catalog first")
        problem = model_window_problem(server, model.strip())
        if problem:
            raise HTTPException(400, problem)
        cfg = {k: model.strip() for k in ("main", "tool_call")}
    if not models.strip():
        return cfg
    try:
        per_role = json.loads(models)
    except ValueError as exc:
        raise HTTPException(400, f"models: invalid JSON ({exc})") from None
    if not isinstance(per_role, dict):
        raise HTTPException(400, "models: must be a {role: model-name} object")
    for kind, name in per_role.items():
        if kind not in MODEL_KINDS:
            raise HTTPException(400, f"unknown model kind {kind!r}")
        if not isinstance(name, str) or name not in server.models:
            raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        problem = model_window_problem(server, name)
        if problem:   # R112/R128: the first reply would die on its first completion
            raise HTTPException(400, problem)
    return {**(cfg or {}), **{k: v.strip() for k, v in per_role.items()}}


@router.post("/conversations")
async def create_conversation(request: Request, text: Annotated[str, Form()] = "",  # noqa: PLR0913 — one Form field per composer knob

                              workdir: Annotated[str, Form()] = "",
                              model: Annotated[str, Form()] = "",
                              models: Annotated[str, Form()] = "",
                              playbook: Annotated[str, Form()] = "",
                              max_turns: Annotated[str, Form()] = "",
                              max_total_turns: Annotated[str, Form()] = "",
                              max_wall_clock_min: Annotated[str, Form()] = "",
                              max_total_tokens: Annotated[str, Form()] = "",
                              deliberation: Annotated[str, Form()] = "",
                              permissions: Annotated[str, Form()] = "",
                              fs_read_roots: Annotated[str, Form()] = "",
                              fs_write_roots: Annotated[str, Form()] = "",
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
    # D70: folder access granted on the composer, applied to the config BEFORE the engine
    # boots — reply #1 already runs with it (the mid-run grant path stays for later changes).
    read_roots = _parse_roots(fs_read_roots, "fs_read_roots")
    write_roots = _parse_roots(fs_write_roots, "fs_write_roots")
    models_cfg = _resolve_create_models(server, model, models)
    server.conversations_home.mkdir(parents=True, exist_ok=True)
    slug = conv_mod.new_slug(server.conversations_home)
    try:
        conv_dir = conv_mod.create_conversation(server, slug=slug, first_message=text,
                                                workdir=workdir, models=models_cfg,
                                                permissions=active_perms,
                                                capabilities=caps_override,
                                                deliberation=deliberation.strip(),
                                                playbook_slug=playbook.strip(),
                                                budgets=budgets or None,
                                                fs_read_roots=read_roots,
                                                fs_write_roots=write_roots)
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
    if is_mid_run:
        # R108 residual (F268): the liveness snapshot above predates the file write — a
        # run that finished inside that window would leave this message queued with
        # nothing waking it (the engine's own finish-defer only covers messages that
        # landed BEFORE its final check). Re-read the LIVE state now that the message is
        # durably down: still live → mid-run delivery as before (the next turn boundary
        # drains it); finished in between → fall through to the terminal-resume below,
        # exactly as if the run had already been terminal when the message arrived.
        fresh = conversation_info(request, slug).last_run
        if fresh and fresh.state not in registry.TERMINAL_STATES:
            return {"ok": True, "delivery": "mid-run", "run_id": fresh.run_id,
                    "command": is_command}
        last = fresh or last
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


def _raw_roots(conv_dir, key: str) -> list[str]:
    """A root list as routine.yaml literally carries it (~ unexpanded) — what the UI shows
    and what the PATCH below edits, so display and write stay in one string domain.
    """
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
    return [str(r) for r in raw.get(key) or []]


@router.get("/conversations/{slug}")
def detail(request: Request, slug: str) -> dict:
    info = conversation_info(request, slug)
    server = request.app.state.server
    permissions, capabilities = permission_layers_detail(
        server, info.cfg, routine_only=conv_mod.ROUTINE_ONLY_PERMISSIONS)
    return {
        **_item(info),
        "description": info.cfg.description,
        "instruction": (info.cfg.dir / "instruction.md").read_text(encoding="utf-8")
        if (info.cfg.dir / "instruction.md").exists() else "",
        "workdir": str(info.cfg.fs_write_roots[0]) if info.cfg.fs_write_roots else "",
        # D70: the full folder-access lists (workdir is write_roots[0] by convention) —
        # the raw config strings, so the UI shows what the file says (~ kept, not expanded)
        "fs_read_roots": _raw_roots(info.cfg.dir, "fs_read_roots"),
        "fs_write_roots": _raw_roots(info.cfg.dir, "fs_write_roots"),
        "playbook": info.cfg.playbook_slug or None,   # bound source → Update-playbook button
        # Model roles are catalog model NAMES (null → system_model fallback);
        # `catalog` = the picker, `catalog_meta` its per-model window sizing (R112/R128:
        # the picker shows what fits instead of letting an impossible pick die at reply #1).
        "models": {k: (info.cfg.models.get(k) or None) for k in MODEL_KINDS},
        "system_model": server.system_model or None,
        "catalog": list(server.models.keys()),
        "catalog_meta": window_meta(server),
        # OAuth connection bindings {provider: account} — a conversation binds connections
        # exactly like a routine (it is routine-shaped; the engine injects the token from
        # routine.yaml `connections:` either way). The picker's options come from
        # GET /api/settings/oauth. D55: closes R70 (a conversation could not bind Google).
        "connections": dict(info.cfg.connections),
        # Remote-machine bindings + the catalog for the picker — the same card the routine
        # page mounts (D102, R475/R496: a conversation had no surface to bind a machine, so
        # RSCHED_MACHINES stayed empty however many grants it held). Stale bindings are
        # kept, like routines: the UI flags them clearable.
        "machines": list(info.cfg.machines),
        "machine_catalog": [{"name": m.name, "description": m.description,
                             "host": m.host, "user": m.user, "tags": list(m.tags)}
                            for m in server.machines.values()],
        "permissions": permissions,
        "capabilities": capabilities,
        "rules": list(info.cfg.rules),
        "budgets": info.cfg.budgets,
        "deliberation": info.cfg.deliberation,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state} for r in info.runs],
        "background": list_background_rows(request, slug),
        "problems": info.problems,
    }


def _apply_machines(server, raw: dict, names: list) -> None:
    """Same rule as the routine PATCH (api_routine_edit): catalog membership required — a
    machine name off the catalog is meaningless and the picker only offers catalog names.
    REPLACE wholesale; the next reply's boot injects RSCHED_MACHINES (D102).
    """
    if any(not isinstance(n, str) for n in names):
        raise HTTPException(400, "machines: must be a list of catalog machine names")
    for n in names:
        if n not in server.machines:
            raise HTTPException(400, f"unknown machine {n!r} (add it in Settings → Machines)")
    raw["machines"] = names


class ConversationPatch(BaseModel):
    # forbid unknown keys, like RoutinePatch: a silently-dropped stray reads as "saved"
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    tags: list[str] | None = None
    workdir: str | None = None
    budgets: dict | None = None
    models: dict | None = None
    machines: list[str] | None = None   # catalog machine names (D102) — REPLACE wholesale
    connections: dict | None = None   # {provider: account} — bound OAuth connections (D55)
    deliberation: str | None = None   # DELIBERATION_LEVELS — applies at the next reply
    fs_read_roots: list[str] | None = None    # D82: full folder-access lists — REPLACE
    fs_write_roots: list[str] | None = None   # wholesale (workdir stays write_roots[0])


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
        # The workdir is BY CONVENTION the first write root. Replace that slot only —
        # folder grants beyond it (D70 create-time roots, allow-forever fs decisions)
        # must survive a project-directory change, not be wiped by it.
        wd = updates["workdir"].strip()
        prev = next(iter(raw.get("fs_write_roots") or []), None)
        for key in ("fs_read_roots", "fs_write_roots"):
            roots = [str(r) for r in raw.get(key) or []]
            roots = [r for r in roots if r not in (prev, wd)]
            raw[key] = ([wd] if wd else []) + roots
    # D82: the header panel edits the FULL folder-access lists — REPLACE wholesale, same
    # validation as the routine page's save path (api_routine_edit._apply_resource_fields).
    # Placed AFTER the workdir slot-swap so a request carrying both (the UI sends one or the
    # other) lands on the explicit lists. Like every conversation config edit, the change
    # reaches the NEXT reply's boot — a live reply keeps the roots it booted with.
    for roots_key in ("fs_read_roots", "fs_write_roots"):
        if roots_key in updates:
            vals = updates[roots_key]
            if any(not isinstance(p, str) or not p.strip() for p in vals):
                raise HTTPException(400, f"{roots_key}: must be a list of non-empty "
                                         f"path strings")
            raw[roots_key] = [p.strip() for p in vals]
    if "budgets" in updates:
        raw.setdefault("budgets", {}).update({k: int(v) for k, v in updates["budgets"].items()})
    if "models" in updates:
        server = request.app.state.server
        for kind, name in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(400, f"unknown model kind {kind!r}")
            if not isinstance(name, str) or name not in server.models:
                raise HTTPException(400, f"models.{kind}: must be a catalog model name")
            problem = model_window_problem(server, name)
            if problem:   # R112/R128: the next reply would die on its first completion
                raise HTTPException(400, problem)
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
    if "machines" in updates:
        _apply_machines(request.app.state.server, raw, updates["machines"] or [])
    if "deliberation" in updates:   # tuning, not config — lands in tuning.yaml
        if updates["deliberation"] not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level "
                                     f"{updates['deliberation']!r}")
        write_tuning(info.cfg.dir, {"deliberation": updates["deliberation"]})
    if set(updates) - {"deliberation"}:   # a tuning-only patch never rewrites routine.yaml
        atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return {"ok": True, "updated": list(updates)}


@router.post("/conversations/{slug}/rules")
def set_conversation_rules(request: Request, slug: str, body: RulesBody) -> dict:
    """Bind/unbind this conversation's general rules — the same implementation routines
    use. A conversation is where this matters most: the work shifts topic mid-thread, and
    a newly bound rule reaches the reply already in flight (control.json) as well as every
    reply after it.
    """
    info = conversation_info(request, slug)
    return apply_rule_edit(request, info.cfg.dir, body, active_run_dir(info))


@router.get("/conversations/{slug}/stopping")
def get_stopping(request: Request, slug: str) -> dict:
    """The conversation's stopping conditions (F334/D98) — the USER's meaning-level bounds.
    The list is user-owned prose; the engine only makes it impossible to ignore (state
    digest + finish gate), so this read is what the sidebar panel renders.
    """
    from ..engine import stopping
    info = conversation_info(request, slug)
    return {"conditions": stopping.load(info.cfg.dir)}


@router.put("/conversations/{slug}/stopping")
def set_stopping(request: Request, slug: str, body: StoppingBody) -> dict:
    """Replace the conversation's stopping-condition list (user-owned, whole-list PUT —
    same single-writer shape as the rules/permissions saves). Ids are assigned stably
    (s1, s2, …); a reply already in flight sees the change at its next boot.
    """
    from ..engine import stopping
    info = conversation_info(request, slug)
    rows = stopping.save(info.cfg.dir, [c.model_dump() for c in body.conditions],
                         now=now_iso())
    return {"ok": True, "conditions": rows}


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


@router.delete("/conversations/{slug}/artifacts")
def delete_artifact(request: Request, slug: str, path: str) -> dict:
    """Remove one artifact from the sidebar (user order 2026-08-14). artifacts/ only —
    attachments are the USER'S uploads and stay.
    """
    info = conversation_info(request, slug)
    return artifacts.delete_artifact(info.cfg.dir, path)


@router.get("/conversations/{slug}/file")
def get_file(request: Request, slug: str, path: str):
    """Serve one artifact or attachment (the chat panel fetches these with the auth header
    and renders from blob URLs). Only artifacts/ and attachments/ are servable.
    """
    info = conversation_info(request, slug)
    return artifacts.serve_file(info.cfg.dir, path, subdirs=("artifacts", "attachments"))
