"""Reading and EDITING a conversation's config.

Split out of `api_conversations.py` (F393): talking to a conversation is one job, changing what
it is allowed to do is another.

Unlike routine edits these are never blocked by an active run — the engine reads `routine.yaml`
only at boot, each reply is its own boot, and a conversation dir has no git commit to race. What
a LIVE reply does with a change is `configflow`'s one classification (F337): the live-classified
fields are adopted at its next turn boundary, and it is told about the rest.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import conversations as conv_mod
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, write_tuning
from ..paths import atomic_write
from .api_background import list_background_rows
from .api_routine_edit import (
    PermissionsBody,
    RulesBody,
    apply_rule_edit,
    resolve_permission_layers,
)
from .conversations_common import (
    _item,
    conversation_info,
)
from .model_fit import model_window_problem, window_meta
from .routines_common import (
    active_run_dir,
    permission_layers_detail,
    signal_config_change,
)

router = APIRouter(tags=["conversation-config"])


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
    """Unlike routine config edits (409 while a run is active), conversation edits are never
    blocked: the engine reads routine.yaml only at run boot, each reply is its own boot, and a
    conversation dir has no git commit to race — so refusing during a live reply would only add
    friction. What a live reply DOES with the change is `configflow`'s one classification (F337):
    the live-classified fields are adopted at its next turn boundary, and it is told about the
    rest as taking effect at the next reply.
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
    # validation as the routine page's save path (api_routine_patch._apply_resource_fields).
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
    # F337: a reply already in flight booted from the OLD config. Tell it what changed and
    # which half reaches it now — the same one classification the routine page's PATCH uses,
    # so "I changed it mid-reply" means the same thing in both homes.
    live = signal_config_change(info, list(updates), updates)
    return {"ok": True, "updated": list(updates),
            **({"told_live_run": True} if live else {})}

@router.post("/conversations/{slug}/rules")
def set_conversation_rules(request: Request, slug: str, body: RulesBody) -> dict:
    """Bind/unbind this conversation's general rules — the same implementation routines
    use. A conversation is where this matters most: the work shifts topic mid-thread, and
    a newly bound rule reaches the reply already in flight (control.json) as well as every
    reply after it.
    """
    info = conversation_info(request, slug)
    return apply_rule_edit(request, info.cfg.dir, body, active_run_dir(info))

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
