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
    finishgate,
    inbox,
    loopnudge,
    loopsetup,
    notes,
    requests,
)
from .actionschema import BRIEF_FIELD
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
)

REPEAT_WARN = 3
# D87-A: consecutive TURNS that each needed schema-rejection retries before landing an
# action — a model that reliably cannot hold the action schema. Fail early and clearly
# instead of limping through the budget at full-prompt retry prices (F297/R255:
# c-20260806-150112 burned 12 retries / 477K input tokens before dying late).
SCHEMA_STORM_TURNS = 4

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
    _challenged: set[str]
    _finish_reserved: Any
    _hist_note_countdown: Any
    _hist_rel: Any
    _history_active: Any
    _history_note: Any
    _last_compact_after: Any
    _last_config_ts: Any
    _last_deliberation_ts: Any
    _last_rules_ts: Any
    _last_switch_ts: Any
    _schema_off: Any
    _schema_storm_streak: Any
    _shed_schema_turns: Any
    _sheds: Any
    abort_event: Any
    action_schema: Any
    admin_leg: Any
    allowed_tools: Any
    base_grants: Any
    consumed_dir: Any
    ctx: Any
    dialog_qid: str | None
    executed_actions: Any
    final_summary: Any
    grants: Any
    instruction: Any
    leg_after_authored: Any
    leg_commands: Any
    leg_prose: Any
    messages: list[dict]
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
                if violation := ctx.budget_violation():
                    if self._finish_reserved:
                        return self._finish_run(
                            "partial", f"Run stopped by the engine: {violation}. "
                                       "Progress so far is in the transcript and LEDGER.")
                    loopnudge.reserve_finish(self, violation)
                pause_gate(self, poll_s=POLL_S)
                apply_model_switch(self)
                apply_deliberation_switch(self)
                apply_rule_additions(self)
                apply_config_change(self)
                drain_injections(self)
                announce_finished_subruns(self)
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
                        f"schema-invalid output means the model ({ctx.main_model}) cannot "
                        "hold the action schema — pick a stronger model (D87); repeated "
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
                            f"rejections so far from {ctx.main_model}) — the model cannot "
                            "reliably hold the action schema; failing early instead of "
                            "burning the budget on retries (D87). Pick a stronger model "
                            "for schema-driven work.")
                else:
                    self._schema_storm_streak = 0
                repeat_streak = loopnudge.repeat_streak(self, action)
                if repeat_streak >= REPEAT_FAIL:
                    return self._finish_run(
                        "failed", f"Stuck: the same action was repeated "
                                  f"{repeat_streak} times in a row. Aborting the run.")

                if action["kind"] == "finish":
                    outcome = finishgate.check_finish(self, action, ctx)
                    if outcome is None:
                        continue   # a guard set it aside; the model gets another turn
                    return outcome
                obs = actionroute.dispatch_action(self, action, ctx)
                ctx.transcript.event("observation", obs, turn=ctx.turn)
                self.executed_actions += 1
                if self.admin_leg:
                    # D62: the capability bypass is never silent — one audit line per action.
                    from .admin import log_admin_action
                    brief = str(action.get(BRIEF_FIELD.get(action["kind"], ""), ""))[:200]
                    log_admin_action(ctx.server.routines_home, run_id=ctx.run_id,
                                     kind=action["kind"], brief=brief)
                text = format_observation(obs)
                # D65: an `allow once` grant is spent by THIS successfully-dispatched
                # matching action — revoked here, at the same boundary, and announced so
                # the next matching attempt is not an unexplained denial.
                if spent := requests.consume_once_grants(self, action, obs):
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
            return self._finish_run("failed", f"Endpoint failure: {exc}.{hint}")
        finally:
            if self.ctx.depth == 0:
                self.ctx.transcript.close()

    def _finish_run(self, status: str, summary: str, *, authored: bool = False) -> str:
        ctx = self.ctx
        # R82: repair a summary whose newlines were double-escaped (literal ``\n`` and no real
        # newline) so result.md / the digest render real line breaks instead of verbatim "\n".
        summary = normalize_escaped_newlines(summary)
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
        ctx.transcript.event("finish", {"status": status, "summary": summary, "authored": authored},
                             usage_total=ctx.usage_total(), turns=ctx.turn)
        if status in ("partial", "failed", "aborted") and ctx.depth == 0:
            event_type = "budget_exhausted" if status == "partial" else "run_failed"
            log_health_event(ctx.server.routines_home, event_type,
                             routine=ctx.routine.slug, run_id=ctx.run_id,
                             detail=summary[:500])
        self.final_summary = self.final_summary or summary
        if ctx.depth == 0:
            from ..paths import atomic_write
            atomic_write(ctx.run_dir / "result.md", summary + "\n")
            _autocommit(ctx.routine.dir, f"{ctx.run_id}: {status}")  # routines never run git
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
        brief = str(action.get(BRIEF_FIELD.get(action["kind"], ""), ""))[:80]
        self.turn_records.append({"turn": self.ctx.turn, "kind": action["kind"],
                                  "brief": json.dumps(brief, ensure_ascii=False),
                                  "say": action.get("say", "")})

