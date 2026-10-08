"""The routine READ surfaces: dashboard cards, the detail payload, the setup surface, the
recommendations, recipe health and its revert, the state graph, and a routine's own local
reminders.

The write half is `api_routine_edit` (rules, permissions, run-now, archive) and
`api_routine_patch` (the one validated config writer); a routine's artifacts and recipe FILES
are `api_routine_files`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import lanes, registry, schedule
from .. import triggers as triggers_mod
from ..config import ROUTINE_MODEL_ROLES
from ..config.base import DEFAULT_LADDER, DEFAULT_RUNG_HEIGHT
from ..engine.ladder import oversight_turns_for
from ..readmodels.stats import monthly_spend
from .api_tasks import layer_on
from .config_fields import decision_picker
from .decisions_read import _snooze_active
from .routines_common import (
    _catalog,
    _info,
    _state,
    guard_not_active,
    permission_layers_detail,
    queue_or_apply,
)

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


def _schedule_fields(info: registry.RoutineInfo, lane: dict | None, *,
                     own_fires: dict[str, datetime], lane_fires: dict[str, datetime]) -> dict:
    """The card's `schedule_desc` + `next_fire`: when the scheduler actually STARTS this routine.

    A member of a scheduled lane is fired by the lane (D71), so its own cron, empty or
    suppressed, names no time it runs at. Read from that cron, every lane member's page said
    "Manual — runs only when you click Run now" beside a lane tile saying the lane fires it;
    a member that kept an old cron showed a daily time the scheduler never uses. `next_fire` is
    the lane's: the moment its chain starts, which a later member follows. A paused lane has
    none; neither has a member that is switched off or retired, which the chain skips.
    """
    if lane is None:
        own = own_fires.get(info.slug)
        return {"schedule_desc": schedule.describe(info.cfg.cron),
                "next_fire": own.isoformat() if own else None}
    when = "lane paused" if lane.get("paused") else schedule.describe(lane["cron"])
    nxt = lane_fires.get(lane["id"]) if info.fireable else None
    return {"schedule_desc": f"Lane “{lane['name']}” — {when}",
            "next_fire": nxt.isoformat() if nxt else None}


def _card(request: Request, info: registry.RoutineInfo, *, monthly: dict,
          lane_of: dict[str, dict]) -> dict:
    """One routine's dashboard card. `monthly` (the spend series) and `lane_of`
    (`lanes.scheduled_lane_by_member`) are read ONCE by the caller for every card it builds.
    """
    sched = _state(request).scheduler
    last = info.last_run
    return {
        "slug": info.slug,
        "name": info.cfg.name,
        "description": info.cfg.description,
        "enabled": info.cfg.enabled,
        # FINISHED, not switched off. It reached its finish line, so the scheduler stops
        # firing this routine (registry.RoutineInfo.retired) — derived from its own finish
        # line, nothing written. The two must read differently everywhere: one
        # routine is DONE, the other was turned off, and a single "disabled" chip said neither.
        "retired": info.retired,
        "tags": info.cfg.tags,
        "cron": info.cfg.cron,
        "tz": info.cfg.tz,
        **_schedule_fields(info, lane_of.get(info.slug), own_fires=sched.next_fires,
                           lane_fires=sched.lane_next_fires),
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
        "spend": _spend_line(monthly, info.slug),
    }


@router.get("/routines")
def list_routines(request: Request) -> list[dict]:
    monthly = monthly_spend(_state(request).server)   # one read serves every card
    lane_of = lanes.scheduled_lane_by_member(_state(request).server.routines_home)
    return [_card(request, info, monthly=monthly, lane_of=lane_of)
            for info in _catalog(request).values()]


@router.get("/routines/{slug}/surface")
def routine_setup_surface(request: Request, slug: str) -> dict:
    """What this routine still needs — the forward reading of the dependency graph.

    Derived live on every call, never stored: the library moves under a routine (a run may
    revise the utils and rules it is made of), so a persisted answer would be stale the first
    time somebody ran write_util.
    """
    from ..readmodels.surface import routine_surface

    return routine_surface(_state(request).server, _info(request, slug).cfg)


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
    # editable routine files by directory (stage modules + state). General rules are not
    # here: they live in the library, edited on the Library tab, shared by every holder.
    files = {}
    for sub in ("stages", "state"):
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
    lane_of = lanes.scheduled_lane_by_member(server.routines_home)
    lane = lane_of.get(info.slug)
    # uncensored-referral audit: how often a turn/llm call was answered by the uncensored
    # model (durable stream; the current month rides spend.current.referrals)
    referrals_total = sum(int(c.get("referrals") or 0)
                          for c in (monthly["by_routine"].get(slug) or {}).values())
    return {
        **_card(request, info, monthly=monthly, lane_of=lane_of),
        "referrals_total": referrals_total,
        # the heading this routine's card sits under on the Steward hub — identity, edited in
        # the page's identity section and named to the run in its harness contract
        "hub_tab": info.cfg.hub_tab,
        "schedule_friendly": (schedule.cron_to_friendly(info.cfg.cron) if info.cfg.enabled
                              else {"frequency": "disabled"}),
        "server_tz": schedule.server_tz(),
        "catchup": info.cfg.catchup,   # skip | run_once when a scheduled fire was missed
        # D141 (operator, 2026-09-21: "on the config page beside the schedule"): the
        # pre-engine admission gate. It belongs to the schedule payload because it decides
        # WHETHER a scheduled fire becomes a run at all — the config page renders it inside
        # the Schedule section, saved by that section's one save button.
        "run_gate": info.cfg.run_gate.model_dump(),
        # D71: set when a SCHEDULED lane contains this routine — its own cron is
        # suppressed and the Schedule dropdown renders the "lane managed" state, linking to
        # the lane. At most one lane can match: membership is exclusive (rsched.lanes). The
        # same lookup the card's schedule fields read, so the two can never disagree.
        "lane_managed": {"id": lane["id"], "name": lane["name"]} if lane else None,
        # Provenance is a CLAIM ("generated from") — in_library says whether the referenced
        # pattern actually exists in the current library, so the UI never implies a findable
        # workflow that isn't there (hand-authored recipes carry an empty slug).
        "workflow_ref": {"slug": info.cfg.workflow_slug, "commit": info.cfg.workflow_commit,
                         "in_library": in_library},
        # Per-routine model roles (main/tool_call/uncensored) — each a catalog model
        # NAME, or null to fall back to the server system_model. `catalog` populates the picker.
        # The two decision roles name a DECISION model, null falling back to the instance's
        # decision defaults; their pickers read `decision_catalog` (empty → no rows at all).
        "models": {k: (info.cfg.models.get(k) or None) for k in ROUTINE_MODEL_ROLES},
        "catalog": list(server.models.keys()),
        "system_model": server.system_model or None,
        **decision_picker(server),
        # OAuth connection bindings {provider: account}; the picker's options come from
        # GET /api/settings/oauth (the connected accounts).
        "connections": dict(info.cfg.connections),
        # Grant-decision rows (entities.py ids → bool): secret exposure (`secret:<NAME>`,
        # true/false — absent is asked on first use, D39) plus the deny-forever tombstones
        # for every other entity class. The store names for the secret editor come from
        # GET /api/settings/secrets.
        "grants": dict(info.cfg.grants),
        # Remote-machine bindings (catalog machine names) + the catalog for the picker; details
        # come from GET /api/settings/machines. A binding to a machine no longer in the catalog
        # is kept as-is (resolves to nothing at run time) — the UI flags it.
        "machines": list(info.cfg.machines),
        "machine_catalog": [{"name": m.name, "description": m.description,
                             "host": m.host, "user": m.user, "tags": list(m.tags)}
                            for m in server.machines.values()],
        "deliberation": info.cfg.deliberation,
        # The ESCALATION LADDER, split across its two authority classes (docs/architecture.md):
        # `ladder` is CONFIG — whether a run of this routine is supervised at all and the
        # ceiling on ladder height — because authority over being watched is the user's, so it
        # saves through the accept bar. The two INTERVAL knobs are tuning (recipe-classed), so a
        # meta-routine may re-level them on measured evidence; `oversight_turns` is sent as the
        # stored value or null, and null means DERIVED (`n // 2 + 1`, floored) rather than unset —
        # the control says so instead of showing an empty box that looks like zero.
        "ladder": {**DEFAULT_LADDER, **(info.cfg.ladder or {})},
        "ladder_rung_height": info.cfg.tuning.get("ladder_rung_height") or DEFAULT_RUNG_HEIGHT,
        "oversight_turns": info.cfg.tuning.get("oversight_turns"),
        "oversight_turns_derived": oversight_turns_for(
            int(info.cfg.tuning.get("ladder_rung_height") or DEFAULT_RUNG_HEIGHT)),
        "max_subrun_depth": info.cfg.budgets.get("max_subrun_depth"),
        # The general rules binding this routine — routine.yaml's `rules:` IS the state
        # (see rules.py); the picker's options come from GET /api/library (`rules`).
        "rules": list(info.cfg.rules),
        # event triggers (webhook now): config rows + fire ledger + hook URL paths — the
        # Triggers card renders these; CRUD lives in api_hooks
        "triggers": triggers_mod.describe_triggers(server.routines_home, slug,
                                                   info.cfg.triggers),
        "permissions": permissions,
        "capabilities": capabilities,
        # the task layer (rsched/tasks.py): on, the overview carries the routine's task list,
        # read from GET …/tasks — the payload says only whether there is one to read
        "tasks_enabled": layer_on(info.cfg),
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
    """The Recipe health section of a routine's development view (#/changes/<slug>): runs
    bucketed by the recipe commit that produced
    them (stamped by the engine; pre-stamp history is date-attributed) with the regression
    evaluation of the newest recipe change; `trend`, the same evaluation over the last two
    run WINDOWS regardless of version — what a shared library revision moves and no recipe
    commit records; and `endings`, which of this routine's partial finishes a budget forced.
    Flag-first: reverting is the user's explicit POST below, never automatic.
    """
    from ..readmodels.run_health import routine_health

    info = _info(request, slug)
    return routine_health(_state(request).server, info.cfg.dir, slug)


class RevertBody(BaseModel):
    commit: str


@router.post("/routines/{slug}/recipe/revert")
def revert_recipe(request: Request, slug: str, body: RevertBody) -> dict:
    """One-click rollback of a recipe change: restore main.md / stages/ / tuning.yaml
    to their state just before `commit` and commit only those paths —
    routine.yaml (the user's config) and state files are never touched. Queued while a run
    is active and applied when it ends (D78-A), by the same `recipes.revert_recipe`.
    """
    from ..recipes import RecipeError
    from ..recipes import revert_recipe as do_revert

    info = _info(request, slug)

    def _apply() -> dict:
        try:
            result = do_revert(info.cfg.dir, body.commit,
                               routines_home=_state(request).server.routines_home)
        except RecipeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, **result}

    # D78-A: queue while a run is active (apply at run end) instead of a 409 busy toast
    return queue_or_apply(request, info, "recipe_revert", {"commit": body.commit}, _apply)


@router.get("/routines/{slug}/stategraph")
def stategraph(request: Request, slug: str) -> dict:
    """The routine's state graph (its stage modules, in main.md mention order) + the
    current phase (the stage module the latest run last read) — the UI's live diagram;
    phase transitions arrive over the run tail's `state` events.
    """
    from ..readmodels import statemap

    info = _info(request, slug)
    return statemap.state_graph(info.cfg.dir)


@router.get("/routines/{slug}/recipe")
def recipe(request: Request, slug: str) -> dict:
    """The routine's recipe as a navigable tree — main.md + stage modules (in Run-flow order),
    each with its heading outline. Powers the routine page's file browser; edits still go
    through the generic /file endpoint.
    """
    from ..readmodels import statemap

    info = _info(request, slug)
    return statemap.recipe_tree(info.cfg.dir)


@router.delete("/routines/{slug}/reminders/{rid}")
def delete_local_reminder(request: Request, slug: str, rid: str) -> dict:
    """Drop one of this routine's OWN reminders, tally and all.

    A local reminder is written by the run with no approval — that is the whole point of the
    local rung — so the user's only lever over one is here. Without it a run that left itself
    a bad pattern would keep paying a turn for it every time the pattern matched, and the only
    remedy would be hand-editing `state/reminders.json` on the server.

    Local only: a curated reminder is the library's copy, removed on the Library tab, and its
    tally here is this routine's evidence about it rather than the reminder itself. Deleting a
    local one takes its tally with it, because the tally is about a definition that is going.

    Refused (409) while a run is active. A live run holds its reminders in memory and keeps
    holding actions on them, and its next `remind` rewrites the file with the definitions it
    holds (`engine/remind._save_local`) — so a delete landing mid-run reached nothing and was
    then silently undone, under a 200.
    """
    from .. import reminders

    info = _info(request, slug)
    if not reminders.is_reminder_id(rid):
        raise HTTPException(400, f"not a reminder id: {rid!r}")
    guard_not_active(request, info)
    local, gstats = reminders.load_local(info.cfg.dir)
    kept = [r for r in local if r.id != rid]
    if len(kept) == len(local):
        raise HTTPException(404, f"{slug} has no local reminder {rid!r}")
    reminders.save_local(info.cfg.dir, kept, gstats)
    return {"ok": True, "remaining": len(kept)}
