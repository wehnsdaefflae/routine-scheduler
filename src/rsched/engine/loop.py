"""The engine turn loop — the workflow-as-harness core.

Turn cycle, spelled out in order in `EngineLoop.run`: abort → budget check (the reserved
finish turn) → the turn boundary (pause gate, control.json switches, inbox drain, finished
children, boundary assists, a landed archive) → one completion (schema-validated, ≤2 retries —
completion.py, which also owns the compaction gate) → the turn lands → the fail-fast verdicts →
the finish gate, or dispatch → observation. Which handler owns which kind is actionroute.py
(ask_user in interact.py, library authoring in authoring.py, the effect kinds in
executor.dispatch). Construction is loopsetup.py; the initial message list (kickoff or resume
rehydration) boot.py; the between-turn feeds control.py and switches.py; the run's nudges
loopnudge.py; how a run ends — the verdicts and the one close-out — loopend.py; the top-level
entry (run_routine) runtime.py. Sub-workflows run in parallel threads (subruns.py) and never
outlive the parent.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Any

from ..assists import Assist
from ..endpoints.base import EndpointError
from . import (
    actionroute,
    archival,
    assist,
    background,
    chain,
    donothing,
    finishstands,
    hold,
    loopend,
    loopnudge,
    loopsetup,
    loopshape,
    mediaops,
    obstails,
    recall,
    remind,
    requests,
    sidefields,
    turnwindow,
)
from .actionschema import brief_value
from .boot import boot
from .compaction import turn_record
from .completion import MAX_SCHEMA_ATTEMPTS, next_action
from .control import (
    _ABORT,
    RunAborted,
    announce_finished_subruns,
    announce_started_subruns,
    drain_injections,
    pause_gate,
)
from .exitgate import failure_tally_key
from .ladder import at_boundary as _ladder_at_boundary
from .loopconst import POLL_S
from .loopend import SCHEMA_STORM_TURNS
from .loopnudge import REPEAT_FAIL
from .observations import live_observation
from .replyactions import unexecuted_note, unexecuted_row
from .run_context import RunContext
from .switches import (
    apply_config_change,
    apply_deliberation_switch,
    apply_model_switch,
    apply_rule_additions,
    apply_rule_drop,
)

REPEAT_WARN = 3
# MAX_SCHEMA_ATTEMPTS and RunAborted are NOT re-exported: completion.py and control.py define
# them and every importer in the tree takes them from there (D243 needed the lines).
__all__ = ["POLL_S", "REPEAT_FAIL", "REPEAT_WARN", "EngineLoop"]


class EngineLoop:
    """The turn loop — the heart of a run. Each turn: budgets → pause gate → drain
    injected messages → announce finished subruns → ONE valid JSON action from the model
    (3 attempts: up to 2 schema retries) → dispatch → append the observation; repeat until `finish`.
    Construct with `resume=True` to rehydrate a prior transcript and continue it.
    """

    # Filled in by `loopsetup.configure` — declared so the class says what an EngineLoop HOLDS.
    _archival: Any
    _challenged: set[str]
    _evict_owed: Any
    _evict_warned: Any
    _budget_spent: Any
    _finish_reserved: Any
    _hist_note_countdown: Any
    _hist_rel: Any
    _history_active: Any
    _history_note: Any
    _last_compact_after: Any
    _stage_mark: Any
    _recall_after: Any
    _recalled: set[str]
    _last_config_ts: Any
    _last_deliberation_ts: Any
    _last_rules_ts: Any
    _last_rule_drop_ts: Any
    _last_switch_ts: Any
    _schema_off: Any
    _schema_storm_streak: Any
    _shed_schema_turns: Any
    _sheds: Any
    _token_ratio: Any
    abort_event: Any
    action_schema: Any
    admin_leg: Any
    allowed_tools: Any
    assist_finish_deferred: Any
    assist_undo_points: Any
    assist_user_replies: Any
    assists: Any
    assists_fired: set[str]
    base_grants: Any
    consumed_dir: Any
    ctx: Any
    dialog_qids: dict[tuple[str, str], str]
    executed_actions: Any
    executed_kinds: set[str]
    final_summary: Any
    grants: Any
    holds: set[tuple[str, str]]
    instruction: Any
    leg_after_authored: Any
    leg_commands: Any
    leg_prose: Any
    messages: list[dict]
    reminder_nudge: Any
    reminder_owed: Any
    reminder_pending: Any
    reminder_replayed: set[str]
    reminder_asked_back: bool
    reminders: Any
    reminders_level: Any
    repeat_hashes: deque[str]
    resume: Any
    subruns: Any
    turn_records: list[dict]
    unexecuted: list[dict]
    chain_queue: list[dict]
    chain_run: int
    chain_seen: set[str]
    chain_opening: set[str] | None
    failures: dict[str, int]
    util_reminder: Any
    workflow_body: Any

    def __init__(self, ctx: RunContext, workflow_body: str, instruction: str,
                 abort_event: threading.Event | None = None,
                 allowed_tools: list[str] | None = None, resume: bool = False):
        loopsetup.configure(self, ctx, workflow_body, instruction, abort_event,
                            allowed_tools, resume)

    def _aborted(self) -> bool:
        return _ABORT["flag"] or self.abort_event.is_set()

    # --- lifecycle ---------------------------------------------------------------

    def run(self) -> str:
        """Drive the run to its end and return its status.

        The turn cycle's ORDER is written down here and nowhere else; each step's body lives in
        a method named for it.
        """
        ctx = self.ctx
        try:
            boot(self)
            if (ctx.depth == 0 and self.leg_after_authored
                    and self.leg_commands and not self.leg_prose):
                return loopend.exit_commands_only(self)
            while True:
                if self._aborted():
                    raise RunAborted
                if spent := ctx.budget_spent():
                    if self._finish_reserved:
                        return self._finish_run(
                            "partial", f"Run stopped by the engine: {spent['message']}. "
                                       "Progress so far is in the transcript.")
                    loopnudge.reserve_finish(self, spent)
                self._turn_boundary()
                retries_before = ctx.schema_retries
                action, usage = next_action(self)
                # Book the spend IMMEDIATELY: tokens burned by failed schema attempts or a
                # turn preempted by abort are real spend even when no action lands.
                ctx.add_usage(usage)
                if self._aborted():
                    raise RunAborted  # a kill during the completion preempts the action
                if action is None:
                    return self._finish_run(
                        "failed", loopend.no_action_verdict(ctx, MAX_SCHEMA_ATTEMPTS))
                self._land(action, usage)
                streak = loopnudge.repeat_streak(self, action)
                if verdict := self._fail_fast(action, retries_before, streak):
                    return self._finish_run("failed", verdict)
                # Every action this ONE reply wrote, in order, inside this turn (engine/chain.py).
                if (outcome := chain.take_turn(self, action, streak)) is not None:
                    return outcome
        except RunAborted:
            return loopend.aborted(self)   # names the ACTOR that stopped it (F751)
        except EndpointError as exc:
            self.ctx.transcript.event("error", {"where": "endpoint", "message": str(exc)})
            return self._finish_run("failed", loopend.endpoint_verdict(self.ctx, exc))
        finally:
            if self.ctx.depth == 0:
                self.ctx.transcript.close()

    # --- the steps of one turn, in the order `run` takes them ----------------------

    def _turn_boundary(self) -> None:
        """Everything that happens BETWEEN two turns: the pause gate, the control.json
        switches, the user's messages, started and finished children, rule assists and a
        landed archive. Each only appends to the message list — it never rewrites it.
        """
        pause_gate(self, poll_s=POLL_S)
        # The escalation ladder (opt-in, engine/ladder.py) runs before the inbox drain, so a
        # due rung's directive is drained below as ordinary freight.
        _ladder_at_boundary(self)
        apply_model_switch(self)
        apply_deliberation_switch(self)
        apply_rule_additions(self)
        apply_rule_drop(self)
        apply_config_change(self)
        drain_injections(self)
        announce_finished_subruns(self)
        announce_started_subruns(self)   # the `spawn` point: once per child, its row in hand
        assist.at_boundary(self)     # rule assists whose moment arrived, as ENGINE NOTEs
        archival.collect(self)       # a background archival landed since the last turn
        background.collect(self)     # backgrounded actions whose observation has landed

    def _land(self, action: dict, usage: dict | None = None, *, chained: bool = False) -> None:
        """The accepted action becomes the turn: counted, recorded with the phase it was
        taken in, its note filed, appended as the model's message, remembered for the digest.

        A CHAINED element (engine/chain.py) lands inside the SAME turn: the counter does not
        advance, the event names its element, and the reply's spend is booked on its first action.
        """
        ctx = self.ctx
        if not chained:
            ctx.turn += 1
        ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage,
                             **({"chained": self.chain_run} if chained else {}),
                             **({"phase": ctx.phase} if ctx.phase else {}))
        sidefields.land(self, action)   # note filed, role swap applied: turn-free, best-effort
        self.messages.append({"role": "assistant",
                              "content": json.dumps(action, ensure_ascii=False)})
        self.turn_records.append(turn_record(ctx.turn, action))

    def _fail_fast(self, action: dict, retries_before: int, streak: int) -> str | None:
        """The verdict that ends a run the model is no longer steering, or None.

        D87-A: SCHEMA_STORM_TURNS consecutive retry-burdened turns fail the run early, cheaper
        than limping to the budget wall. A FINISH is exempt (failing it would discard the
        authored summary), but still counts toward the streak.
        """
        ctx = self.ctx
        if ctx.schema_retries > retries_before:
            self._schema_storm_streak += 1
            if (self._schema_storm_streak >= SCHEMA_STORM_TURNS
                    and action["kind"] != "finish"):
                return loopend.storm_verdict(ctx)
        else:
            self._schema_storm_streak = 0
        if streak >= REPEAT_FAIL:
            return (f"Stuck: the same action was repeated {streak} times in a row. "
                    "Aborting the run.")
        return None

    def _finish(self, action: dict) -> str | None:
        """The status when the finish stands, None when a guard set it aside for a turn — via
        engine/finishstands.py, never finishgate: a RAISING guard must not cost the summary.
        """
        # The reminder side fields ride a finish as `note` does; a `did`/`didnt` label is often
        # owed on exactly this turn.
        remind_note = remind.apply_ops(self, action, poll_s=POLL_S, replayable=True)
        # `turn:finish` fires here: a finish has no ordinary observation tail to ride.
        remind_note += remind.at_finish(self, action)
        # Actions written after the finish did not run either, if a guard sets it aside.
        remind_note += unexecuted_note(
            [unexecuted_row(a) for a in self.unexecuted],
            "a `finish` ends the run, so nothing written after it could run")
        outcome = finishstands.check_finish_or_stand(self, action, self.ctx)
        if outcome is None and remind_note and self.messages:
            self.messages[-1]["content"] += remind_note   # rides the guard's own message
        return outcome

    def _observe(self, action: dict, streak: int) -> None:
        """Run ONE action and append what came back, with its tails.

        The hold seam (engine/hold.py) is asked first: a matching reminder or rule HOLDS the
        action before it runs, since after execution is after the consequence. Observation
        assists are asked BEFORE the observation is recorded, so the record names what fired —
        a resumed leg rebuilds its once-only guards from it (engine/guardscope.py).
        """
        ctx = self.ctx
        obs = hold.before_dispatch(self, action) or actionroute.dispatch_action(self, action, ctx)
        # Whether the rest of this reply still runs, and the actions that did NOT — stored on
        # this observation so a resumed leg replays the same words (engine/replyactions.py).
        obs = {**obs, **chain.stop_fields(self, obs)}
        held = hold.is_hold(obs)
        if donothing.grounds_a_finish(obs):
            donothing.record_grounding(self, action, obs)   # the count AND its kinds (D243)
            # F711: a DECLARED informational exit never counts (engine/exitgate.py)
            if key := failure_tally_key(ctx, action, obs):
                self.failures[key] = self.failures.get(key, 0) + 1
        fired = assist.at_observation(self, action, obs)
        tails = self._tails(action, obs, fired, held=held, streak=streak)
        ctx.transcript.event("observation", obstails.record(assist.recorded(
            self, mediaops.without_bytes(obs), fired), tails), turn=ctx.turn)
        if obs.get("cancelled"):
            # A person stopped ONE call (F586). Recorded as its own event: in outcomes alone a
            # cancelled call is indistinguishable from one that failed fast.
            ctx.transcript.event("action_cancelled",
                                 {"kind": action["kind"], "brief": brief_value(action)[:200],
                                  "exit": obs.get("exit")}, turn=ctx.turn)
        if self.admin_leg:
            # D62: the capability bypass is never silent — one audit line per action.
            from .admin import log_admin_action
            log_admin_action(ctx.server.routines_home, run_id=ctx.run_id,
                             kind=action["kind"], brief=brief_value(action)[:200])
        text = live_observation(obs, ctx) + tails
        msg: dict = {"role": "user", "content": text}
        if obs.get("media"):  # view_image / auto-attach: the model sees it next turn
            msg["media"] = obs["media"]
        self.messages.append(msg)
        ctx.write_status()

    def _tails(self, action: dict, obs: dict, fired: list[Assist], *, held: bool,
               streak: int) -> str:
        """Everything that rides an observation, in the order the model reads it
        (docs/prompt-anatomy.md §3b). Each tail is free and appears only when it applies.
        """
        # `remind` side fields, applied AFTER the hold check so a reminder authored this turn
        # can never hold the action it rode on.
        text = remind.apply_ops(self, action, poll_s=POLL_S)
        # Reminders on non-action triggers (`result`, `prose`, `turn:*`) cannot hold; they ride.
        text += remind.at_observation(self, action, obs)
        text += assist.tail(fired)   # observation-moment assists, asked in `_observe`
        text += recall.at_observation(self, action, obs)   # an overlapping archived topic
        # D65: an `allow once` grant is spent by USE, not attempt — a HELD action never reached
        # the executor, and re-emitting it is the confirmation to proceed.
        if not held and (spent := requests.consume_once_grants(self, action, obs)):
            text += requests.spent_notice(spent, action)
        if REPEAT_WARN <= streak < REPEAT_FAIL:
            text += self._repeat_warning(streak)
        text += loopshape.after(self, action, obs)   # a cycle, varied failures, a same result
        if warning := self.ctx.budget_warning():
            text += turnwindow.converge(warning)
        # D227: what is LEFT, as a fact — here, since the contract is composed once.
        text += turnwindow.budget_tail(self.ctx)
        if self._history_active:
            self._hist_note_countdown -= 1
            if self._hist_note_countdown <= 0:
                text += self._history_note
                self._hist_note_countdown = 10
        return text

    def _repeat_warning(self, streak: int) -> str:
        """The repeat-streak tail, and its escape hatch: the next reply runs schema-free; after
        two sheds the provider schema stays off for the rest of the run.
        """
        self._shed_schema_turns = 1   # re-arms on every further repeat
        self._sheds += 1
        if self._sheds >= 2 and not self._schema_off:
            self._schema_off = True
            self.ctx.transcript.event("error", {
                "where": "schema", "attempt": 0,
                "message": "provider response-format disabled for the rest of the "
                           "run: repeat-streak shedding rescued it twice — the "
                           "grammar is suppressing fields for this model"})
        return (f"\n[ENGINE WARNING: this exact action has now run "
                f"{streak} times in a row — {REPEAT_FAIL} identical "
                "actions fail the run. Change course. The structured-output "
                "constraint is lifted for your next reply: emit ONE JSON object "
                "and include every field the action needs (args, content, …).]")

    # --- ending ------------------------------------------------------------------

    def _finish_run(self, status: str, summary: str, *, authored: bool = False,
                    reply_to: str | None = None, final: bool | None = None) -> str:
        """End the run — the one close-out every ending goes through (engine/loopend.py)."""
        return loopend.finish_run(self, status, summary, authored=authored, reply_to=reply_to,
                                  final=final)
