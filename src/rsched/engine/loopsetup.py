"""Constructing an EngineLoop — the decisions made once, before the first turn."""

from __future__ import annotations

import threading
from collections import deque

from ..policyload import load_policy
from . import (
    chain,
    loopnudge,
    reviseleg,
    runkind,
)
from .loopnudge import REPEAT_FAIL
from .run_context import RunContext
from .subruns import SubrunManager


def _take_invited_revisions(ctx: RunContext) -> bool:
    """Consume the revisions another routine proposed for THIS one and file them as this leg's
    instruction — True when there were any, which is what unlocks the recipe for the leg (D198).

    Ordinary routine runs only (not a child, conversation or detached run). Best-effort: a
    broken spool leaves the invitation for the next fire rather than failing the boot.
    """
    if ctx.depth > 0 or runkind.is_conversation(ctx) or runkind.is_detached_run(ctx):
        return False
    try:
        from .. import reciperevise
        from . import inbox
        records = reciperevise.take(ctx.server.routines_home, ctx.routine.slug)
        if not records:
            return False
        # `web-converse` is a LIVE via, so this leg's own boot drains it.
        inbox.file_message(ctx.routine.dir, reciperevise.instruction(records),
                           via="web-converse")
    except OSError:
        return False
    return True


def _revise_unlock(loop, ctx: RunContext, *, resume: bool) -> tuple[bool, bool]:
    """`(revising, narrow)` for this leg — whether its recipe is unlocked, and whether the leg
    is a NARROW one (an approved recipe-revision card, engine/reviseleg.py).

    The /revise marker is read once and cleared here; an unlocked leg gains the file-edit kinds
    even where `tools:` omits them. An invited revision from another routine (D198,
    rsched/reciperevise.py) is the same one-leg unlock, consumed at init so a run is never
    rewritten mid-flight. It is taken before a full-leg marker (so it is not skipped unread),
    but not on a narrow leg, which makes only the edit its card approved.
    """
    from .revise import REVISE_KINDS, clear_revise_marker, is_narrow, revise_marker
    marker = revise_marker(ctx.run_dir)
    narrow = resume and is_narrow(marker)
    revising = narrow or _take_invited_revisions(ctx) or marker is not None
    if revising:
        clear_revise_marker(ctx.run_dir)
        if loop.allowed_tools is not None:
            loop.allowed_tools |= set(REVISE_KINDS)
    return revising, narrow


def build_base_policy(loop, grants_map: dict) -> None:
    """Set `loop.base_grants` — the run's CONFIG-derived grant policy, before the one-time
    overlay `requests.rebuild_policy` folds over it.

    ONE builder, shared with the live `grants` PATCH (`switches._adopt`): a second copy of
    these arguments drifts, silently dropping the recipe unlock, `admin` or a child's scope.
    """
    loop.base_grants = load_policy(loop.ctx.server.permissions_home,
                                   loop.ctx.routine.permissions,
                                   loop.ctx.routine.capabilities,
                                   current_run_ts=loop.ctx.run_ts,
                                   recipe_unlocked=bool(loop._recipe_unlocked),
                                   admin=loop.admin_leg,
                                   grants_map=grants_map)
    if loop.ctx.depth == 0 and runkind.is_root_conversation(loop.ctx):
        # `detach` is STRUCTURAL: a job that outlives the reply can only be delivered back
        # into a conversation, so a root conversation holds it and nothing else does.
        from dataclasses import replace
        loop.base_grants = replace(loop.base_grants,
                                   actions=loop.base_grants.actions | {"detach"})
    if loop.ctx.depth > 0:
        # A child: capabilities are off by design, so a denial must name the child scope; and
        # its brief, not the archive, is its context (run_history "none").
        from dataclasses import replace
        loop.base_grants = replace(loop.base_grants, is_subrun=True, run_history="none")


def configure(loop, ctx: RunContext, workflow_body: str, instruction: str,
                 abort_event: threading.Event | None = None,
                 allowed_tools: list[str] | None = None, resume: bool = False) -> None:
    """Everything an EngineLoop decides ONCE, before its first turn.

    The load-bearing part is which kinds this run may emit: the workflow's `tools:` allowlist,
    intersected with the routine's capabilities, plus structural rules (a child holds nothing
    gated; creation kinds reach top-level runs only). The action SCHEMA is projected from that
    set, so a kind the run may not use is unrepresentable, not merely refused.
    """
    loop.ctx = ctx
    # Import the finish path NOW, while the build on disk is the one this process booted with —
    # a run that ships a release would otherwise load a newer gate at its finish (F677).
    from . import finishstands
    finishstands.pin()
    loop.workflow_body = workflow_body
    loop.instruction = instruction
    loop.resume = resume     # rehydrate the prior transcript instead of a clean start
    # The workflow's `tools:` allowlist (None = every kind); `finish` is always permitted.
    # `is not None`, NOT a falsy test (D242): `tools: []` is a restriction, and must never
    # fail open into "unrestricted".
    loop.allowed_tools = (set(allowed_tools) | {"finish"}
                          if allowed_tools is not None else None)
    loop.abort_event = abort_event or threading.Event()
    # The same check, where the jailed runner can ask it: a util, script or shell command in
    # flight ends with this run (RunContext.aborted, utils_run.run_jailed).
    ctx.aborted = loop._aborted
    loop.subruns = SubrunManager(loop)
    loop.messages = []
    loop.turn_records = []
    loop.failures = {}       # failure_key → how often that call failed this run
    loop.repeat_hashes = deque(maxlen=REPEAT_FAIL)
    loop.consumed_dir = ctx.root_run_dir / "consumed"
    loop.final_summary = ""
    loop.dialog_qids = {}    # records an ask-back left open, by (type, subject) — askback.py;
    #                          a resumed leg re-keys them from the transcript
    loop.executed_actions = 0  # actions that produced an observation this run
    loop.executed_kinds = set()   # …and their KINDS: what the claim guard may call taken (D243)
    chain.configure(loop)      # this reply's action queue and what it has read
    loop._schema_storm_streak = 0   # consecutive retry-burdened turns (D87, SCHEMA_STORM_TURNS)
    # This leg's wake, set in boot. After an authored finish (`leg_after_authored`) a resuming
    # message that only EXECUTES commands keeps the turn with the user; prose hands it to the
    # model. A run with its own work (a scheduled fire, crash recovery) always proceeds.
    loop.leg_commands = False
    loop.leg_prose = False
    loop.leg_after_authored = False
    # Gated capabilities come from the routine's CAPABILITIES mapping — user-set config a run
    # cannot grant itself. Own-recipe writes are their own capability (`write_recipe`), never a
    # side effect of a write root covering the routine dir; routine.yaml stays sealed either way.
    unlocked = "write_recipe" in ((ctx.routine.capabilities or {}).get("actions") or [])
    # A "revise recipe" leg gets recipe write + the file-edit kinds for THIS leg only.
    revising, narrow = _revise_unlock(loop, ctx, resume=resume)
    # D62: an ADMIN conversation leg (one-shot marker the web layer dropped) lifts CAPABILITY
    # gating for this leg only; structural gates still apply. Root conversations only.
    from .admin import admin_marker, clear_admin_marker
    loop.admin_leg = admin_marker(ctx.run_dir) and runkind.is_root_conversation(ctx)
    clear_admin_marker(ctx.run_dir)
    # Creation kinds reach every depth-0 run; the HANDLER decides between materializing (root
    # conversation) and queuing a proposal (F328). Never a child: its proposal traces to nothing.
    if loop.allowed_tools is not None and ctx.depth == 0:
        loop.allowed_tools |= {"create_routine", "manage_lane"}
    # `decide` needs a configured DECISION model; without one the kind is projected out.
    from .decideaction import available as decide_available
    if not decide_available(ctx.server):
        from .actionschema import KINDS
        loop.allowed_tools = (set(KINDS) if loop.allowed_tools is None
                              else loop.allowed_tools) - {"decide"}
    # LAST of the kind decisions: a narrow revise leg holds the recipe edit kinds and nothing
    # the widenings above added (engine/reviseleg.py).
    reviseleg.configure(loop, narrow=narrow)
    loop._recipe_unlocked = unlocked or revising
    build_base_policy(loop, ctx.routine.grants)
    loop.grants = ctx.grants = loop.base_grants.with_overlay(ctx.granted_now,
                                                             ctx.denied_now)
    loop.util_reminder = loopnudge.build_util_reminder(loop)
    loop._last_switch_ts = ""   # edge-trigger for mid-run model switches (control.json)
    loop._last_deliberation_ts = ""   # edge-trigger for mid-run deliberation switches
    loop._last_rules_ts = ""    # edge-trigger for user-bound general rules
    loop._last_rule_drop_ts = ""   # …and for unbinding one mid-run (symmetric)
    loop._last_config_ts = ""   # edge-trigger for a live config PATCH (F337)
    loop._challenged = set()   # verifier objections, at most once each (finishgate.rebuild)
    # The run's applied ledger seeds the edge-triggers, so an earlier leg's signal won't re-fire.
    from .switches import load_applied_baselines
    load_applied_baselines(loop)
    ctx.deliberation = ctx.routine.deliberation   # live level; control.json may re-set it
    # Repeat-streak escape hatch: identical valid actions in a row signal grammar distortion, so
    # at REPEAT_WARN the next completion runs schema-free. After two rescues the provider schema
    # stays OFF for the rest of the run.
    loop._shed_schema_turns = 0
    loop._sheds = 0
    loop._schema_off = False
    # The transport's schema, projected onto the kinds this run may emit (as the prompt shows);
    # a mid-run grant (requests.apply_decision) re-projects it.
    from .kindsurface import effective_kinds, schema_for_kinds
    loop.action_schema = schema_for_kinds(effective_kinds(loop.allowed_tools, ctx.grants),
                                          reminders=bool(ctx.grants and ctx.grants.reminders_on))
    # When the prompt carries only the field index, a turn served with no grammar is handed the
    # full contract once as an ENGINE NOTE (`turntakeover._contract_if_unenforced`).
    from . import schemaindex
    loop._schema_is_index = schemaindex.wire_enforces(ctx)
    loop._schema_contract_sent = False
    # Per-loop layers each initialise their OWN state (a resumed leg re-seeds the once-only
    # ledgers from the transcript — engine/guardscope.py).
    from . import archival, assist, background, hold, loopshape, recall, remind
    for layer in (hold, remind, assist, recall, archival, background, loopshape):
        layer.configure(loop)
    # After archiving, the history-index reminder rides right after each compaction, then every
    # 10th turn — not every turn, which would be pure rent on the context.
    loop._history_active = False
    loop._hist_note_countdown = 0
    # The RESERVED FINISH turn: the first budget violation grants one finish-only turn, so the
    # summary is always authored; a second force-finishes (loopnudge.reserve_finish).
    loop._finish_reserved = False
    loop._budget_spent = None    # which budget spent it: {resource, limit, message}
    loop._last_compact_after = 0   # post-compaction size; gates re-compaction (anti-thrash)
    loop._evict_warned = False   # the one-turn warning before the middle is elided
    loop._evict_owed = None      # (cap, stage, economics) of the pass that warning deferred
    loop._stage_mark = None      # where the run stood last turn — the stage-boundary edge
    # Provider-calibrated token ratio; 1.0 until a completion reports (window.note_prompt_size).
    loop._token_ratio = 1.0
    try:
        hist_rel = str((ctx.run_dir / "history").relative_to(ctx.routine.dir))
    except ValueError:
        hist_rel = "history"
    loop._hist_rel = hist_rel
    loop._history_note = (
        f"\n[history: earlier turns are archived under {hist_rel}/INDEX.md — "
        "read_file the index and the relevant files before relying on memory.]")
