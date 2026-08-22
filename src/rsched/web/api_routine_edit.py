"""Routine config editing: general rules, permissions+capabilities, the PATCH endpoint,
run-now, and archive — the write half of the old api_routines (which keeps the read
surfaces: cards, detail, health, recipe, artifacts).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import rules as rules_mod
from .. import schedule
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

class RulesBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []


def apply_rule_edit(request: Request, routine_dir: Path, body: RulesBody,
                    active_run_dir: Path | None) -> dict:
    """Bind/unbind general rules on an existing routine or conversation — the ONE
    implementation both homes use. Only the SET changes here; the prose lives in the
    library and is edited on the Library tab, where a revision reaches every holder.

    Deliberately NOT guarded by an active run, unlike other routine file edits: no run
    writes routine.yaml, so the web layer is the only writer and no two-writer race exists.
    When a run IS live, the config alone would not reach it — its prompt was composed at
    boot and is immutable under the prompt-caching contract — so an `add_rules` signal goes
    into the run's control.json and `engine/control.apply_rule_additions` appends the prose
    at the next turn boundary. Removal has no live counterpart on purpose: prose already in
    the context cannot be unsaid, so an unbind takes effect at the next run.
    """
    server = request.app.state.server
    try:
        added, removed = rules_mod.apply_changes(server.rules_home, routine_dir,
                                                 body.add, body.remove)
    except KeyError as exc:
        raise HTTPException(400, f"unknown rule: {exc.args[0]!r}") from exc
    if not added and not removed:
        return {"ok": True, "added": [], "removed": [],
                "rules": rules_mod.current_rules(routine_dir)}
    _git_commit(routine_dir, f"rules via web (+{len(added)}/-{len(removed)})")
    if added and active_run_dir is not None:
        from .api_runs import merge_control
        merge_control(active_run_dir, {"add_rules": {"slugs": added, "ts": now_iso()}})
    return {"ok": True, "added": added, "removed": removed,
            "live": bool(added and active_run_dir is not None),
            "rules": rules_mod.current_rules(routine_dir)}


@router.post("/routines/{slug}/rules")
def set_routine_rules(request: Request, slug: str, body: RulesBody) -> dict:
    """Bind/unbind this routine's general rules. Applies to a LIVE run too (see
    apply_rule_edit); otherwise it lands at the next run.
    """
    info = _info(request, slug)
    guard_template(info.cfg, "the clarification template's rules are fixed")
    return apply_rule_edit(request, info.cfg.dir, body, active_run_dir(info))


class PermissionsBody(BaseModel):
    active: list[str]
    capabilities: dict | None = None   # omitted → keep the routine's current mapping as base


def resolve_permission_layers(server, body: PermissionsBody, current: dict,
                              inherited: list[str] | None = None) -> tuple[list, dict]:
    """Validate + cascade one permissions update (shared with conversations): unknown doc
    slugs are dropped, the capabilities mapping is normalized (422 on junk), then RAISED
    until every active doc's requires are covered — so the invariant 'held docs' needs
    are on' holds regardless of what the client sent. Deactivation cascades live in the
    UI (dropping a capability there also unticks the docs requiring it).

    `inherited` names permissions the routine holds through its GROUP (D82). They RAISE
    nothing — a group permission must not silently add a capability to this routine's own
    file — but they DO count for the floor, because a capability they legitimately cover is
    not an orphan. Without this, saving a member's permissions floors away every capability
    its group supplies (`runs`/`workflows` back to none/catalog), and the explicit "off" it
    writes then SHADOWS the group's value, since a member's own key always wins.
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
    caps = floor_capabilities([*active, *(inherited or [])], lib,
                              capabilities_for(active, lib, base))
    return active, caps


@router.put("/routines/{slug}/permissions")
def set_permissions(request: Request, slug: str, body: PermissionsBody) -> dict:
    """Set both permission layers (user-only; a routine can never change its own): the
    held conduct docs AND the capabilities mapping. Pure routine.yaml config, read at run
    start, so changes take effect at the next run. The general rules are config too, but
    they have their own endpoint (they can reach a LIVE run).
    """
    info = _info(request, slug)
    # No busy-guard (D35): the engine reads routine.yaml exactly ONCE, at run boot
    # (runtime.run_routine); a save during a live run cleanly applies to the NEXT run.
    server = _state(request).server
    # D82: permissions this routine holds through its GROUP count for the floor, or saving
    # here would strip every capability the group supplies and write an explicit "off" that
    # then shadows it (a member's own key always wins over the group's).
    from ..config.routine import group_config_for, strip_group_dials
    group_cfg, _ = group_config_for(info.cfg.dir, slug)
    active, caps = resolve_permission_layers(server, body, info.cfg.capabilities or {},
                                             inherited=list(group_cfg.get("permissions") or []))
    # …and record only what DIFFERS from the group, or the concrete dial the floor always emits
    # would shadow the group's value and no later group change could reach this routine.
    caps = strip_group_dials(caps, group_cfg.get("capabilities") or {}, body.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["permissions"] = active
    raw["capabilities"] = caps
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"permissions: {', '.join(active) or '(none)'}")
    return {"ok": True, "active": active, "capabilities": caps}


class RoutinePatch(BaseModel):
    # forbid unknown keys: this is the validated single-writer save path — a misspelled
    # field silently dropped reads as "saved" to a direct API caller (and to the Decisions
    # page's config-patch apply, which verifies its keys against `updated`); a 422 names
    # the stray instead.
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    schedule: dict | None = None            # {"friendly":…, "catchup":…} (cron built server-side)
    budgets: dict | None = None
    models: dict | None = None              # {main|tool_call|uncensored: catalog name}
    connections: dict | None = None         # {provider: account-label} OAuth connection bindings
    grants: dict | None = None              # {entity-id: bool} decision rows (secret exposure
    #                                          + deny-forever tombstones — entities.py)
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
    # `updated` reports every field this PATCH applied. Captured BEFORE the appliers pop
    # what they consume (models/connections/machines/grants/keep_runs/schedule) — the
    # response and commit message must not under-report, because the Decisions page's
    # config-patch apply verifies its patch keys against this list (R102: a key an
    # endpoint silently ignores must read as NOT applied, never as success).
    requested = list(updates)
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
    # Validate the grant-decision rows (entities.py ids → bool); REPLACE wholesale —
    # removing a row returns that entity to undecided (asked on first use / requestable).
    if "grants" in updates:
        from ..entities import normalize_grants
        gmap, gproblems = normalize_grants(updates.pop("grants") or {})
        if gproblems:
            raise HTTPException(400, "; ".join(gproblems))
        raw["grants"] = gmap
    _apply_resource_fields(raw, updates)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(raw.get(key), dict):
            raw[key].update(val)
        else:
            raw[key] = val
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _git_commit(info.cfg.dir, f"routine.yaml edit via web ({', '.join(requested)})")
    _state(request).scheduler.rescan()
    return {"ok": True, "updated": requested}


@router.post("/routines/{slug}/run")
async def run_now(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_template(info.cfg, "it never runs directly (clarify sessions start from it)")
    run_id = await _state(request).runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"routine {slug!r} already has an active run")
    return {"run_id": run_id}


@router.post("/routines/{slug}/archive")
def archive_routine(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_template(info.cfg, "it cannot be archived (sessions copy their config from it)")
    guard_not_active(request, info)
    home = _state(request).server.routines_home
    target = home / ".archive" / f"{slug}-{run_ts()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(info.cfg.dir), str(target))
    _state(request).scheduler.rescan()
    return {"ok": True, "archived_to": str(target), "ts": now_iso()}
