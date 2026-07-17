"""Routine CRUD: dashboard cards, detail, config edits (409 while a run is active),
manual fire, archive, file reads.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import schedule
from .. import triggers as triggers_mod
from ..config import DELIBERATION_LEVELS, MODEL_KINDS, write_tuning
from ..daemon import registry
from ..ids import now_iso, run_ts
from ..paths import atomic_write, resolve_rel
from ..stats import monthly_spend
from . import artifacts
from .wizard_store import TEMPLATE_SLUG

router = APIRouter(tags=["routines"])

# Above this many unanswered deferred asks, a routine's card flags a decision backlog —
# the operator question is "which routine is silently starving on my input".
DEFERRED_BACKLOG_N = 5

# The dashboard heartbeat strip shows this many recent runs per routine — enough to see a
# flaky week at a glance without growing the card payload (the registry already parses
# every run's status.json, so this is a slice of data in hand, not a new scan).
HEARTBEAT_RUNS_N = 15


def guard_template(slug: str, refusal: str) -> None:
    """The 'clarification' routine is the wizard's protected template — configuration the
    user edits, never a runnable/removable routine. 403 keeps it on the page regardless.
    """
    if slug == TEMPLATE_SLUG:
        raise HTTPException(403, f"{slug!r} is the protected clarification template — {refusal}")


def _state(request: Request):
    return request.app.state


def _catalog(request: Request) -> dict[str, registry.RoutineInfo]:
    return registry.scan(_state(request).server)


def _info(request: Request, slug: str) -> registry.RoutineInfo:
    info = _catalog(request).get(slug)
    if info is None:
        raise HTTPException(404, f"no routine {slug!r}")
    return info


def guard_not_active(request: Request, info: registry.RoutineInfo,
                     noun: str = "routine") -> None:
    """409 while a run is active — the web layer edits config/files only between runs
    (shared with conversations, where the 'run' is a live reply).
    """
    if info.active_run or request.app.state.runner.is_active(info.slug):
        raise HTTPException(409, f"{noun} {info.slug!r} is busy (a run is active) "
                                 "— try again after it ends")


def permission_layers_detail(server, cfg, *,
                             routine_only: list[str] | None = None) -> tuple[list[dict], dict]:
    """The two permission layers of a detail payload (shared with conversations): every
    library conduct doc as a toggle row (held ones active; `routine_only` marks the docs a
    conversation greys out), plus the machine-enforced capabilities mapping + its vocabulary.
    """
    from .. import library_docs
    from ..grants import EMPTY_CAPABILITIES, GATED_KINDS

    all_perms = library_docs.list_docs(server.permissions_home)
    held = set(cfg.permissions)
    permissions = [{"slug": p["slug"], "summary": p["summary"], "title": p["title"],
                    "requires": p["requires"], "active": p["slug"] in held,
                    **({"routine_only": p["slug"] in routine_only}
                       if routine_only is not None else {})}
                   for p in all_perms]
    own_caps = cfg.capabilities or {}
    reservable = sorted({u for p in all_perms for u in (p["requires"].get("utils") or [])}
                        | set(own_caps.get("utils") or []))
    capabilities = {"active": {**EMPTY_CAPABILITIES, **own_caps},
                    "vocabulary": {"actions": list(GATED_KINDS), "utils": reservable}}
    return permissions, capabilities


def _git_commit(routine_dir: Path, message: str) -> None:
    if not (routine_dir / ".git").exists():
        return
    subprocess.run(["git", "add", "-A"], cwd=routine_dir, capture_output=True, timeout=30,
                   check=False)
    subprocess.run(["git", "commit", "-qm", message], cwd=routine_dir,
                   capture_output=True, timeout=30, check=False)


def _spend_line(monthly: dict, slug: str) -> dict | None:
    """This month + last month from the durable spend series — the card's compact answer
    to "what does this cost me and is it growing".
    """
    months = monthly.get("months") or []
    cells = (monthly.get("by_routine") or {}).get(slug) or {}
    if not months or not cells:
        return None
    current = months[-1]
    prev = months[-2] if len(months) > 1 else None
    return {"month": current, "current": cells.get(current),
            "prev_month": prev, "prev": cells.get(prev) if prev else None}


def _card(request: Request, info: registry.RoutineInfo, *, monthly: dict | None = None) -> dict:
    sched = _state(request).scheduler
    last = info.last_run
    return {
        "slug": info.slug,
        # the clarification template renders with run/archive hidden (wizard config, not a job)
        "protected": info.slug == TEMPLATE_SLUG,
        "name": info.cfg.name,
        "description": info.cfg.description,
        "enabled": info.cfg.enabled,
        "tags": info.cfg.tags,
        "cron": info.cfg.cron,
        "tz": info.cfg.tz,
        "schedule_desc": schedule.describe(info.cfg.cron),
        "next_fire": (sched.next_fires.get(info.slug).isoformat()
                      if sched.next_fires.get(info.slug) else None),
        "active_run": info.active_run.run_id if info.active_run else None,
        "active_state": info.active_run.state if info.active_run else None,
        "last_run": ({"run_id": last.run_id, "ts": last.ts, "state": last.state,
                      "summary": last.summary[:280], "turns": last.turn,
                      "usage": last.usage, "elapsed_s": last.elapsed_s} if last else None),
        # the heartbeat strip's window, newest first: state + finish outcome (partial is
        # invisible in state) + the hover stats, flattened to keep the payload lean
        "recent_runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state,
                         "outcome": r.outcome, "turns": r.turn,
                         "tokens": (r.usage.get("in") or 0) + (r.usage.get("out") or 0),
                         "cost": r.usage.get("cost") or 0, "elapsed_s": r.elapsed_s}
                        for r in info.runs[:HEARTBEAT_RUNS_N]],
        "open_questions": sum(1 for q in info.open_questions if not q.get("answered")),
        # >N unanswered deferred asks = the routine is starving on decisions; the
        # dashboard flags it loud instead of letting the count quietly grow.
        "decision_backlog": sum(1 for q in info.open_questions
                                if not q.get("answered")
                                and q.get("mode", "deferred") != "blocking")
                            > DEFERRED_BACKLOG_N,
        "problems": info.problems,
        "improve": info.cfg.improve,
        **({"spend": _spend_line(monthly, info.slug)} if monthly is not None else {}),
    }


@router.get("/routines")
def list_routines(request: Request) -> list[dict]:
    monthly = monthly_spend(_state(request).server)   # one read serves every card
    return [_card(request, info, monthly=monthly) for info in _catalog(request).values()]


@router.get("/routines/{slug}")
def routine_detail(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    server = _state(request).server
    d = info.cfg.dir
    ledger = d / "LEDGER.md"
    ledger_tail = ""
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger_tail = "\n".join(lines[-100:])
    # editable routine files by directory (stage modules + the routine's own trait copies + state)
    files = {}
    for sub in ("stages", "traits", "state"):
        subdir = d / sub
        files[sub] = ([p.name for p in sorted(subdir.iterdir())
                       if p.is_file() and p.suffix == ".md"]
                      if subdir.is_dir() else [])
    # the two permission layers: all library conduct docs → toggle list (held ones are
    # this routine's), plus the machine-enforced capabilities mapping + its vocabulary
    permissions, capabilities = permission_layers_detail(server, info.cfg)
    in_library = bool(info.cfg.workflow_slug) and \
        (server.library_home / "workflows" / f"{info.cfg.workflow_slug}.py").exists()
    monthly = monthly_spend(server)
    # uncensored-referral audit: how often a turn/llm call was answered by the uncensored
    # model (durable stream; the current month rides spend.current.referrals)
    referrals_total = sum(int(c.get("referrals") or 0)
                          for c in (monthly["by_routine"].get(slug) or {}).values())
    return {
        **_card(request, info, monthly=monthly),
        "referrals_total": referrals_total,
        "schedule_friendly": schedule.cron_to_friendly(info.cfg.cron),
        "server_tz": schedule.server_tz(),
        # Provenance is a CLAIM ("generated from") — in_library says whether the referenced
        # pattern actually exists in the current library, so the UI never implies a findable
        # workflow that isn't there (hand-authored recipes carry an empty slug).
        "workflow_ref": {"slug": info.cfg.workflow_slug, "commit": info.cfg.workflow_commit,
                         "in_library": in_library},
        # Per-routine model roles (main/subroutine/tool_call/uncensored) — each a catalog model
        # NAME, or null to fall back to the server system_model. `catalog` populates the picker.
        "models": {k: (info.cfg.models.get(k) or None) for k in MODEL_KINDS},
        "catalog": list(server.models.keys()),
        "system_model": server.system_model or None,
        "deliberation": info.cfg.deliberation,
        # event triggers (webhook now): config rows + fire ledger + hook URL paths — the
        # Triggers card renders these; CRUD lives in api_hooks
        "triggers": triggers_mod.describe_triggers(server.routines_home, slug,
                                                   info.cfg.triggers),
        "permissions": permissions,
        "capabilities": capabilities,
        "ledger_tail": ledger_tail,
        "files": files,
        "questions": info.open_questions,
        "runs": [{"run_id": r.run_id, "ts": r.ts, "state": r.state,
                  "summary": r.summary[:200], "turn": r.turn, "usage": r.usage,
                  "elapsed_s": r.elapsed_s}
                 for r in info.runs[:50]],
        "budgets": info.cfg.budgets,
    }


@router.get("/routines/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The routine's state graph (its stage modules, in main.md mention order) + the
    current phase (the stage module the latest run last read) — the UI's live diagram;
    phase transitions arrive over the run SSE `state` events.
    """
    from .. import statemap

    info = _info(request, slug)
    return statemap.state_graph(info.cfg.dir)


@router.get("/routines/{slug}/recipe")
def recipe(request: Request, slug: str) -> dict:
    """The routine's recipe as a navigable tree — main.md + stage modules (in Run-flow order) +
    trait modules, each with its heading outline. Powers the routine page's file browser; edits
    still go through the generic /file endpoint.
    """
    from .. import statemap

    info = _info(request, slug)
    return statemap.recipe_tree(info.cfg.dir)


@router.get("/routines/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    """Everything under <routine>/artifacts/ — the routine's deliverables, newest first
    (the conversations panel's counterpart).
    """
    info = _info(request, slug)
    return artifacts.list_artifacts(info.cfg.dir)


@router.get("/routines/{slug}/artifact")
def get_artifact(request: Request, slug: str, path: str):
    """Serve one artifact raw (blob-rendered client-side). ONLY artifacts/ is servable
    here — routine config/recipe reads stay on the JSON /file endpoint.
    """
    info = _info(request, slug)
    return artifacts.serve_file(info.cfg.dir, path)


@router.get("/routines/{slug}/file")
def get_routine_file(request: Request, slug: str, path: str) -> dict:
    info = _info(request, slug)
    try:
        p = resolve_rel(info.cfg.dir, path)
        return {"path": path, "content": p.read_text(encoding="utf-8")}
    except (PermissionError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


class RoutineFileBody(BaseModel):
    path: str
    content: str


@router.put("/routines/{slug}/file")
def put_routine_file(request: Request, slug: str, body: RoutineFileBody) -> dict:
    """Edit any of the routine's own files — main.md, stage modules, traits, state, or routine.yaml.
    A routine owns its recipe (materialized in), so main.md, stages/ and traits/ ARE editable here.
    This is the USER editing via the web (guarded while a run is active) — distinct from a run,
    which may never write its own recipe or config.
    """
    info = _info(request, slug)
    guard_not_active(request, info)
    try:
        p = resolve_rel(info.cfg.dir, body.path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    _git_commit(info.cfg.dir, f"edit {body.path} via web")
    return {"ok": True}


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
    guard_not_active(request, info)
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
    schedule: dict | None = None            # {"friendly": {...}} — converted to cron server-side
    budgets: dict | None = None
    models: dict | None = None              # {main|subroutine|tool_call|uncensored: catalog name}
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None           # freeform filter tags (e.g. ["meta"])
    improve: bool | None = None             # include in the routine-improver's passes (default on)
    deliberation: str | None = None         # DELIBERATION_LEVELS — how much thinking lands on paper


@router.patch("/routines/{slug}")
def patch_routine(request: Request, slug: str, patch: RoutinePatch) -> dict:
    info = _info(request, slug)
    guard_not_active(request, info)
    path = info.cfg.dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = patch.model_dump(exclude_none=True)
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
    # deliberation is TUNING, not config — it lands in tuning.yaml (recipe-classed), never
    # in routine.yaml (the user's sealed authority surface).
    if "deliberation" in updates:
        level = updates.pop("deliberation")
        if level not in DELIBERATION_LEVELS:
            raise HTTPException(400, f"deliberation: unknown level {level!r} "
                                     f"(expected one of {DELIBERATION_LEVELS})")
        write_tuning(info.cfg.dir, {"deliberation": level})
        if not updates:   # a tuning-only patch never rewrites routine.yaml
            _git_commit(info.cfg.dir, "tuning.yaml edit via web (deliberation)")
            _state(request).scheduler.rescan()
            return {"ok": True, "updated": ["deliberation"]}
    # Translate the friendly schedule → cron + the server's own timezone (never asked of the user).
    if "schedule" in updates and "friendly" in updates["schedule"]:
        try:
            cron = schedule.friendly_to_cron(updates["schedule"]["friendly"])
        except ValueError as exc:
            raise HTTPException(400, f"invalid schedule: {exc}") from exc
        raw.setdefault("schedule", {})
        raw["schedule"].update(cron=cron, tz=schedule.server_tz())
        updates.pop("schedule")
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
