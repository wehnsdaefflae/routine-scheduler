"""Routine config editing: traits, permissions+capabilities, the PATCH endpoint,
run-now, and archive — the write half of the old api_routines (which keeps the read
surfaces: cards, detail, health, recipe, artifacts).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import schedule
from .. import traits as traits_mod
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, write_tuning
from ..ids import now_iso, run_ts
from ..paths import atomic_write
from .routines_common import (
    _git_commit,
    _info,
    _state,
    active_run_dir,
    guard_not_active,
    guard_template,
)

router = APIRouter(tags=["routines"])

class TraitsBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []


def apply_trait_edit(request: Request, routine_dir: Path, body: TraitsBody,
                     active_run_dir: Path | None) -> dict:
    """Add/remove practice modules on an existing routine or conversation — the ONE
    implementation both homes use.

    Deliberately NOT guarded by an active run, unlike other routine file edits: a run may
    never write its own `traits/` (the recipe invariant), so the web layer is the only
    writer there and no two-writer race exists. When a run IS live, the durable copy alone
    would not reach it — its prompt was composed at boot and is immutable under the
    prompt-caching contract — so an `add_traits` signal goes into the run's control.json and
    `engine/control.apply_trait_additions` appends the prose at the next turn boundary.
    Removal has no live counterpart on purpose: prose already in the context cannot be
    unsaid, so a removal takes effect at the next run.
    """
    from .. import library_docs

    server = request.app.state.server
    known = set(library_docs.slugs(server.traits_home))
    if unknown := [s for s in body.add if s not in known]:
        raise HTTPException(400, f"unknown practice module(s): {sorted(unknown)}")
    added, removed = traits_mod.apply_changes(server.traits_home, routine_dir,
                                              body.add, body.remove)
    if not added and not removed:
        return {"ok": True, "added": [], "removed": [], "traits": traits_mod.current_traits(
            routine_dir)}
    _git_commit(routine_dir, f"traits via web (+{len(added)}/-{len(removed)})")
    if added and active_run_dir is not None:
        from .api_runs import merge_control
        merge_control(active_run_dir, {"add_traits": {"slugs": added, "ts": now_iso()}})
    return {"ok": True, "added": added, "removed": removed,
            "live": bool(added and active_run_dir is not None),
            "traits": traits_mod.current_traits(routine_dir)}


@router.post("/routines/{slug}/traits")
def set_routine_traits(request: Request, slug: str, body: TraitsBody) -> dict:
    """Add/remove this routine's practice modules. Applies to a LIVE run too (see
    apply_trait_edit); otherwise it lands at the next run.
    """
    info = _info(request, slug)
    guard_template(slug, "the clarification template's practices are fixed")
    return apply_trait_edit(request, info.cfg.dir, body, active_run_dir(info))


class PermissionsBody(BaseModel):
    active: list[str]
    capabilities: dict | None = None   # omitted → keep the routine's current mapping as base


def resolve_permission_layers(server, body: PermissionsBody, current: dict) -> tuple[list, dict]:
    """Validate + cascade one permissions update (shared with conversations): unknown doc
    slugs are dropped, the capabilities mapping is normalized (422 on junk), then RAISED
    until every active doc's requires are covered — so the invariant 'held docs' needs
    are on' holds regardless of what the client sent. Deactivation cascades live in the
    UI (dropping a capability there also unticks the docs requiring it).
    """
    from .. import library_docs
    from ..grants import (
        capabilities_for,
        floor_capabilities,
        normalize_capabilities,
        read_library_requires,
    )

    available = set(library_docs.slugs(server.permissions_home))
    active = [p for p in body.active if p in available]
    base, problems = normalize_capabilities(
        body.capabilities if body.capabilities is not None else current)
    if body.capabilities is not None and problems:
        raise HTTPException(422, "; ".join(problems))
    lib = read_library_requires(server.permissions_home)
    # Bind the two layers (D8): RAISE the mapping to cover every held doc's requires, then
    # FLOOR it back to them — a gated action / reserved util / run access survives only as
    # the means of a HELD permission. The permission is the switch; the confirm level and
    # run depth stay as user policy under it. So the saved mapping can never contradict the
    # held permissions (a write_util capability with util-authoring off, etc.).
    caps = floor_capabilities(active, lib, capabilities_for(active, lib, base))
    return active, caps


@router.put("/routines/{slug}/permissions")
def set_permissions(request: Request, slug: str, body: PermissionsBody) -> dict:
    """Set both permission layers (user-only; a routine can never change its own): the
    held conduct docs AND the capabilities mapping. Pure routine.yaml config, read at run
    start, so changes take effect at the next run. Traits are NOT toggleable here: they
    became the routine's own files at creation.
    """
    info = _info(request, slug)
    # No busy-guard (D35): the engine reads routine.yaml exactly ONCE, at run boot
    # (runtime.run_routine); a save during a live run cleanly applies to the NEXT run.
    server = _state(request).server
    active, caps = resolve_permission_layers(server, body, info.cfg.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["permissions"] = active
    raw["capabilities"] = caps
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"permissions: {', '.join(active) or '(none)'}")
    return {"ok": True, "active": active, "capabilities": caps}


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    schedule: dict | None = None            # {"friendly":…, "catchup":…} (cron built server-side)
    budgets: dict | None = None
    models: dict | None = None              # {main|subroutine|tool_call|uncensored: catalog name}
    connections: dict | None = None         # {provider: account-label} OAuth connection bindings
    machines: list[str] | None = None       # catalog machine names this routine may act on (SSH)
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None           # freeform filter tags (e.g. ["meta"])
    improve: bool | None = None             # include in the routine-improver's passes (default on)
    deliberation: str | None = None         # DELIBERATION_LEVELS — how much thinking lands on paper
    keep_runs: int | None = None            # retention.keep_runs — how many run dirs to keep
    fs_read_roots: list[str] | None = None  # dirs the run may READ beyond its own
    fs_write_roots: list[str] | None = None  # dirs the run may WRITE (one covering the routine
    #                                          dir unlocks recipe self-edit — the improver's lever)


def _apply_resource_fields(raw: dict, updates: dict) -> None:
    """Place the routine.yaml resource fields a PATCH carries that the caller's generic
    top-level merge can't handle on its own: retention.keep_runs (nested under `retention:`),
    the fs roots (validated, stripped — left in `updates` for the wholesale merge), and the
    schedule (a friendly spec → cron + the server's tz, plus the catchup policy). Pops what
    it consumes. A write root covering the routine's own dir unlocks recipe self-editing
    (grants.py) — the user's deliberate choice here, the same lever the routine-improver holds.
    """
    if "keep_runs" in updates:
        n = updates.pop("keep_runs")
        if not isinstance(n, int) or n < 1:
            raise HTTPException(400, "keep_runs must be a positive integer")
        raw.setdefault("retention", {})["keep_runs"] = n
    for roots_key in ("fs_read_roots", "fs_write_roots"):
        if roots_key in updates:
            vals = updates[roots_key] or []
            if not isinstance(vals, list) or any(not isinstance(p, str) or not p.strip()
                                                 for p in vals):
                raise HTTPException(400, f"{roots_key}: must be a list of non-empty path strings")
            updates[roots_key] = [p.strip() for p in vals]
    if "schedule" in updates:
        sched_patch = updates.pop("schedule") or {}
        raw.setdefault("schedule", {})
        if "friendly" in sched_patch:
            try:
                cron = schedule.friendly_to_cron(sched_patch.pop("friendly"))
            except ValueError as exc:
                raise HTTPException(400, f"invalid schedule: {exc}") from exc
            raw["schedule"].update(cron=cron, tz=schedule.server_tz())
        if sched_patch.get("catchup") not in (None, "skip", "run_once"):
            raise HTTPException(400, "catchup must be 'skip' or 'run_once'")
        # merge any remaining RAW keys (cron / tz / catchup) verbatim — a friendly spec was
        # already translated and popped above; tz is preserved when only cron is sent.
        raw["schedule"].update(sched_patch)


@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    # No busy-guard (D35): pure routine.yaml config, read at run START only — saving
    # mid-run applies at the next run. Destructive ops (archive) keep their guard.
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = patch.model_dump(exclude_none=True)
    # deliberation is TUNING, not config — it lands in tuning.yaml (recipe-classed), never in
    # routine.yaml (the user's sealed authority surface). Handle it FIRST, before any raw
    # mutation, so a tuning-only patch returns without rewriting routine.yaml.
    if "deliberation" in updates:
        level = updates.pop("deliberation")
        if level not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level {level!r} "
                                     f"(expected one of {DELIBERATION_LEVELS})")
        write_tuning(info.cfg.dir, {"deliberation": level})
        if not updates:
            _git_commit(info.cfg.dir, "tuning.yaml edit via web (deliberation)")
            _state(request).scheduler.rescan()
            return {"ok": True, "updated": ["deliberation"]}
    # Validate per-routine models: known kinds, each a catalog model NAME. Models REPLACE
    # wholesale (not merge) so blanking a kind clears it back to the system_model fallback.
    if "models" in updates:
        server = _state(request).server
        for kind, name in (updates["models"] or {}).items():
            if kind not in MODEL_KINDS:
                raise HTTPException(
                    400, f"unknown model kind {kind!r} (expected one of {MODEL_KINDS})")
            if not isinstance(name, str) or name not in server.models:
                raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        raw["models"] = updates.pop("models")
    # Validate connection bindings: known provider, non-empty account label; REPLACE wholesale
    # (blanking a provider clears it). Existence of the connection is NOT required — a routine may
    # bind ahead of connecting; the engine injects nothing until the account is connected.
    if "connections" in updates:
        from ..oauth.providers import PROVIDERS
        for prov, account in (updates["connections"] or {}).items():
            if prov not in PROVIDERS:
                raise HTTPException(400, f"unknown connection provider {prov!r}")
            if not isinstance(account, str) or not account:
                raise HTTPException(400, f"connections.{prov}: must be an account label")
        raw["connections"] = updates.pop("connections")
    # Validate machine bindings: each a name in the instance catalog; REPLACE wholesale (an empty
    # list clears them). Unlike connections, we DO require catalog membership — a machine name is
    # meaningless off the catalog, and the picker only offers catalog names.
    if "machines" in updates:
        catalog = _state(request).server.machines
        names = updates["machines"] or []
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise HTTPException(400, "machines: must be a list of catalog machine names")
        for n in names:
            if n not in catalog:
                raise HTTPException(400, f"unknown machine {n!r} (add it in Settings → Machines)")
        raw["machines"] = updates.pop("machines")
    _apply_resource_fields(raw, updates)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"routine.yaml edit via web ({', '.join(updates)})")
    _state(request).scheduler.rescan()
    return {"ok": True, "updated": list(updates)}


@router.post("/routines/{slug}/run")
async def run_now(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_template(slug, "it never runs directly (the wizard starts sessions from it)")
    run_id = await _state(request).runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"routine {slug!r} already has an active run")
    return {"run_id": run_id}


@router.post("/routines/{slug}/archive")
def archive_routine(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_template(slug, "it cannot be archived (sessions copy their config from it)")
    guard_not_active(request, info)
    home = _state(request).server.routines_home
    target = home / ".archive" / f"{slug}-{run_ts()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(info.cfg.dir), str(target))
    _state(request).scheduler.rescan()
    return {"ok": True, "archived_to": str(target), "ts": now_iso()}
