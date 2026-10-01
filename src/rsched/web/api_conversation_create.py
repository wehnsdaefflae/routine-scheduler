"""CREATING a conversation — the composer's field parsing, the POST, and its defaults.

Split out of `api_conversations.py` (F393). Creating a conversation and living with one are
different jobs, and the first is almost entirely INPUT PARSING: every knob the composer offers
(project dir and extra roots, rules, connections, models, budgets, deliberation, a playbook
seed) arrives as a multipart form field and has to become config before
`conversations.create_conversation` ever runs.

That parsing sits apart because of what it has to get right: these choices are PRE-START on
purpose (F339). The rules reply #1 boots with are the ones in routine.yaml when it starts — a
rule added afterwards never governs reply #1, which has already fired. Same for the fs roots:
they must land in routine.yaml before the engine boots, or reply #1 runs without the access.
A field silently mis-parsed here is not a save bug, it is a reply that ran with the wrong
capabilities.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import conversations as conv_mod
from ..config import DELIBERATION_LEVELS, load_routine
from ..paths import atomic_write_json
from .api_routine_edit import (
    PermissionsBody,
    resolve_permission_layers,
)
from .config_fields import validate_models, validate_roots
from .conversations_common import (
    _save_attachments,
)
from .model_fit import model_window_problem
from .routines_common import (
    permission_layers_detail,
)

router = APIRouter(tags=["conversations"])

_autolabel_tasks: set[asyncio.Task] = set()   # strong refs for fire-and-forget autolabel tasks


def _parse_roots(raw: str, field: str) -> list[str]:
    """The composer's folder-access fields (D70): a JSON string array of server paths, then
    the one grant enforcer (`config_fields.validate_roots` — absolute, deduplicated, never a
    credential store). Existence is NOT required, matching the routine page's roots editor.
    """
    if not raw.strip():
        return []
    try:
        vals = json.loads(raw)
    except ValueError:
        raise HTTPException(400, f"{field}: expected a JSON array of paths") from None
    if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
        raise HTTPException(400, f"{field}: expected a JSON array of paths")
    return validate_roots(field, vals)


def _parse_rules(server, raw: str) -> list[str] | None:
    """The composer's rules field (F339): a JSON string array of library rule SLUGS, or
    blank to keep the conversation default. Every slug is validated against the live
    library, so a typo cannot quietly produce a conversation holding a rule that has no
    prose — the tail would name a practice nobody wrote.
    """
    from ..readmodels import library_reads

    if not raw.strip():
        return None                        # keep CONVERSATION_RULES
    try:
        vals = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "rules: expected a JSON array of rule slugs") from None
    if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
        raise HTTPException(400, "rules: expected a JSON array of rule slugs")
    known = set(library_reads.doc_slugs(server.rules_home))
    picked: list[str] = []
    unknown: list[str] = []
    for v in vals:
        slug = v.strip()
        if not slug:
            continue
        (picked if slug in known else unknown).append(slug)
    if unknown:
        raise HTTPException(400, f"rules: no such rule in the library: {', '.join(unknown)}")
    return picked


def _parse_connections(raw: str) -> dict[str, str] | None:
    """The composer's connections field (F339): a JSON {provider: account} map. Stricter than
    the PATCH (`config_fields.validate_connections` lets a routine bind ahead of connecting):
    reply #1 fires on create, so an unknown provider or an account that is not connected YET
    is a 400 here rather than a binding that fails on the very first reply.
    """
    from ..oauth import store as oauth_store
    from ..oauth.providers import PROVIDERS

    if not raw.strip():
        return None
    try:
        vals = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "connections: expected a JSON object") from None
    if not isinstance(vals, dict):
        raise HTTPException(400, "connections: expected a JSON object")
    connected = {(c.get("provider"), str(c.get("account"))) for c in oauth_store.list_connections()}
    out: dict[str, str] = {}
    for prov, account in vals.items():
        if prov not in PROVIDERS:
            raise HTTPException(400, f"connections.{prov}: unknown provider "
                                     f"(known: {', '.join(sorted(PROVIDERS))})")
        if not isinstance(account, str) or not account.strip():
            raise HTTPException(400, f"connections.{prov}: must be an account label")
        if (prov, account.strip()) not in connected:
            raise HTTPException(400, f"connections.{prov}: no connected account "
                                     f"{account.strip()!r} — connect it in Settings first")
        out[prov] = account.strip()
    return out or None


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
    # the PATCH's own check — role, catalog name, and a window that can run a turn (R112/R128)
    return {**(cfg or {}), **validate_models(server, per_role)}


#: The budgets the composer offers, each a whole number (-1 = unlimited where it applies).
_BUDGET_FIELDS = ("max_turns", "max_total_turns", "max_wall_clock_min", "max_total_tokens")
_RESTARTING = ("the server is restarting — the conversation was NOT created. Start it again "
               "in a moment, once the server is back.")


class ComposerForm(BaseModel):
    """Everything the new-conversation composer sends, as multipart form fields — each one a
    PRE-START choice (D70/F339): reply #1 boots with whatever lands in routine.yaml here. A
    blank field keeps the conversation default; the structured ones arrive as JSON strings
    and are parsed above, each into the 400 that names it.
    """

    text: str = ""              # the first message — blank only when a playbook seeds it
    playbook: str = ""          # a library playbook that seeds instruction.md
    workdir: str = ""           # the project directory: write root #1
    model: str = ""             # shorthand: one catalog model for main + tool_call
    models: str = ""            # JSON {role: catalog model}, wins over `model`
    max_turns: str = ""         # per REPLY …
    max_total_turns: str = ""   # … except this one: cumulative over the conversation
    max_wall_clock_min: str = ""
    max_total_tokens: str = ""
    deliberation: str = ""      # one of DELIBERATION_LEVELS
    permissions: str = ""       # JSON — the ⚙ panel's {active, capabilities}
    fs_read_roots: str = ""     # JSON array of absolute (or ~/) paths
    fs_write_roots: str = ""
    rules: str = ""             # JSON array of library rule slugs
    connections: str = ""       # JSON {provider: connected account}
    files: list[UploadFile] = []   # the first message's attachments


@router.post("/conversations")
async def create_conversation(request: Request,
                              form: Annotated[ComposerForm, Form()]) -> dict:
    server = request.app.state.server
    text = form.text.replace("\r\n", "\n")   # multipart encodes newlines CRLF; \n is canonical
    playbook, workdir, deliberation = (form.playbook.strip(), form.workdir.strip(),
                                       form.deliberation.strip())
    if not text.strip() and not playbook:
        raise HTTPException(400, "empty message — write the first message or pick a playbook")
    budgets: dict[str, int] = {}
    for key in _BUDGET_FIELDS:
        if raw_val := getattr(form, key).strip():
            try:
                budgets[key] = int(raw_val)
            except ValueError:
                raise HTTPException(400, f"{key} must be a whole number (-1 = unlimited)") from None
    if deliberation and deliberation not in DELIBERATION_LEVELS:
        raise HTTPException(400, f"unknown deliberation level {deliberation!r} "
                                 f"(expected one of {DELIBERATION_LEVELS})")
    # Pre-start permission layers: the composer's ⚙ panel sends the same {active,
    # capabilities} payload the header panel saves — resolved through the same
    # validate + cascade + floor, so reply #1 already runs under the chosen surface.
    active_perms: list[str] | None = None
    caps_override: dict | None = None
    if form.permissions.strip():
        try:
            body = PermissionsBody.model_validate_json(form.permissions)
        except ValueError as exc:
            raise HTTPException(400, f"invalid permissions payload: {exc}") from None
        active_perms, caps_override = resolve_permission_layers(server, body, {})
    # D70: folder access granted on the composer, applied to the config BEFORE the engine
    # boots — reply #1 already runs with it (the mid-run grant path stays for later changes).
    # The workdir is write root #1, so it is a grant like the lists.
    if workdir:
        validate_roots("workdir", [workdir])
    read_roots = _parse_roots(form.fs_read_roots, "fs_read_roots")
    write_roots = _parse_roots(form.fs_write_roots, "fs_write_roots")
    # F339: rules and connections are pre-start choices too: reply #1 boots with the rules
    # routine.yaml holds when it starts, so one bound afterwards never governs it.
    rule_slugs = _parse_rules(server, form.rules)
    conn_map = _parse_connections(form.connections)
    models_cfg = _resolve_create_models(server, form.model, form.models)
    # R81, the create door: in the restart's exit window the fire is refused, so refuse BEFORE
    # anything lands — a conversation made and never started stays in the list, and the
    # retry after the restart makes a second one.
    runner = request.app.state.runner
    if getattr(runner, "draining", False):
        raise HTTPException(503, _RESTARTING)
    server.conversations_home.mkdir(parents=True, exist_ok=True)
    slug = conv_mod.new_slug(server.conversations_home)
    try:
        conv_dir = conv_mod.create_conversation(server, slug=slug, first_message=text,
                                                workdir=workdir, models=models_cfg,
                                                permissions=active_perms,
                                                capabilities=caps_override,
                                                deliberation=deliberation,
                                                playbook_slug=playbook,
                                                budgets=budgets or None,
                                                fs_read_roots=read_roots,
                                                fs_write_roots=write_roots,
                                                rules=rule_slugs, connections=conn_map)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"the library has no '{conv_mod.CONVERSE_WORKFLOW}' workflow "
                                 f"— restart the daemon to seed it ({exc})") from exc
    try:
        rels = await _save_attachments(conv_dir, form.files)
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
    rid = await runner.fire(cfg, reason="conversation")
    if rid is None:   # a brand-new slug is never already active: the drain gate closed meanwhile
        shutil.rmtree(conv_dir, ignore_errors=True)
        raise HTTPException(503, _RESTARTING)
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

    from ..grants import capabilities_for, floor_capabilities
    from ..readmodels import library_reads

    server = request.app.state.server
    available = set(library_reads.doc_slugs(server.permissions_home))
    active = [p for p in conv_mod.CONVERSATION_PERMISSIONS if p in available]
    # floored like the create path — the preview must show what will actually persist
    lib = library_reads.requires(server.permissions_home)
    caps = floor_capabilities(active, lib, capabilities_for(active, lib))
    permissions, capabilities = permission_layers_detail(
        server, SimpleNamespace(permissions=active, capabilities=caps),
        routine_only=conv_mod.ROUTINE_ONLY_PERMISSIONS)
    # F339: the RULES surface too — which library rules exist (slug + summary, for the
    # picker) and which a new conversation holds by default: reply #1 boots with what is
    # chosen here.
    from .. import rules as rules_mod

    rule_slugs = library_reads.doc_slugs(server.rules_home)
    summaries = rules_mod.summaries(server.rules_home, rule_slugs)
    return {"permissions": permissions, "capabilities": capabilities,
            "budgets": dict(conv_mod.CONVERSATION_BUDGETS),
            "deliberation": conv_mod.CONVERSATION_DELIBERATION,
            "library_rules": [{"slug": s, "summary": summaries.get(s, "")}
                              for s in rule_slugs],
            "rules": [r for r in conv_mod.CONVERSATION_RULES if r in set(rule_slugs)]}
