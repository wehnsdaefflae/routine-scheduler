"""Constructing an EngineLoop — the decisions made once, before the first turn."""

from __future__ import annotations

import threading
from collections import deque

from ..policyload import load_policy
from . import (
    detach,
    loopnudge,
)
from .loopnudge import REPEAT_FAIL
from .run_context import RunContext
from .subruns import SubrunManager


def configure(loop, ctx: RunContext, workflow_body: str, instruction: str,
                 abort_event: threading.Event | None = None,
                 allowed_tools: list[str] | None = None, resume: bool = False) -> None:
    """Everything an EngineLoop decides ONCE, before its first turn.

    Split out of `EngineLoop.__init__` (F393). Which kinds this run may emit is the
    load-bearing part: the workflow's `tools:` allowlist, intersected with the routine's
    capabilities, plus the structural rules (a child holds nothing gated; creation kinds
    reach every top-level run but not a within-reply child). The action SCHEMA is
    projected from that set, so a kind this run may not use is not merely refused — it is
    unrepresentable in the grammar the model generates against.
    """
    loop.ctx = ctx
    loop.workflow_body = workflow_body
    loop.instruction = instruction
    loop.resume = resume     # rehydrate the prior transcript instead of a clean start
    # A workflow may restrict which action kinds it may use (frontmatter `tools:`); `finish`
    # is always permitted so a run can end. None = every tool allowed. Enforced per turn by
    # validate_action, so the model is corrected within the schema-retry cycle.
    loop.allowed_tools = set(allowed_tools) | {"finish"} if allowed_tools else None
    loop.abort_event = abort_event or threading.Event()
    loop.subruns = SubrunManager(loop)
    loop.messages = []
    loop.turn_records = []
    loop.repeat_hashes = deque(maxlen=REPEAT_FAIL)
    loop.consumed_dir = ctx.root_run_dir / "consumed"
    loop.final_summary = ""
    loop.dialog_qid = None   # open ask_user record a dialog reply left behind
    loop.executed_actions = 0  # actions that produced an observation this run
    loop._schema_storm_streak = 0   # consecutive retry-burdened turns (D87, SCHEMA_STORM_TURNS)
    # This leg's wake, set in boot. The speaker turn is the USER's after the model hands
    # it back with an authored finish (`leg_after_authored`); a message that resumes then
    # keeps the turn with the user if it only EXECUTES commands (`leg_commands`, no
    # `leg_prose`) — the model takes no turn. Prose hands the turn over. A run with its own
    # work to do — a scheduled routine fire, or crash recovery mid-workflow — has no
    # authored hand-back, so it always proceeds (commands there are injected context).
    loop.leg_commands = False
    loop.leg_prose = False
    loop.leg_after_authored = False
    # Gated capabilities (write_util, reserved utils, runs/ access) come from the
    # routine's CAPABILITIES mapping — user-set config a routine cannot loop-grant
    # (its own routine.yaml is write-protected like the recipe); the library docs'
    # requires: contribute only the reserved-util vocabulary and denial wording.
    # Own-recipe writes are a CAPABILITY (`write_recipe`, held through the recipe-authoring
    # conduct doc) — not, as before 0.261.0, a side effect of a user-granted fs_write_root
    # covering the routine's own dir. That coupling meant giving a routine write access to its
    # working directory silently also gave it the right to rewrite its own instructions, which
    # is a different decision and deserves its own switch. routine.yaml stays sealed either way.
    unlocked = "write_recipe" in ((ctx.routine.capabilities or {}).get("actions") or [])
    # A "revise recipe" run (the user asked from the run view to change this routine's OWN
    # files) is granted recipe loop-write + the file-edit kinds for THIS leg only — a marker
    # the /revise endpoint drops in the run dir, read once and cleared here. No persisted
    # fs_write_root, so the recipe stays sealed to every ordinary run (see engine/revise.py).
    from .revise import REVISE_KINDS, clear_revise_marker, revise_marker
    revising = revise_marker(ctx.run_dir) is not None
    if revising:
        clear_revise_marker(ctx.run_dir)
        if loop.allowed_tools is not None:
            loop.allowed_tools |= set(REVISE_KINDS)
    # D62: an ADMIN conversation leg — the web layer validated RSCHED_ADMIN_TOKEN and dropped
    # a one-shot marker, read once and cleared here. admin lifts CAPABILITY gating for this
    # leg only (gated kinds, reserved utils, previous-run read depth); structural/ownership
    # gates still apply. Root conversations only — a scheduled routine never gets an operator
    # at the keyboard, and a subrun builds its own capabilities-off policy (engine/admin.py).
    from .admin import admin_marker, clear_admin_marker
    loop.admin_leg = admin_marker(ctx.run_dir) and detach._is_root_conversation(ctx)
    clear_admin_marker(ctx.run_dir)
    # D58: routine and lane creation is INITIATED from a conversation — that is where a
    # user is in the loop to design with. F328 keeps the restriction and drops its
    # consequence: a run without a user may still PROPOSE, so the kinds are surfaced
    # everywhere and it is the HANDLER that decides between materializing (root
    # conversation) and queuing a proposal for the Decisions page (anywhere else). Before
    # this, a scheduled run holding a fully designed, user-approved routine had no way to
    # hand it over at all and it was carried back to the operator by hand (R353). A None
    # allowed set means "unrestricted" and already carries every kind. Depth 0 ONLY: a
    # within-reply CHILD must not create or propose routines as a side effect — its parent
    # is the one reasoning with the user, and a child's proposal traces to nothing.
    if loop.allowed_tools is not None and ctx.depth == 0:
        loop.allowed_tools |= {"create_routine", "manage_lane"}
    # base_grants is the CONFIG-derived policy; the live loop.grants folds the run's
    # one-time grant overlay over it (requests.rebuild_policy) — always base+overlay,
    # never stacked, so a decision can also be reasoned about from the base.
    loop.base_grants = load_policy(ctx.server.permissions_home,
                                   ctx.routine.permissions,
                                   ctx.routine.capabilities,
                                   current_run_ts=ctx.run_ts,
                                   recipe_unlocked=unlocked or revising,
                                   admin=loop.admin_leg,
                                   grants_map=ctx.routine.grants)
    if ctx.depth > 0:
        # A spawned/subtask child: capabilities are off by design (childrun), so a
        # gated-kind denial must name the child scope, not claim the routine lacks it.
        # run_history drops back to "none" here: D96's always-on 'last' floor is a
        # ROUTINE baseline, and a child's brief — not the archive — is its context.
        from dataclasses import replace
        loop.base_grants = replace(loop.base_grants, is_subrun=True,
                                   run_history="none")
    loop.grants = ctx.grants = loop.base_grants.with_overlay(ctx.granted_now,
                                                             ctx.denied_now)
    loop.util_reminder = loopnudge.build_util_reminder(loop)
    loop._last_switch_ts = ""   # edge-trigger for mid-run model switches (control.json)
    loop._last_deliberation_ts = ""   # edge-trigger for mid-run deliberation switches
    loop._last_rules_ts = ""    # edge-trigger for user-bound general rules
    loop._last_rule_drop_ts = ""   # …and for unbinding one mid-run (symmetric)
    loop._last_config_ts = ""   # edge-trigger for a live config PATCH (F337)
    loop._challenged = set()   # F334 v2: conditions the verifier has
    #                                      already objected to — at most once each,
    #                                      or a stubborn pair livelocks the run
    # A signal already applied by an earlier leg must not re-fire on this one —
    # the run's applied ledger (engine-owned) seeds the edge-triggers.
    from .switches import load_applied_baselines
    load_applied_baselines(loop)
    ctx.deliberation = ctx.routine.deliberation   # live level; control.json may re-set it
    # Repeat-streak escape hatch: identical-but-valid actions in a row are the second
    # signature of provider grammar distortion (a model narrating "I keep forgetting args"
    # while the grammar suppresses the field). At REPEAT_WARN the next completion runs
    # schema-free; the contract in the system prompt still demands one JSON object.
    # Once shedding has rescued the run twice, the diagnosis is settled for this model —
    # the provider schema stays OFF for the rest of the run instead of re-triggering the
    # suppression cycle on every fresh util call (~3 wasted turns each).
    loop._shed_schema_turns = 0
    loop._sheds = 0
    loop._schema_off = False
    # The schema the TRANSPORT gets, projected onto the kinds this run may emit
    # (the same projection the composed prompt shows). Narrowing the grammar makes a
    # disallowed kind ungeneratable instead of generated-then-rejected. allowed_tools
    # is fixed at boot; a mid-run GRANT decision (requests.apply_decision) re-projects
    # this, so an allowed-now kind becomes generatable on the very next turn.
    from .kindsurface import effective_kinds, schema_for_kinds
    loop.action_schema = schema_for_kinds(effective_kinds(loop.allowed_tools, ctx.grants),
                                          reminders=bool(ctx.grants and ctx.grants.reminders_on))
    # The relevance-trigger layer: one hold seam (engine/hold.py), two trigger sources —
    # the reminders this routine learned and the assists its rules declare — and the run's
    # own archived history as a third store to surface from. Each initialises its OWN run
    # state: this function has no business knowing their field names, and the statement
    # ceiling said so before the fourth one landed.
    from . import archival, assist, hold, recall, remind
    for layer in (hold, remind, assist, recall, archival):
        layer.configure(loop)
    # Once the conversation has been archived to on-disk history, the model is reminded
    # to consult its index — right after each compaction, then every 10th turn (NOT every
    # turn: an identical tail on every observation is pure rent on the context).
    loop._history_active = False
    loop._hist_note_countdown = 0
    # The RESERVED FINISH turn: a budget violation no longer ends the run behind the
    # model's back. The first violation spends this reserve — one last turn, schema
    # narrowed to finish — so the summary is ALWAYS authored. Only a second violation
    # (the reserve already spent) force-finishes. See _reserve_finish.
    loop._finish_reserved = False
    loop._last_compact_after = 0   # post-compaction size; gates re-compaction (anti-thrash)
    loop._evict_warned = False   # the one-turn warning before the middle is elided
    loop._last_seen_phase = None   # the anticipatory-compaction edge (window.py)
    try:
        hist_rel = str((ctx.run_dir / "history").relative_to(ctx.routine.dir))
    except ValueError:
        hist_rel = "history"
    loop._hist_rel = hist_rel
    loop._history_note = (
        f"\n[history: earlier turns are archived under {hist_rel}/INDEX.md — "
        "read_file the index and the relevant files before relying on memory.]")
