"""Routine config editing: general rules, the two permission layers, run-now and archive —
the write half of the old api_routines, which keeps the read surfaces (cards, detail,
health, the state graph). The generic validated `PATCH /routines/{slug}` lives in
api_routine_patch.py and routes its two authority keys back through
`write_permission_layers` here; a routine's artifacts and recipe files are
api_routine_files.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import pending_edits
from .. import rules as rules_mod
from ..ids import now_iso, run_ts
from ..paths import read_yaml
from .routines_common import (
    _git_commit,
    _info,
    _state,
    active_run_dir,
    guard_not_active,
    write_routine_config,
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

    `inherited` names permissions the routine holds through its DOMAIN (D82). They RAISE
    nothing — a domain permission must not silently add a capability to this routine's own
    file — but they DO count for the floor, because a capability they legitimately cover is
    not an orphan. Without this, saving a routine's permissions floors away every capability
    its domain supplies (`runs`/`workflows` back to none/catalog); the explicit "off" it
    writes then SHADOWS the domain's value, since a routine's own key always wins.
    """
    from ..grants import capabilities_for, floor_capabilities, normalize_capabilities
    from ..readmodels import library_reads

    available = set(library_reads.doc_slugs(server.permissions_home))
    active = [p for p in body.active if p in available]
    base, problems = normalize_capabilities(
        body.capabilities if body.capabilities is not None else current)
    if body.capabilities is not None and problems:
        raise HTTPException(422, "; ".join(problems))
    lib = library_reads.requires(server.permissions_home)
    # Bind the two layers (D8): RAISE the mapping to cover every held doc's requires, then
    # FLOOR it back to them — a gated action / reserved util / run access survives only as
    # the means of a HELD permission. The permission is the switch; the confirm level and
    # run depth stay as user policy under it. So the saved mapping can never contradict the
    # held permissions (a write_util capability with util-authoring off, etc.).
    caps = floor_capabilities([*active, *(inherited or [])], lib,
                              capabilities_for(active, lib, base))
    return active, caps


def write_permission_layers(server, info, body: PermissionsBody, raw: dict) -> dict:
    """Resolve both permission layers for one save and record in `raw` ONLY what this
    routine owns — the whole of the two-layer write, for every endpoint that performs one.

    Two things happen here and neither is optional. First the cascade
    (`resolve_permission_layers`): unknown docs dropped, the mapping raised to cover every
    held doc's requires and floored back to them, with the DOMAIN's docs counted for the
    floor so a member never loses a capability its domain supplies.

    Then the strip, which is what makes the write safe on a domained routine. The panel and
    `info.cfg.permissions` are both the EFFECTIVE config, in which a doc the domain supplies
    is indistinguishable from one this routine holds itself. Writing that back verbatim makes
    the member's own file own every inherited doc, list entry and dial — and a member's own
    key always wins, so the domain never reaches this routine again (F489: one save flattened
    five inherited docs and three inherited actions into a member's file and widened `runs`
    none → last on the way). `strip_shared_dials` + `strip_shared_list` leave the domain's
    contributions to the domain while keeping every entry the member already had of its own.

    The strip is why this is ONE function rather than a step each caller remembers: the PUT
    performed it and the PATCH did not, so a config_patch that named only a confirm dial
    un-inherited the whole domain block on its way through.

    Returns the three layers for the response (R102: a client that sent a doc which is the
    DOMAIN's must not be told it was saved here, because it was not).
    """
    from ..config.domainconfig import domain_config_for, strip_shared_dials, strip_shared_list

    shared, _ = domain_config_for(info.cfg.dir, info.cfg.domain)
    inherited_docs = list(shared.get("permissions") or [])
    active, caps = resolve_permission_layers(server, body, info.cfg.capabilities or {},
                                             inherited=inherited_docs)
    caps = strip_shared_dials(caps, shared.get("capabilities") or {}, body.capabilities or {})
    before_docs = raw.get("permissions")
    own_active = strip_shared_list(active, inherited_docs,
                                   before_docs if isinstance(before_docs, list) else [])
    before_caps = raw.get("capabilities")
    caps_before: dict = before_caps if isinstance(before_caps, dict) else {}
    shared_caps = shared.get("capabilities") or {}
    for key, val in list(caps.items()):
        if isinstance(val, list) and isinstance(shared_caps.get(key), list):
            before = caps_before.get(key)
            caps[key] = strip_shared_list(val, shared_caps[key],
                                          before if isinstance(before, list) else [])
    raw["permissions"] = own_active
    raw["capabilities"] = caps
    return {"active": active, "own": own_active,
            "inherited": [p for p in inherited_docs if p not in own_active],
            "capabilities": caps}


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
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    layers = write_permission_layers(_state(request).server, info, body, raw)
    write_routine_config(request, info, raw,
                         message=f"permissions: {', '.join(layers['own']) or '(none)'}",
                         fields=["permissions", "capabilities"],
                         values={"permissions": layers["active"],
                                 "capabilities": layers["capabilities"]})
    return {"ok": True, **layers}


@router.post("/routines/{slug}/run")
async def run_now(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    if not info.cfg.enabled:
        raise HTTPException(409, f"routine {slug!r} is disabled — "
                            "choose a schedule before starting it")
    run_id = await _state(request).runner.fire(info.cfg, reason="manual")
    if run_id is None:
        raise HTTPException(409, f"routine {slug!r} already has an active run")
    return {"run_id": run_id}


# Places a routine publishes to that OUTLIVE it, keyed by a marker in its own config.
# `root` is the kit/library path a publisher is granted; `owner` is who can actually remove
# the residue, because residue nobody owns is residue nobody removes — and `owner` therefore
# names whoever CAN act, never whoever sounds responsible. It said "steward-hub-maintainer"
# and nobody could act on that: the kit's api.php has no delete-project op (say · revise ·
# retract · advance · put-state · put-model · put-items · seen · put-progress · lease ·
# invite-*), and no routine has a way onto that host to install one (FTP confinement, D128).
# An owner who cannot perform the removal is the same silence this inventory exists to end.
EXTERNAL_SURFACES = (
    {"marker": "libraries/web/steward",
     "surface": "steward hub",
     "owner": "the operator",
     "locator": "_store/{slug}/ on the steward host",
     "note": "the hub derives a card from the published store directory, so the card stands "
             "until that directory goes — and removing it is a manual host operation: no "
             "routine and no kit api.php operation can delete a project store"},
)


def external_residue(cfg) -> list[dict]:
    """What this routine published OUTSIDE the scheduler, which archiving cannot reach.

    Derived from config the routine ALREADY carries — a publisher is granted the kit's root to
    read it — so nothing has to be declared for a routine to be covered, and a routine cannot
    forget to declare it. Deliberately a REPORT and never an action: this daemon holds no
    credentials for those hosts, and making an archive request publish outward would turn the
    scheduler into a deploy dependency of every host a routine ever wrote to.

    Empty list, never a missing key: a caller must be able to tell "nothing was left behind"
    from "nobody looked".
    """
    roots = [str(r) for r in (getattr(cfg, "fs_read_roots", None) or [])]
    roots += [str(r) for r in (getattr(cfg, "fs_write_roots", None) or [])]
    return [{"surface": s["surface"],
             "owner": s["owner"],
             "locator": s["locator"].format(slug=cfg.slug),
             "note": s["note"]}
            for s in EXTERNAL_SURFACES
            if any(s["marker"] in r for r in roots)]


def _leave_lanes(home: Path, slug: str) -> list[str]:
    """Drop an archived routine from every lane that holds it, returning the lane ids touched.

    CLAUDE.md justifies the missing cascade with "routines are deleted out of band" (F442) —
    but archive IS the in-band deletion, and this is the one moment the web layer knows the
    routine is gone. Left in place, the slug becomes a member with no catalog entry: the next
    chain fire logs `lane member missing`, files a health event, and under `on_failure: stop`
    halts the chain — so every member after it silently stops firing until someone edits the
    lane by hand. F442's tolerance stays for the out-of-band case, which is the one nothing
    can cascade.
    """
    from .. import lanes

    touched = []
    for lane in lanes.list_lanes(home):
        members = [m for m in lane.get("members") or [] if m.get("slug") != slug]
        if len(members) != len(lane.get("members") or []):
            lanes.update(home, lane["id"], members=members)
            touched.append(lane["id"])
    return touched


@router.post("/routines/{slug}/archive")
def archive_routine(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    guard_not_active(request, info)
    home = _state(request).server.routines_home
    target = home / ".archive" / f"{slug}-{run_ts()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Read BEFORE the move: the inventory is derived from the routine's config, and after
    # `shutil.move` there is no routine there to ask.
    residue = external_residue(info.cfg)
    shutil.move(str(info.cfg.dir), str(target))
    # D103: the routine's OWN secrets die with it. They live under the config dir, so the
    # move would otherwise leave live credentials behind with nothing entitled to them —
    # and a later routine reusing the slug would silently inherit them.
    from ..secrets import drop_routine_secrets
    dropped = drop_routine_secrets(slug)
    lanes_left = _leave_lanes(home, slug)
    # A queued mid-run edit for a routine that no longer exists can only fail at replay.
    shutil.rmtree(pending_edits.spool_dir(home, slug), ignore_errors=True)
    _state(request).scheduler.rescan()
    # R1658 / bina, 2026-09-21: archiving used to tidy what it could reach and say nothing
    # about the rest, so a routine's steward card outlived it three times in two weeks and
    # each one was found by the operator's eyes. This is the ONE moment anything knows the
    # routine is gone — spending it in silence is what made the residue invisible. The
    # routine cannot clean up after itself either: publishing needs the credentials the
    # line above has just dropped.
    return {"ok": True, "archived_to": str(target), "ts": now_iso(),
            "secrets_dropped": dropped, "lanes_left": lanes_left,
            "external_residue": residue}
