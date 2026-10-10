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
    finishgate,
    hold,
    loopend,
    loopnudge,
    loopsetup,
    mediaops,
    notes,
    recall,
    remind,
    requests,
)
from .actions import ALWAYS_KINDS
from .actionschema import brief_value
from .assist_predicates import failure_key
from .boot import boot
from .compaction import turn_record
from .completion import MAX_SCHEMA_ATTEMPTS, next_action
from .control import (
    _ABORT,
    RunAborted,
    announce_finished_subruns,
    drain_injections,
    pause_gate,
)
from .ladder import at_boundary as _ladder_at_boundary
from .loopconst import POLL_S
from .loopend import SCHEMA_STORM_TURNS
from .loopnudge import REPEAT_FAIL
from .observations import format_observation, is_failure
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
#: What the RESERVED finish turn may still execute — the narrowed grammar's own vocabulary
#: (`kindsurface.schema_for_kinds({"finish"})` carries ALWAYS_KINDS with it), so it is that
#: tuple rather than a copy of it. Spending the reserve on a `report` instead of a `finish` is
#: the model's call and costs it the authored summary; anything else is an action on a turn the
#: model was told executes nothing.
RESERVED_TURN_KINDS = frozenset(ALWAYS_KINDS)
#: The refusal of anything else there. An action refused with it ran nothing — not even the
#: side fields every other turn applies — which is how `remind_ledger.rebuild` tells it apart.
RESERVED_REFUSAL = ("the reserved finish turn executes nothing but `finish` (or "
                    + " / ".join(f"`{k}`" for k in ALWAYS_KINDS if k != "finish")
                    + ") — the budget is spent")


__all__ = [
    "MAX_SCHEMA_ATTEMPTS",
    "POLL_S",
    "REPEAT_FAIL",
    "REPEAT_WARN",
    "EngineLoop",
    "RunAborted",
]


class EngineLoop:
    """The turn loop — the heart of a run. Each turn: budgets → pause gate → drain
    injected messages → announce finished subruns → ONE valid JSON action from the model
    (3 attempts: up to 2 schema retries) → dispatch → append the observation; repeat until `finish`.
    Construct with `resume=True` to rehydrate a prior transcript and continue it.
    """

    # Filled in by `loopsetup.configure` — declared here so the class still says what
    # an EngineLoop HOLDS, which is what lifting construction out would otherwise cost.
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
    assist_user_replies: Any
    assists: Any
    assists_fired: set[str]
    base_grants: Any
    consumed_dir: Any
    ctx: Any
    dialog_qids: dict[tuple[str, str], str]
    executed_actions: Any
    final_summary: Any
    grants: Any
    holds: set[tuple[str, str]]
    instruction: Any
    leg_after_authored: Any
    leg_commands: Any
    leg_prose: Any
    messages: list[dict]
    reminder_nudge: Any
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

        The turn cycle is ONE deliberate sequence and this is the only place it is written
        down: each step's body lives in a method named for it, so the ORDER that defines the
        engine reads top to bottom here, and nowhere else.
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
                                       "Progress so far is in the transcript and LEDGER.")
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
                if action["kind"] == "finish":
                    if (outcome := self._finish(action)) is not None:
                        return outcome
                elif self._finish_reserved and action["kind"] not in RESERVED_TURN_KINDS:
                    self._refuse_on_reserved_turn(action)
                else:
                    self._observe(action, streak)
        except RunAborted:
            return self._finish_run("aborted", "Run aborted by the user/daemon.")
        except EndpointError as exc:
            self.ctx.transcript.event("error", {"where": "endpoint", "message": str(exc)})
            return self._finish_run("failed", loopend.endpoint_verdict(self.ctx, exc))
        finally:
            if self.ctx.depth == 0:
                self.ctx.transcript.close()

    # --- the steps of one turn, in the order `run` takes them ----------------------

    def _turn_boundary(self) -> None:
        """Everything that happens BETWEEN two turns: the pause gate, the control.json
        switches, the user's messages, finished children, rule assists and a landed archive.
        Each only appends to the message list — the boundary never rewrites it.
        """
        pause_gate(self, poll_s=POLL_S)
        # The escalation ladder's trigger, after the pause gate and before the inbox drain:
        # a rung that is due runs HERE, so its directive is filed as ordinary freight and
        # drained by the very same `drain_injections` below — an append at a boundary, never
        # a prefix rewrite. Off unless the routine's config enables it (engine/ladder.py).
        _ladder_at_boundary(self)
        apply_model_switch(self)
        apply_deliberation_switch(self)
        apply_rule_additions(self)
        apply_rule_drop(self)
        apply_config_change(self)
        drain_injections(self)
        announce_finished_subruns(self)
        # Rule ASSISTS at the turn boundary: a rule whose moment has arrived says its
        # operative line as an appended ENGINE NOTE. Append-only and turn-free, the
        # same carrier a mid-run rule binding already uses.
        assist.at_boundary(self)
        # …and a background archival that finished since the last turn
        # announces itself here, where the message list is appended to.
        archival.collect(self)
        # …as does a BACKGROUNDED ACTION whose real observation has landed (D118 phase 1):
        # the run marked a read or a fetch `background`, got its turn back at once, and this
        # is where the actual observation arrives — the same start-now/collect-later shape
        # `announce_finished_subruns` above uses for a child run, sized to one action.
        background.collect(self)

    def _land(self, action: dict, usage: dict) -> None:
        """The accepted action becomes the turn: counted, recorded with the phase it was
        taken in, its note filed, appended as the model's message, remembered for the digest.
        """
        ctx = self.ctx
        ctx.turn += 1
        ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage,
                             **({"phase": ctx.phase} if ctx.phase else {}))
        notes.capture(ctx, action)   # the note channel: turn-free, stamped, best-effort
        self.messages.append({"role": "assistant",
                              "content": json.dumps(action, ensure_ascii=False)})
        self.turn_records.append(turn_record(ctx.turn, action))

    def _fail_fast(self, action: dict, retries_before: int, streak: int) -> str | None:
        """The verdict that ends a run the model is no longer steering, or None.

        D87-A: a turn that needed schema-rejection retries extends the storm streak; a clean
        turn resets it. At SCHEMA_STORM_TURNS consecutive retry-burdened turns the run fails
        early — cheaper and clearer than limping to the budget wall at full-prompt retry
        prices. A FINISH is exempt: it ends the run anyway, so failing it saves nothing and
        discards the one thing a run leaves behind, its authored summary — on the reserved
        finish turn, the very loss the reserve exists to prevent. A finish a guard sets aside
        still counts toward the streak, so the next retry-burdened turn trips it.
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
        """A `finish`: the run's status when it stands, None when a guard set it aside and
        the model gets another turn (engine/finishgate.py).
        """
        # The reminder side fields ride a finish exactly as `note` does (captured when the
        # turn landed). The last turn is where they matter most: the engine asks for a
        # `did`/`didnt` label on the turn AFTER the held action ran, and that is very often
        # this one.
        remind_note = remind.apply_ops(self, action, poll_s=POLL_S, replayable=True)
        # …and the `turn:finish` trigger fires here, its own fire point: the finish produces no
        # ordinary observation, so there is no tail for `at_observation` to ride.
        remind_note += remind.at_finish(self, action)
        # A finish that was its reply's FIRST action, with more written after it: when a guard
        # sets the finish aside, the run is told those did not run either (replyactions).
        remind_note += unexecuted_note([unexecuted_row(a) for a in self.unexecuted])
        outcome = finishgate.check_finish(self, action, self.ctx)
        if outcome is None and remind_note and self.messages:
            self.messages[-1]["content"] += remind_note   # rides the guard's own message
        return outcome

    def _refuse_on_reserved_turn(self, action: dict) -> None:
        """Record a non-finish emitted on the reserved finish turn — and do NOT run it.

        "This is your LAST turn — the engine executes nothing else" is a promise, and it was
        only a promise: the reserved turn's grammar is narrowed to `finish`, but a provider
        without constrained decoding can emit any kind and the executor ran it. The budget
        check that opens the next cycle force-finishes — the same ending as before, minus the
        action.
        """
        self.ctx.transcript.event("observation", {
            "kind": action["kind"], "rejected": True, "reason": RESERVED_REFUSAL},
            turn=self.ctx.turn)

    def _observe(self, action: dict, streak: int) -> None:
        """Run ONE action and append what came back, with its tails.

        The pre-execution caution layer (engine/hold.py) is asked first: a consequence
        reminder this routine wrote, or a general rule whose moment this action IS, HOLDS the
        action — it does NOT run — and the model decides again with the caution in front of
        it. After execution would be after the consequence.

        The observation-moment rule assists are asked BEFORE the observation is recorded, so
        the record names what fired (`assist.recorded`): the transcript is what a resumed leg
        rebuilds the once-only guards from (engine/guardscope.py). Their line still rides the
        tail, in its place.
        """
        ctx = self.ctx
        obs = (hold.before_dispatch(self, action)
               or actionroute.dispatch_action(self, action, ctx))
        if self.unexecuted:
            # The reply carried more actions than this one; they did not run, and the run is
            # told so by name in THIS observation — stored on it, so a resumed leg replays the
            # same words (engine/replyactions.py). Their own side fields never ran either.
            obs = {**obs, "not_executed": [unexecuted_row(a) for a in self.unexecuted]}
        held = hold.is_hold(obs)
        if not held:
            self.executed_actions += 1   # a HELD action executed nothing
            if is_failure(obs):
                key = failure_key(action)
                self.failures[key] = self.failures.get(key, 0) + 1
        fired = assist.at_observation(self, action, obs)
        ctx.transcript.event("observation",
                             assist.recorded(self, mediaops.without_bytes(obs), fired),
                             turn=ctx.turn)
        if obs.get("cancelled"):
            # F586 / D160-C's second half: a person reached into this run and stopped ONE call.
            # The observation above already carries the flag, but an observation records what
            # the RUN was told; this records that somebody OUTSIDE the run intervened, which is
            # exactly what an audit cannot reconstruct later — a cancelled call and a call that
            # failed fast are indistinguishable in a record of outcomes alone.
            ctx.transcript.event("action_cancelled",
                                 {"kind": action["kind"], "brief": brief_value(action)[:200],
                                  "exit": obs.get("exit")}, turn=ctx.turn)
        if self.admin_leg:
            # D62: the capability bypass is never silent — one audit line per action.
            from .admin import log_admin_action
            log_admin_action(ctx.server.routines_home, run_id=ctx.run_id,
                             kind=action["kind"], brief=brief_value(action)[:200])
        text = format_observation(obs) + self._tails(action, obs, fired, held=held,
                                                     streak=streak)
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
        # `remind` / `remind_feedback` ride ANY action at no turn cost (like `note`),
        # applied AFTER the interception check so a reminder authored this turn can
        # never hold the very action it rode on.
        text = remind.apply_ops(self, action, poll_s=POLL_S)
        # …and the reminders whose TRIGGER is not an action — `result` (what just came back),
        # `prose` (what this turn said), the `turn:*` moments. They cannot hold (the thing they
        # watch has already happened), so they ride the tail at no turn cost.
        text += remind.at_observation(self, action, obs)
        # …and the observation-moment assists ride the same tail, for the rules whose
        # moment is "what just came back" rather than "what you are about to do" — asked
        # before the observation was recorded (`_observe`), read here in their place.
        text += assist.tail(fired)
        # …and the run's OWN archived history is the third store this layer
        # feeds from: when what just happened overlaps an archived topic, the
        # tail names the file rather than leaving the run to remember it.
        text += recall.at_observation(self, action, obs)
        # D65: an `allow once` grant is spent by THIS successfully-dispatched
        # matching action — revoked here, at the same boundary, and announced so
        # the next matching attempt is not an unexplained denial. A HELD action is
        # not one: it never reached the executor, so it used nothing ("spent by USE,
        # not by attempt"). Spending it there would also break the hold's own
        # contract — re-emitting the same action is the confirmation to proceed, and
        # it would have been denied for a grant the first attempt consumed.
        if not held and (spent := requests.consume_once_grants(self, action, obs)):
            text += requests.spent_notice(spent, action)
        if REPEAT_WARN <= streak < REPEAT_FAIL:
            text += self._repeat_warning(streak)
        if warning := self.ctx.budget_warning():
            text += (f"\n[BUDGET: {warning} — converge DELIBERATELY now: reach a point "
                     "worth handing over, record what matters (LEDGER, state files), "
                     "then finish with an authored summary. Once the budget is spent "
                     "you get exactly ONE turn, and it can only be a finish.]")
        if self._history_active:
            self._hist_note_countdown -= 1
            if self._hist_note_countdown <= 0:
                text += self._history_note
                self._hist_note_countdown = 10
        return text

    def _repeat_warning(self, streak: int) -> str:
        """The repeat-streak tail, and the escape hatch it opens (see loopsetup): the next
        reply runs schema-free, and once shedding has rescued the run twice the provider
        schema stays off for the rest of it.
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
