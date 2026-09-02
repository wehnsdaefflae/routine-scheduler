"""Routine config editing: general rules, permissions+capabilities, the PATCH endpoint,
run-now, and archive — the write half of the old api_routines (which keeps the read
surfaces: cards, detail, health, recipe, artifacts).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import rules as rules_mod
from ..ids import now_iso, run_ts
from ..paths import atomic_write_yaml, read_yaml
from .routines_common import (
    _git_commit,
    _info,
    _state,
    active_run_dir,
    guard_not_active,
)

router = APIRouter(tags=["routines"])

class RulesBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []
    # Also withdraw the unbound rules' TEXT from a live run's context, not just their
    # authority. Rewrites the messages carrying it, which invalidates the provider's prompt
    # cache from that point — so it is a deliberate escalation, never the default.
    erase: bool = False


def apply_rule_edit(request: Request, routine_dir: Path, body: RulesBody,
                    active_run_dir: Path | None) -> dict:
    """Bind/unbind general rules on an existing routine or conversation — the ONE
    implementation both homes use. Only the SET changes here; the prose lives in the
    library and is edited on the Library tab, where a revision reaches every holder.

    Deliberately NOT guarded by an active run, unlike other routine file edits: no run
    writes routine.yaml, so the web layer is the only writer and no two-writer race exists.
    When a run IS live, the config alone would not reach it — its prompt was composed at
    boot and is immutable under the prompt-caching contract — so an `add_rules` signal goes
    into the run's control.json and `engine/switches.apply_rule_additions` appends the prose
    at the next turn boundary.

    Removal is now symmetric. "Prose already in the context cannot be unsaid" is true of the
    TEXT and false of its AUTHORITY: telling the run the rule no longer binds costs one
    appended note, so `drop_rules` lands on a live run exactly as `add_rules` does. `erase`
    is the escalation for when the text itself is the problem — it rewrites the messages
    carrying that rule into a tombstone, which INVALIDATES the provider's prompt cache from
    the first edited message on. Opt-in for that reason, and the caller is told the cost.
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
    if active_run_dir is not None:
        from .routines_common import merge_control
        signal: dict = {}
        if added:
            signal["add_rules"] = {"slugs": added, "ts": now_iso()}
        if removed:
            signal["drop_rules"] = {"slugs": removed, "ts": now_iso(),
                                    "erase": bool(body.erase)}
        if signal:
            merge_control(active_run_dir, signal)
    live = bool((added or removed) and active_run_dir is not None)
    return {"ok": True, "added": added, "removed": removed,
            "live": live, "erased": bool(removed and body.erase and live),
            "rules": rules_mod.current_rules(routine_dir)}


@router.post("/routines/{slug}/rules")
def set_routine_rules(request: Request, slug: str, body: RulesBody) -> dict:
    """Bind/unbind this routine's general rules. Applies to a LIVE run too (see
    apply_rule_edit); otherwise it lands at the next run.
    """
    info = _info(request, slug)
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
    from ..config.groupconfig import group_config_for, strip_group_dials
    group_cfg, _ = group_config_for(info.cfg.dir, slug)
    active, caps = resolve_permission_layers(server, body, info.cfg.capabilities or {},
                                             inherited=list(group_cfg.get("permissions") or []))
    # …and record only what DIFFERS from the group, or the concrete dial the floor always emits
    # would shadow the group's value and no later group change could reach this routine.
    caps = strip_group_dials(caps, group_cfg.get("capabilities") or {}, body.capabilities or {})
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    raw["permissions"] = active
    raw["capabilities"] = caps
    atomic_write_yaml(path, raw)
    _git_commit(info.cfg.dir, f"permissions: {', '.join(active) or '(none)'}")
    return {"ok": True, "active": active, "capabilities": caps}


    #                                          dir unlocks recipe self-edit — the improver's lever)


@router.post("/routines/{slug}/run")
async def run_now(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    run_id = await _state(request).runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"routine {slug!r} already has an active run")
    return {"run_id": run_id}


@router.post("/routines/{slug}/archive")
def archive_routine(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_not_active(request, info)
    home = _state(request).server.routines_home
    target = home / ".archive" / f"{slug}-{run_ts()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(info.cfg.dir), str(target))
    # D103: the routine's OWN secrets die with it. They live under the config dir, so the
    # move would otherwise leave live credentials behind with nothing entitled to them —
    # and a later routine reusing the slug would silently inherit them.
    from ..secrets import drop_routine_secrets
    dropped = drop_routine_secrets(slug)
    _state(request).scheduler.rescan()
    return {"ok": True, "archived_to": str(target), "ts": now_iso(),
            "secrets_dropped": dropped}
