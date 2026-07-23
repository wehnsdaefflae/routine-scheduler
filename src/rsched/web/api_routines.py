"""Routine CRUD: dashboard cards, detail, config edits (409 while a run is active),
manual fire, archive, file reads.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import registry, schedule
from .. import triggers as triggers_mod
from ..config import MODEL_KINDS
from ..paths import resolve_rel
from ..readmodels.stats import monthly_spend
from . import artifacts
from .api_questions import _snooze_active
from .routines_common import (  # noqa: F401 — re-exported: siblings historically import from here
    _catalog,
    _git_commit,
    _info,
    _state,
    active_run_dir,
    guard_not_active,
    guard_template,
    permission_layers_detail,
)
from .wizard_store import TEMPLATE_SLUG

router = APIRouter(tags=["routines"])

# Above this many unanswered deferred asks, a routine's card flags a decision backlog —
# the operator question is "which routine is silently starving on my input".
DEFERRED_BACKLOG_N = 5

# The dashboard heartbeat strip shows this many recent runs per routine — enough to see a
# flaky week at a glance without growing the card payload (the registry already parses
# every run's status.json, so this is a slice of data in hand, not a new scan).
HEARTBEAT_RUNS_N = 15



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


def _awaiting_questions(info: registry.RoutineInfo) -> list[dict]:
    """Questions genuinely waiting on the user: unanswered AND not snoozed into the
    future. This is the exact visibility the Decisions badge and page apply (they hide
    snoozed items by design — a snoozed decision is deliberately quiet), so a card's
    open-question count can never disagree with the badge.
    """
    now = datetime.now(UTC)
    return [q for q in info.open_questions
            if not q.get("answered") and not _snooze_active(q.get("snoozed_until"), now)]


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
        "open_questions": len(_awaiting_questions(info)),
        # >N unanswered deferred asks = the routine is starving on decisions; the
        # dashboard flags it loud instead of letting the count quietly grow. Snoozed
        # asks are excluded (the user parked them — not silently starving), so the card
        # count matches the Decisions badge.
        "decision_backlog": sum(1 for q in _awaiting_questions(info)
                                if q.get("mode", "deferred") != "blocking")
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
        (server.libraries_home / "workflows" / f"{info.cfg.workflow_slug}.py").exists()
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
        "catchup": info.cfg.catchup,   # skip | run_once when a scheduled fire was missed
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
        # OAuth connection bindings {provider: account}; the picker's options come from
        # GET /api/settings/oauth (the connected accounts).
        "connections": dict(info.cfg.connections),
        # Per-routine secret exposure map (D39): SECRET_NAME → expose (true) / withhold
        # (false); a store secret absent here is asked about on first use. The store names
        # for the editor come from GET /api/settings/secrets.
        "secret_grants": dict(info.cfg.secret_grants),
        # Remote-machine bindings (catalog machine names) + the catalog for the picker; details
        # come from GET /api/settings/machines. A binding to a machine no longer in the catalog
        # is kept as-is (resolves to nothing at run time) — the UI flags it.
        "machines": list(info.cfg.machines),
        "machine_catalog": [{"name": m.name, "description": m.description,
                             "host": m.host, "user": m.user, "tags": list(m.tags)}
                            for m in server.machines.values()],
        "deliberation": info.cfg.deliberation,
        # Practice modules this routine holds — the traits/ dir IS the state (see traits.py);
        # the picker's options come from GET /api/library (`traits`).
        "traits": sorted(p.stem for p in (info.cfg.dir / "traits").glob("*.md")),
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
        "keep_runs": info.cfg.keep_runs,
        # fs roots resolve to absolute server paths on load; the editor shows + saves those.
        "fs_read_roots": [str(p) for p in info.cfg.fs_read_roots],
        "fs_write_roots": [str(p) for p in info.cfg.fs_write_roots],
    }


@router.get("/routines/{slug}/health")
def recipe_health(request: Request, slug: str) -> dict:
    """Health by recipe version: the routine's runs bucketed by the recipe commit that
    produced them (stamped by the engine; pre-stamp history is date-attributed), plus the
    deterministic regression evaluation of the newest recipe change — the routine page's
    health section. Flag-first: reverting is the user's explicit POST below, never
    automatic.
    """
    from ..readmodels.run_health import routine_health

    info = _info(request, slug)
    return routine_health(_state(request).server, info.cfg.dir, slug)


class RevertBody(BaseModel):
    commit: str


@router.post("/routines/{slug}/recipe/revert")
def revert_recipe(request: Request, slug: str, body: RevertBody) -> dict:
    """One-click rollback of a recipe change: restore main.md / stages/ / traits/ /
    tuning.yaml to their state just before `commit` and commit only those paths —
    routine.yaml (the user's config) and state files are never touched. Guarded like
    every web-side routine edit: 409 while a run is active.
    """
    from ..recipes import RecipeError
    from ..recipes import revert_recipe as do_revert

    info = _info(request, slug)
    guard_not_active(request, info)
    try:
        result = do_revert(info.cfg.dir, body.commit)
    except RecipeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@router.get("/routines/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The routine's state graph (its stage modules, in main.md mention order) + the
    current phase (the stage module the latest run last read) — the UI's live diagram;
    phase transitions arrive over the run SSE `state` events.
    """
    from ..readmodels import statemap

    info = _info(request, slug)
    return statemap.state_graph(info.cfg.dir)


@router.get("/routines/{slug}/recipe")
def recipe(request: Request, slug: str) -> dict:
    """The routine's recipe as a navigable tree — main.md + stage modules (in Run-flow order) +
    trait modules, each with its heading outline. Powers the routine page's file browser; edits
    still go through the generic /file endpoint.
    """
    from ..readmodels import statemap

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
