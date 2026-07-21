"""The engine turn loop — the workflow-as-harness core.

Turn cycle: budget check → pause gate → inbox drain → sub-workflow exit notifications →
one completion (schema-validated, ≤2 retries — completion.py, which also owns the
compaction gate) → dispatch → observation. Control-flow kinds (spawn/subruns/kill/wait)
are handled here; ask_user and write_util in interact.py; effect kinds go through
executor.dispatch. The initial message list (kickoff or resume rehydration) is composed
in boot.py; between-turn concerns (pause, model switch, injections, subrun announcements)
live in control.py; the top-level entry (run_routine) in runtime.py. Sub-workflows run in
parallel threads (subruns.py) and never outlive the parent.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque

from ..endpoints.base import EndpointError
from ..grants import load_policy
from ..health_events import log_health_event
from . import detach, executor, interact, notes
from .actions import BRIEF_FIELD
from .autocommit import autocommit as _autocommit
from .boot import boot
from .completion import MAX_SCHEMA_ATTEMPTS, next_action
from .control import (
    _ABORT,
    RunAborted,
    announce_finished_subruns,
    apply_deliberation_switch,
    apply_model_switch,
    apply_trait_additions,
    drain_injections,
    pause_gate,
    request_abort,
)
from .finish_guard import unbacked_action_claims
from .observations import format_observation
from .run_context import RunContext
from .subruns import SubrunManager

POLL_S = 2.0
REPEAT_WARN = 3
REPEAT_FAIL = 5

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
    (up to 3 schema retries) → dispatch → append the observation; repeat until `finish`.
    Construct with `resume=True` to rehydrate a prior transcript and continue it.
    """

    def __init__(self, ctx: RunContext, workflow_body: str, instruction: str,
                 abort_event: threading.Event | None = None,
                 allowed_tools: list[str] | None = None, resume: bool = False):
        self.ctx = ctx
        self.workflow_body = workflow_body
        self.instruction = instruction
        self.resume = resume     # rehydrate the prior transcript instead of a clean start
        # A workflow may restrict which action kinds it may use (frontmatter `tools:`); `finish`
        # is always permitted so a run can end. None = every tool allowed. Enforced per turn by
        # validate_action, so the model is corrected within the schema-retry cycle.
        self.allowed_tools = set(allowed_tools) | {"finish"} if allowed_tools else None
        self.abort_event = abort_event or threading.Event()
        self.subruns = SubrunManager(self)
        self.messages: list[dict] = []
        self.turn_records: list[dict] = []
        self.repeat_hashes: deque[str] = deque(maxlen=REPEAT_FAIL)
        self.consumed_dir = ctx.root_run_dir / "consumed"
        self.final_summary = ""
        self.dialog_qid: str | None = None   # open ask_user record a dialog reply left behind
        self.executed_actions = 0  # actions that produced an observation this run
        # This leg's wake, set in boot. The speaker turn is the USER's after the model hands
        # it back with an authored finish (`leg_after_authored`); a message that resumes then
        # keeps the turn with the user if it only EXECUTES commands (`leg_commands`, no
        # `leg_prose`) — the model takes no turn. Prose hands the turn over. A run with its own
        # work to do — a scheduled routine fire, or crash recovery mid-workflow — has no
        # authored hand-back, so it always proceeds (commands there are injected context).
        self.leg_commands = False
        self.leg_prose = False
        self.leg_after_authored = False
        # Gated capabilities (write_util, reserved utils, runs/ access) come from the
        # routine's CAPABILITIES mapping — user-set config a routine cannot self-grant
        # (its own routine.yaml is write-protected like the recipe); the library docs'
        # requires: contribute only the reserved-util vocabulary and denial wording.
        # Own recipe/config writes unlock ONLY when a user-granted fs_write_root covers
        # the routine dir — the routine-improver's case. Enforced per turn by
        # validate_action.
        from ..paths import within
        unlocked = any(within(root, ctx.routine.dir)
                       for root in ctx.routine.fs_write_roots or [])
        self.grants = ctx.grants = load_policy(ctx.server.permissions_home,
                                               ctx.routine.permissions,
                                               ctx.routine.capabilities,
                                               current_run_ts=ctx.run_ts,
                                               recipe_unlocked=unlocked)
        self.util_reminder = self._build_util_reminder()
        self._last_switch_ts = ""   # edge-trigger for mid-run model switches (control.json)
        self._last_deliberation_ts = ""   # edge-trigger for mid-run deliberation switches
        self._last_traits_ts = ""   # edge-trigger for user-added practice modules
        ctx.deliberation = ctx.routine.deliberation   # live level; control.json may re-set it
        # Repeat-streak escape hatch: identical-but-valid actions in a row are the second
        # signature of provider grammar distortion (a model narrating "I keep forgetting args"
        # while the grammar suppresses the field). At REPEAT_WARN the next completion runs
        # schema-free; the contract in the system prompt still demands one JSON object.
        # Once shedding has rescued the run twice, the diagnosis is settled for this model —
        # the provider schema stays OFF for the rest of the run instead of re-triggering the
        # suppression cycle on every fresh util call (~3 wasted turns each).
        self._shed_schema_turns = 0
        self._sheds = 0
        self._schema_off = False
        # Once the conversation has been archived to on-disk history, the model is reminded
        # to consult its index — right after each compaction, then every 10th turn (NOT every
        # turn: an identical tail on every observation is pure rent on the context).
        self._history_active = False
        self._hist_note_countdown = 0
        self._last_compact_after = 0   # post-compaction size; gates re-compaction (anti-thrash)
        try:
            hist_rel = str((ctx.run_dir / "history").relative_to(ctx.routine.dir))
        except ValueError:
            hist_rel = "history"
        self._hist_rel = hist_rel
        self._history_note = (
            f"\n[history: earlier turns are archived under {hist_rel}/INDEX.md — "
            "read_file the index and the relevant files before relying on memory.]")

    def _build_util_reminder(self) -> str:
        # One-shot nudge appended to the FIRST user message only (kickoff / resume note) —
        # the catalog already sits in CAPABILITIES and a failed util call carries its own
        # repair hint, so repeating this on every observation was rent without information
        # (~60 tokens × every turn, re-read for the rest of the run).
        if self.allowed_tools is not None and "util" not in self.allowed_tools:
            return ""
        if self.grants.allows_kind("write_util"):
            create = ("write_util to create/revise one"
                      + (" (needs the user's approval first)"
                         if self.grants.needs_confirm(creating=True) else ""))
        else:
            create = ("note the gap with a deferred ask_user — the write_util capability "
                      "is switched off for this routine")
        return ("\n[tools: the CAPABILITIES catalog lists the global utils; run `util "
                f"name=list args=[\"<name>\"]` for one util's exact usage; "
                f"if none fits, {create}.]")

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
                    return self._finish_run(
                        "partial", f"Run stopped by the engine: {violation}. "
                                   "Progress so far is in the transcript and LEDGER.")
                pause_gate(self, poll_s=POLL_S)
                apply_model_switch(self)
                apply_deliberation_switch(self)
                apply_trait_additions(self)
                drain_injections(self)
                announce_finished_subruns(self)
                action, usage = next_action(self)
                if self._aborted():
                    raise RunAborted  # a kill during the completion preempts the action
                if action is None:
                    return self._finish_run(
                        "failed", "Orchestrator failed to produce a valid action "
                                  f"after {MAX_SCHEMA_ATTEMPTS} attempts.")
                ctx.turn += 1
                ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage,
                                     **({"phase": ctx.phase} if ctx.phase else {}),
                                     **({"referred": True} if getattr(self, "_referred_turn", False)
                                        else {}))
                notes.capture(ctx, action)   # the note channel: turn-free, stamped, best-effort
                ctx.add_usage(usage)
                self.messages.append({"role": "assistant",
                                      "content": json.dumps(action, ensure_ascii=False)})
                self._record_turn(action)
                repeat_streak = self._repeat_streak(action)
                if repeat_streak >= REPEAT_FAIL:
                    return self._finish_run(
                        "failed", f"Stuck: the same action was repeated "
                                  f"{repeat_streak} times in a row. Aborting the run.")

                if action["kind"] == "finish":
                    if action["status"] == "ok" and self.executed_actions == 0 and ctx.depth == 0:
                        # Fabrication guard: a top-level ok-finish as the very first action
                        # is a hallucinated completion (the classic no-tools failure mode) —
                        # no observation exists that could ground any of its claims.
                        obs = {"kind": "finish", "rejected": True}
                        ctx.transcript.event("observation", obs, turn=ctx.turn)
                        self.messages.append({"role": "user", "content":
                            "OBSERVATION (finish REJECTED): you have not executed a single "
                            "action this run, so the workflow cannot be complete and none of "
                            "your claims have observations behind them. Start at workflow "
                            "step 1 and do the actual work, one action per turn."})
                        ctx.write_status()
                        continue
                    if action["status"] == "ok" and ctx.depth == 0:
                        # Claim guard (D31=B): a top-level ok-finish whose summary claims a
                        # high-signal action (report_bug/ask_user/schedule_run) the run never
                        # took is narrated unperformed work — reject so the run either takes
                        # the action or drops the claim. Meta routines are exempt (they quote
                        # other runs' actions); see finish_guard.py.
                        unbacked = unbacked_action_claims(
                            action.get("summary", ""),
                            {r["kind"] for r in self.turn_records},
                            is_meta="meta" in (getattr(ctx.routine, "tags", None) or []))
                        if unbacked:
                            obs = {"kind": "finish", "rejected": True,
                                   "unbacked_claims": unbacked}
                            ctx.transcript.event("observation", obs, turn=ctx.turn)
                            names = ", ".join(unbacked)
                            self.messages.append({"role": "user", "content":
                                f"OBSERVATION (finish REJECTED): your summary states you "
                                f"performed {names}, but no such action was taken this run. "
                                f"Either actually take the action now, or remove that claim "
                                f"from your summary, then finish again."})
                            ctx.write_status()
                            continue
                    self.final_summary = action["summary"]
                    return self._finish_run(action["status"], action["summary"], authored=True)
                if action["kind"] == "ask_user":
                    obs = interact.handle_ask(self, action, poll_s=POLL_S)
                elif action["kind"] == "write_util":
                    obs = interact.handle_write_util(self, action, poll_s=POLL_S)
                elif action["kind"] == "remove_util":
                    obs = interact.handle_remove_util(self, action, poll_s=POLL_S)
                elif action["kind"] == "schedule_run":
                    obs = interact.handle_schedule_run(self, action)
                elif action["kind"] == "report_bug":
                    obs = interact.handle_report_bug(self, action)
                elif action["kind"] == "spawn":
                    obs = self.subruns.spawn(action)
                elif action["kind"] == "subtask":
                    obs = self.subruns.subtask(action)
                elif action["kind"] == "detach":
                    obs = detach.handle_detach(ctx, action)
                elif action["kind"] == "subruns":
                    obs = self.subruns.status_table()
                elif action["kind"] == "kill":
                    obs = self.subruns.kill(action["n"])
                elif action["kind"] == "wait":
                    obs = self.subruns.wait(action, poll_s=POLL_S, aborted=self._aborted)
                else:
                    obs = executor.dispatch(action, ctx)
                ctx.transcript.event("observation", obs, turn=ctx.turn)
                self.executed_actions += 1
                text = format_observation(obs)
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
                    text += (f"\n[BUDGET: {warning} — wind down DELIBERATELY now: record what "
                             "matters (LEDGER, state files), then finish with an authored "
                             "summary. An engine-forced stop loses your conclusions.]")
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
        killed = self.subruns.kill_all(reason=f"parent run finished ({status})")
        if killed:
            summary += f"\n[{killed} still-running sub-workflow(s) were terminated at run end.]"
        ctx.transcript.event("finish", {"status": status, "summary": summary, "authored": authored},
                             usage_total=ctx.usage_total(), turns=ctx.turn)
        if status in ("partial", "failed", "aborted") and ctx.depth == 0:
            event_type = "budget_exhausted" if status == "partial" else "run_failed"
            log_health_event(ctx.server.routines_home, event_type,
                             routine=ctx.routine.slug, run_id=ctx.run_id,
                             detail=summary[:500])
        self.final_summary = self.final_summary or summary
        if ctx.depth == 0:
            (ctx.run_dir / "result.md").write_text(summary + "\n", encoding="utf-8")
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

    def _repeat_streak(self, action: dict) -> int:
        key = {k: v for k, v in action.items() if k != "say"}
        digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode("utf-8"),
                              usedforsecurity=False).hexdigest()
        self.repeat_hashes.append(digest)
        streak = 0
        for h in reversed(self.repeat_hashes):
            if h != digest:
                break
            streak += 1
        return streak
