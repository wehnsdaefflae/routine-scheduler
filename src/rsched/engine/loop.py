"""The engine turn loop — the workflow-as-harness core.

Turn cycle: budget check → pause gate → inbox drain → sub-workflow exit notifications →
one completion (schema-validated, ≤2 retries — completion.py, which also owns the
compaction gate) → dispatch → observation. Control-flow kinds (spawn/subruns/kill/wait)
are handled here; ask_user in interact.py, library authoring in authoring.py;
effect kinds go through
executor.dispatch. The initial message list (kickoff or resume rehydration) is composed
in boot.py; between-turn concerns (pause, model switch, injections, subrun announcements)
live in control.py; the top-level entry (run_routine) in runtime.py. Sub-workflows run in
parallel threads (subruns.py) and never outlive the parent.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Any

from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import (
    actionroute,
    archival,
    assist,
    finishgate,
    hold,
    inbox,
    loopnudge,
    loopsetup,
    mediaops,
    notes,
    recall,
    remind,
    requests,
)
from .actionschema import brief_value
from .autocommit import autocommit as _autocommit
from .boot import boot
from .completion import MAX_SCHEMA_ATTEMPTS, next_action
from .control import (
    _ABORT,
    RunAborted,
    announce_finished_subruns,
    drain_injections,
    pause_gate,
    request_abort,
)
from .finish_guard import normalize_escaped_newlines
from .loopconst import POLL_S
from .loopnudge import REPEAT_FAIL
from .observations import format_observation
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
#: (`kindsurface.schema_for_kinds({"finish"})` carries ALWAYS_KINDS with it). Spending the
#: reserve on a `report` instead of a `finish` is the model's call and costs it the authored
#: summary; anything else is an action on a turn the model was told executes nothing.
RESERVED_TURN_KINDS = frozenset({"finish", "report", "list_models"})
# D87-A: consecutive TURNS that each needed schema-rejection retries before landing an
# action — a model that reliably cannot hold the action schema. Fail early and clearly
# instead of limping through the budget at full-prompt retry prices (F297/R255:
# c-20260806-150112 burned 12 retries / 477K input tokens before dying late).
SCHEMA_STORM_TURNS = 4


def _serving_model_phrase(ctx: RunContext) -> str:
    """How a failure verdict should NAME the model it is talking about.

    `ctx.main_model` follows every failover switch, so on a run that stepped down the chain
    these verdicts were indicting a model the operator never chose — while the routine's
    config page, the dashboard row and the operator's own memory all said the primary.
    weightloss:20260923-220004 was configured on Opus high, was served two rungs down after a
    503 and a 402, and reported that "the model cannot reliably hold the action schema"
    naming the third rung (F547/D146).

    So when the serving model is NOT the configured one, the sentence says both and how far
    down the chain it got. When nothing failed over, it stays the short form it always was.
    """
    if ctx.failover_rungs and ctx.configured_model and ctx.main_model != ctx.configured_model:
        rungs = "one rung" if ctx.failover_rungs == 1 else f"{ctx.failover_rungs} rungs"
        return (f"the model now serving this run ({ctx.main_model}, {rungs} down the fallback "
                f"chain from the configured {ctx.configured_model})")
    return f"the model ({ctx.main_model})"


def _model_advice(ctx: RunContext) -> str:
    """The "pick a stronger model" clause — dropped when it would be false advice.

    Telling the operator to choose a stronger model is actionable only when the model that
    failed is the model they chose. After a failover it is the opposite of useful: the
    configured model IS the strong one and it was unreachable, so the fix is the transport or
    the chain, never the routine's config (F547/D146).
    """
    if ctx.failover_rungs and ctx.configured_model and ctx.main_model != ctx.configured_model:
        return (f". The configured model was not the one that failed, so a stronger model is "
                f"not the remedy — {ctx.configured_model} was unreachable. Check that "
                f"transport and the fallback chain")
    return " — pick a stronger model"


__all__ = [
    "MAX_SCHEMA_ATTEMPTS",
    "POLL_S",
    "REPEAT_FAIL",
    "REPEAT_WARN",
    "EngineLoop",
    "RunAborted",
    "request_abort",
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
    _evict_warned: Any
    _budget_spent: Any
    _finish_reserved: Any
    _hist_note_countdown: Any
    _hist_rel: Any
    _history_active: Any
    _history_note: Any
    _last_compact_after: Any
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
    dialog_qid: str | None
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
    reminders: Any
    reminders_level: Any
    repeat_hashes: deque[str]
    resume: Any
    subruns: Any
    turn_records: list[dict]
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

    # The complexity ratchet's current worst (pyproject notes it): the turn cycle is ONE
    # deliberate sequence; splitting it would hide the order that defines the engine.
    def run(self) -> str:  # noqa: C901, PLR0912, PLR0915
        ctx = self.ctx
        try:
            boot(self)
            if (ctx.depth == 0 and self.leg_after_authored
                    and self.leg_commands and not self.leg_prose):
                return self._exit_commands_only()
            while True:
                if self._aborted():
                    raise RunAborted
                if spent := ctx.budget_spent():
                    if self._finish_reserved:
                        return self._finish_run(
                            "partial", f"Run stopped by the engine: {spent['message']}. "
                                       "Progress so far is in the transcript and LEDGER.")
                    loopnudge.reserve_finish(self, spent)
                pause_gate(self, poll_s=POLL_S)
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
                retries_before = ctx.schema_retries
                action, usage = next_action(self)
                # Book the spend IMMEDIATELY: tokens burned by failed schema attempts or a
                # turn preempted by abort are real spend even when no action lands.
                ctx.add_usage(usage)
                if self._aborted():
                    raise RunAborted  # a kill during the completion preempts the action
                if action is None:
                    return self._finish_run(
                        "failed",
                        f"No action was ACCEPTED in {MAX_SCHEMA_ATTEMPTS} attempts "
                        f"({ctx.schema_retries} rejections this run, {ctx.turn} completed "
                        f"turns). The last rejection in the transcript names the wall: "
                        f"schema-invalid output means {_serving_model_phrase(ctx)} cannot "
                        f"hold the action schema{_model_advice(ctx)}; repeated "
                        "capability/grant denials mean the run was boxed in by policy, "
                        "and no model change fixes that (R404/F351).")
                ctx.turn += 1
                ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage,
                                     **({"phase": ctx.phase} if ctx.phase else {}))
                notes.capture(ctx, action)   # the note channel: turn-free, stamped, best-effort
                self.messages.append({"role": "assistant",
                                      "content": json.dumps(action, ensure_ascii=False)})
                self._record_turn(action)
                # D87-A: a turn that needed schema-rejection retries extends the storm
                # streak; a clean turn resets it. At SCHEMA_STORM_TURNS consecutive
                # retry-burdened turns the run fails early — cheaper and clearer than
                # limping to the budget wall at full-prompt retry prices.
                if ctx.schema_retries > retries_before:
                    self._schema_storm_streak += 1
                    if self._schema_storm_streak >= SCHEMA_STORM_TURNS:
                        return self._finish_run(
                            "failed",
                            f"Schema storm: every one of the last {SCHEMA_STORM_TURNS} "
                            f"turns needed schema-rejection retries ({ctx.schema_retries} "
                            f"rejections so far from {ctx.main_model}) — "
                            f"{_serving_model_phrase(ctx)} cannot reliably hold the action "
                            f"schema; failing early instead of burning the budget on "
                            f"retries (D87).{_model_advice(ctx)}")
                else:
                    self._schema_storm_streak = 0
                repeat_streak = loopnudge.repeat_streak(self, action)
                if repeat_streak >= REPEAT_FAIL:
                    return self._finish_run(
                        "failed", f"Stuck: the same action was repeated "
                                  f"{repeat_streak} times in a row. Aborting the run.")

                if action["kind"] == "finish":
                    # The reminder side fields ride a finish exactly as `note` does (which is
                    # captured above, before this branch). The last turn is where they matter
                    # most: the engine asks for a `did`/`didnt` label on the turn AFTER the
                    # held action ran, and that is very often this one.
                    remind_note = remind.apply_ops(self, action, poll_s=POLL_S,
                                                   replayable=True)
                    outcome = finishgate.check_finish(self, action, ctx)
                    if outcome is None:
                        if remind_note and self.messages:
                            self.messages[-1]["content"] += remind_note
                        continue   # a guard set it aside; the model gets another turn
                    return outcome
                # The pre-execution caution layer (engine/hold.py): a consequence
                # reminder this routine wrote, or a general rule whose moment this
                # action IS, HOLDS the action — it does NOT run — and the model decides
                # again with the caution in front of it. After execution would be after
                # the consequence.
                if self._finish_reserved and action["kind"] not in RESERVED_TURN_KINDS:
                    # "This is your LAST turn — the engine executes nothing else" is a
                    # promise, and it was only a promise: the reserved turn's grammar is
                    # narrowed to `finish`, but a provider without constrained decoding can
                    # emit any kind and the executor ran it. Record the refusal and go round
                    # — the budget check at the top of the loop force-finishes, which is the
                    # same ending as before, minus the action.
                    ctx.transcript.event("observation", {
                        "kind": action["kind"], "rejected": True,
                        "reason": "the reserved finish turn executes nothing but `finish` "
                                  "(or `report`) — the budget is spent"}, turn=ctx.turn)
                    continue
                obs = (hold.before_dispatch(self, action)
                       or actionroute.dispatch_action(self, action, ctx))
                ctx.transcript.event("observation", mediaops.without_bytes(obs), turn=ctx.turn)
                held = hold.is_hold(obs)
                if not held:
                    self.executed_actions += 1   # a HELD action executed nothing
                if self.admin_leg:
                    # D62: the capability bypass is never silent — one audit line per action.
                    from .admin import log_admin_action
                    brief = brief_value(action)[:200]
                    log_admin_action(ctx.server.routines_home, run_id=ctx.run_id,
                                     kind=action["kind"], brief=brief)
                text = format_observation(obs)
                # `remind` / `remind_feedback` ride ANY action at no turn cost (like `note`),
                # applied AFTER the interception check so a reminder authored this turn can
                # never hold the very action it rode on.
                text += remind.apply_ops(self, action, poll_s=POLL_S)
                # …and the observation-moment assists ride the same tail, for the rules whose
                # moment is "what just came back" rather than "what you are about to do".
                text += assist.at_observation(self, action, obs)
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
                if REPEAT_WARN <= repeat_streak < REPEAT_FAIL:
                    self._shed_schema_turns = 1   # re-arms on every further repeat
                    self._sheds += 1
                    if self._sheds >= 2 and not self._schema_off:
                        self._schema_off = True
                        ctx.transcript.event("error", {
                            "where": "schema", "attempt": 0,
                            "message": "provider response-format disabled for the rest of the "
                                       "run: repeat-streak shedding rescued it twice — the "
                                       "grammar is suppressing fields for this model"})
                    text += (f"\n[ENGINE WARNING: this exact action has now run "
                             f"{repeat_streak} times in a row — {REPEAT_FAIL} identical "
                             "actions fail the run. Change course. The structured-output "
                             "constraint is lifted for your next reply: emit ONE JSON object "
                             "and include every field the action needs (args, content, …).]")
                if warning := ctx.budget_warning():
                    text += (f"\n[BUDGET: {warning} — converge DELIBERATELY now: reach a point "
                             "worth handing over, record what matters (LEDGER, state files), "
                             "then finish with an authored summary. Once the budget is spent "
                             "you get exactly ONE turn, and it can only be a finish.]")
                if self._history_active:
                    self._hist_note_countdown -= 1
                    if self._hist_note_countdown <= 0:
                        text += self._history_note
                        self._hist_note_countdown = 10
                msg: dict = {"role": "user", "content": text}
                if obs.get("media"):  # view_image / auto-attach: the model sees it next turn
                    msg["media"] = obs["media"]
                self.messages.append(msg)
                ctx.write_status()
        except RunAborted:
            return self._finish_run("aborted", "Run aborted by the user/daemon.")
        except EndpointError as exc:
            self.ctx.transcript.event("error", {"where": "endpoint", "message": str(exc)})
            hint = (" Check the endpoint's key file under ~/.credentials/ (see config.yaml)."
                    if exc.auth else "")
            # F566: when the run walked down a chain to get here, the error in hand is the LAST
            # rung's — and the first rung's is the one a reader can act on. Name both, with the
            # rung count, so the line says what started the collapse and what ended it.
            if first := self.ctx.first_failover_cause:
                rungs = self.ctx.failover_rungs
                step = "1 rung" if rungs == 1 else f"{rungs} rungs"
                said = (f"Endpoint failure: the model chain was exhausted after {step}. "
                        f"First: {first}. Last: {exc}.{hint}")
            else:
                said = f"Endpoint failure: {exc}.{hint}"
            return self._finish_run("failed", said)
        finally:
            if self.ctx.depth == 0:
                self.ctx.transcript.close()

    def _finish_run(self, status: str, summary: str, *, authored: bool = False,
                    reply_to: str | None = None) -> str:
        ctx = self.ctx
        # R82: repair a summary whose newlines were double-escaped (literal ``\n`` and no real
        # newline) so result.md / the digest render real line breaks instead of verbatim "\n".
        summary = normalize_escaped_newlines(summary)
        archival.settle(self)   # an archive already in flight gets a moment to land
        killed = self.subruns.kill_all(reason=f"parent run finished ({status})")
        if killed:
            summary += f"\n[{killed} still-running sub-workflow(s) were terminated at run end.]"
        if ctx.depth == 0 and inbox.has_pending_messages(ctx.routine.dir,
                                                         vias=inbox.LIVE_MESSAGE_VIAS):
            # The paths the R108 deferral cannot serve (the spent reserved-finish turn,
            # aborts, engine failures — plus a message racing this very write): the
            # message could not become a turn THIS run, so say so on BOTH sides — this
            # note rides result.md (a conversation's rendered reply) and the next run's
            # digest. The message itself stays queued; the next leg's boot drains it.
            summary += ("\n[A user message arrived as this run ended — it could not be "
                        "delivered this run; it stays queued and opens the next "
                        "run/reply.]")
        finish_payload = {"status": status, "summary": summary, "authored": authored}
        if reply_to:   # F438/D117: the reply targets an earlier message (conversations)
            finish_payload["reply_to"] = reply_to
        ctx.transcript.event("finish", finish_payload,
                             usage_total=ctx.usage_total(), turns=ctx.turn)
        if ctx.depth == 0:
            # A `partial` is budget_exhausted ONLY when a budget violation forced it (the
            # reserved finish turn was spent, or the engine ended it). A partial the model
            # chose on its own — the job needs another run, an ask timed out, a source was
            # down — is run_partial: every routine in the fleet was reading as "out of
            # budget" while 2 of 3 such finishes were authored with budget to spare.
            #
            # The reserved turn is what decides, NOT the status: a run that spends it and
            # still finishes `ok` was ended by a budget just as much, and used to leave no
            # event at all — 11 such runs since 09-01, so the fleet's budget-forced endings
            # read as a smaller number than they are.
            if self._finish_reserved:
                event_type = "budget_exhausted"
            elif status == "partial":
                event_type = "run_partial"
            elif status in ("failed", "aborted"):
                event_type = "run_failed"
            else:
                event_type = ""
            if event_type:
                # `resource` and `limit` ride as FIELDS, not prose: which budget ended the
                # run is exactly the question a sweep must be able to filter on, and
                # `detail` carried only the model's summary.
                spent = self._budget_spent or {}
                log_health_event(ctx.server.routines_home, event_type,
                                 routine=ctx.routine.slug, run_id=ctx.run_id,
                                 detail=summary[:500],
                                 status=status if event_type == "budget_exhausted" else None,
                                 resource=spent.get("resource"), limit=spent.get("limit"))
        self.final_summary = self.final_summary or summary
        if ctx.depth == 0:
            from ..paths import atomic_write
            atomic_write(ctx.run_dir / "result.md", summary + "\n")
            _autocommit(ctx.routine.dir, f"{ctx.run_id}: {status}",   # routines never run git
                        routines_home=ctx.server.routines_home, run_id=ctx.run_id)
            state = {"ok": "finished", "partial": "finished", "failed": "failed",
                     "aborted": "aborted"}.get(status, "finished")
            ctx.outcome = status   # `state` folds partial into finished — this keeps it visible
            ctx.write_status(state, question=None)
        return status

    def _exit_commands_only(self) -> str:
        """A conversation woken ONLY to run slash commands: the commands already executed in
        boot, appending their events to the transcript. End the leg with NO model turn and NO
        authored reply (no finish event, result.md untouched) so the conversation returns to
        idle and the user keeps the speaking turn. The next PROSE message resumes normally and
        the model sees the command results replayed from the transcript.
        """
        self.ctx.write_status("finished", question=None)
        return "finished"

    def _record_turn(self, action: dict) -> None:
        brief = brief_value(action)[:80]
        self.turn_records.append({"turn": self.ctx.turn, "kind": action["kind"],
                                  "brief": json.dumps(brief, ensure_ascii=False),
                                  "say": action.get("say", "")})

