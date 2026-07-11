"""The engine turn loop — the workflow-as-harness core.

Turn cycle: budget check → pause gate → inbox drain → sub-workflow exit notifications →
compaction → one completion (schema-validated, ≤2 retries) → dispatch → observation.
Control-flow kinds (spawn/subruns/kill/wait) are handled here; ask_user and write_util in
interact.py; effect kinds go through executor.dispatch. Between-turn concerns (pause,
model switch, injections, subrun announcements) live in control.py; the top-level entry
(run_routine) in runtime.py. Sub-workflows run in parallel threads (subruns.py) and never
outlive the parent.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from pathlib import Path

from ..endpoints.base import EndpointError
from ..paths import read_json
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import executor, inbox, interact
from .actions import ACTION_SCHEMA, BRIEF_FIELD, KIND_EXAMPLES, normalize_action, validate_action
from .composer import build_system_prompt, format_observation, kickoff_message, state_digest
from .control import (_ABORT, RunAborted, announce_finished_subruns, apply_model_switch,
                      drain_injections, pause_gate, request_abort)
from .history import (COMPACT_AT_FRACTION, KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, compact_to_history,
                      maybe_compact, messages_size, replay_messages)
from .run_context import RunContext
from .subruns import SubrunManager

POLL_S = 2.0
MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn
REPEAT_WARN = 3
REPEAT_FAIL = 5

__all__ = ["EngineLoop", "RunAborted", "request_abort", "POLL_S",
           "MAX_SCHEMA_ATTEMPTS", "REPEAT_WARN", "REPEAT_FAIL"]


def _autocommit(routine_dir: Path, message: str) -> None:
    """Commit the routine's working dir at run end with the neutral identity (best-effort).
    Routines have no shell, so the engine owns version control of their state/outputs."""
    import subprocess

    if not (routine_dir / ".git").is_dir():
        return
    try:
        subprocess.run(["git", "-C", str(routine_dir), "add", "-A"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(routine_dir),
                        "-c", "user.name=routine-scheduler",
                        "-c", "user.email=noreply@routine-scheduler.local",
                        "commit", "-qm", message], capture_output=True, timeout=30)
    except OSError:
        pass


class EngineLoop:
    def __init__(self, ctx: RunContext, workflow_body: str, instruction: str,
                 abort_event: threading.Event | None = None, fragments_text: str = "",
                 allowed_tools: list[str] | None = None, resume: bool = False):
        self.ctx = ctx
        self.workflow_body = workflow_body
        self.instruction = instruction
        self.fragments_text = fragments_text
        self.resume = resume     # rehydrate the prior transcript into the prompt instead of a clean start
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
        self.executed_actions = 0  # actions that produced an observation this run
        self.util_reminder = self._build_util_reminder()
        self._last_switch_ts = ""   # edge-trigger for mid-run model switches (control.json)
        # Once the conversation has been archived to on-disk history, every turn reminds the model
        # to consult its index (compaction-proof — re-appended to the observation each turn).
        self._history_active = False
        try:
            hist_rel = str((ctx.run_dir / "history").relative_to(ctx.routine.dir))
        except ValueError:
            hist_rel = "history"
        self._hist_rel = hist_rel
        self._history_note = (f"\n[history: earlier turns are archived under {hist_rel}/INDEX.md — "
                              "read_file the index and the relevant files before relying on memory.]")

    def _build_util_reminder(self) -> str:
        # Per-turn nudge, only when the global-utils fragment is active for this routine (req 2).
        if "global-utils" not in (self.ctx.routine.fragments or []):
            return ""
        create = ("write_util to create/revise one"
                  + (" (needs your approval first)"
                     if self.ctx.routine.confirm_utils(self.ctx.server) else ""))
        return ("\n[tools: run `util name=list` to see the available global utils and their "
                f"usage; if none fits, {create}.]")

    def _aborted(self) -> bool:
        return _ABORT["flag"] or self.abort_event.is_set()

    # --- lifecycle ---------------------------------------------------------------

    def run(self) -> str:
        ctx = self.ctx
        try:
            self._boot()
            while True:
                if self._aborted():
                    raise RunAborted()
                if violation := ctx.budget_violation():
                    return self._finish_run("partial", f"Run stopped by the engine: {violation}. "
                                                       "Progress so far is in the transcript and LEDGER.")
                pause_gate(self, poll_s=POLL_S)
                apply_model_switch(self)
                drain_injections(self)
                announce_finished_subruns(self)
                action, usage = self._next_action()
                if self._aborted():
                    raise RunAborted()  # a kill during the completion preempts the action
                if action is None:
                    return self._finish_run("failed", "Orchestrator failed to produce a valid action "
                                                      f"after {MAX_SCHEMA_ATTEMPTS} attempts.")
                ctx.turn += 1
                ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage)
                ctx.add_usage(usage)
                self.messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                self._record_turn(action)
                repeat_streak = self._repeat_streak(action)
                if repeat_streak >= REPEAT_FAIL:
                    return self._finish_run("failed", f"Stuck: the same action was repeated "
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
                    self.final_summary = action["summary"]
                    return self._finish_run(action["status"], action["summary"], authored=True)
                if action["kind"] == "ask_user":
                    obs = interact.handle_ask(self, action, poll_s=POLL_S)
                elif action["kind"] == "write_util":
                    obs = interact.handle_write_util(self, action, poll_s=POLL_S)
                elif action["kind"] == "spawn":
                    obs = self.subruns.spawn(action)
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
                    text += (f"\n[ENGINE WARNING: this exact action has now run {repeat_streak} times "
                             f"in a row — {REPEAT_FAIL} identical actions fail the run. Change course.]")
                text += self.util_reminder
                if self._history_active:
                    text += self._history_note
                self.messages.append({"role": "user", "content": text})
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

    def _boot(self) -> None:
        ctx = self.ctx
        ctx.write_status("starting")
        if ctx.depth == 0:
            deferred_qa = inbox.collect_deferred_answers(ctx.routine.dir, self.consumed_dir)
            open_qs = inbox.open_questions(ctx.routine.dir)
            msgs = inbox.drain_messages(ctx.routine.dir, self.consumed_dir)
            digest = state_digest(ctx.routine.dir, deferred_qa, open_qs)
        else:
            msgs = []
            digest = "(subrun — no routine state digest; everything you need is in the instruction)"
        phase = read_json(ctx.routine.dir / "state" / "phase.json")
        if isinstance(phase, dict) and phase.get("phase"):
            ctx.phase = str(phase["phase"])
        system = build_system_prompt(ctx, self.workflow_body, self.instruction, digest, msgs,
                                     fragments_text=self.fragments_text)
        if self.resume and ctx.depth == 0:
            from .transcript import read_events
            events, _ = read_events(ctx.run_dir / "transcript.jsonl", 0)
            replayed, last_turn, records = replay_messages(events, self.util_reminder)
            self.messages = [{"role": "system", "content": system}, *replayed]
            self.turn_records = records
            ctx.turn = last_turn
            ctx.budget_base_turn = last_turn        # a fresh budget window from the resume point
            ctx.transcript.event("user_injection", {"text": "run resumed after interruption",
                                                    "source": "engine"})
            self.messages.append({"role": "user", "content":
                "ENGINE NOTE: this run was interrupted (budget/error) and is now RESUMED. The "
                "conversation above is the run so far — continue from the last observation; do NOT "
                "restart from step 1. Re-orient briefly, then proceed."})
        else:
            self.messages = [{"role": "system", "content": system},
                             {"role": "user", "content": kickoff_message(ctx)}]
        ctx.write_status("running")

    def _finish_run(self, status: str, summary: str, *, authored: bool = False) -> str:
        ctx = self.ctx
        killed = self.subruns.kill_all(reason=f"parent run finished ({status})")
        if killed:
            summary += f"\n[{killed} still-running sub-workflow(s) were terminated at run end.]"
        ctx.transcript.event("finish", {"status": status, "summary": summary, "authored": authored},
                             usage_total=dict(ctx.usage), turns=ctx.turn)
        self.final_summary = self.final_summary or summary
        if ctx.depth == 0:
            (ctx.run_dir / "result.md").write_text(summary + "\n", encoding="utf-8")
            _autocommit(ctx.routine.dir, f"{ctx.run_id}: {status}")  # routines never run git
            state = {"ok": "finished", "partial": "finished", "failed": "failed",
                     "aborted": "aborted"}.get(status, "finished")
            ctx.write_status(state, question=None)
        return status

    # --- turn pieces -------------------------------------------------------------

    def _next_action(self) -> tuple[dict | None, dict]:
        ctx = self.ctx
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
        ctx.main_model = f"{ref.endpoint}/{ref.model}"     # surfaced in status.json; updates on a switch
        self._compact_if_needed(endpoint, ref)
        usage_sum = {"in": 0, "out": 0}
        schema = ACTION_SCHEMA
        prev_raw: str | None = None
        for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
            # Generous output cap: reasoning models need room to think AND answer — a
            # provider's small default can swallow the content entirely.
            completion = endpoint.complete(self.messages, model=ref.model,
                                           schema=schema, effort=ref.effort,
                                           max_tokens=16_384)
            usage_sum["in"] += completion.usage["in"]
            usage_sum["out"] += completion.usage["out"]
            if completion.parsed is None and not completion.text.strip():
                # Empty reply = provider hiccup, not a model mistake: retry cleanly (no
                # poisoned context); the last attempt drops the provider-side format
                # constraint entirely — the contract in the system prompt still demands JSON.
                ctx.transcript.event("error", {"where": "endpoint", "attempt": attempt,
                                               "message": "empty completion (no content/reasoning)"})
                if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                    schema = None
                time.sleep(1.5 * attempt)
                continue
            kind_hint = None
            try:
                candidate = (completion.parsed if completion.parsed is not None
                             else extract_json(completion.text))
                candidate = normalize_action(candidate)
                if isinstance(candidate, dict) and candidate.get("kind") in KIND_EXAMPLES:
                    kind_hint = candidate["kind"]
                problems = (validate(candidate, ACTION_SCHEMA)
                            or validate_action(candidate, allowed_kinds=self.allowed_tools))
                if problems:
                    raise SchemaViolation(problems)
                return candidate, usage_sum
            except SchemaViolation as exc:
                raw = completion.text or json.dumps(completion.parsed or {})
                repeated = prev_raw is not None and raw.strip() == prev_raw.strip()
                prev_raw = raw
                ctx.transcript.event("error", {"where": "schema", "attempt": attempt,
                                               "message": str(exc)[:500], "raw": raw[:1500]})
                self.messages.append({"role": "assistant", "content": raw[:4000]})
                self.messages.append({"role": "user", "content": retry_message(
                    exc.problems, example=KIND_EXAMPLES.get(kind_hint), repeated=repeated)})
        return None, usage_sum

    def _compact_if_needed(self, endpoint, ref) -> None:
        """When the prompt exceeds ~60% of context, archive the middle to a navigable on-disk
        history via the LLM (compact_to_history); fall back to the deterministic one-line digest if
        that fails, so a run never stalls on compaction."""
        ctx = self.ctx
        if (messages_size(self.messages) <= COMPACT_AT_FRACTION * endpoint.context_chars
                or len(self.messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS):
            return
        cinfo = None
        try:
            result = compact_to_history(self.messages, self.turn_records, endpoint, ref,
                                        ctx.run_dir, self._hist_rel)
        except Exception as exc:
            ctx.transcript.event("error", {"where": "compaction", "message": str(exc)[:300]})
            result = None
        if result is not None:
            self.messages, cinfo = result
            self._history_active = True
        else:
            self.messages, cinfo = maybe_compact(self.messages, self.turn_records, endpoint.context_chars)
        if cinfo:
            ctx.transcript.event("compaction", cinfo)

    def _record_turn(self, action: dict) -> None:
        brief = str(action.get(BRIEF_FIELD.get(action["kind"], ""), ""))[:80]
        self.turn_records.append({"turn": self.ctx.turn, "kind": action["kind"],
                                  "brief": json.dumps(brief, ensure_ascii=False),
                                  "say": action.get("say", "")})

    def _repeat_streak(self, action: dict) -> int:
        key = {k: v for k, v in action.items() if k != "say"}
        digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()
        self.repeat_hashes.append(digest)
        streak = 0
        for h in reversed(self.repeat_hashes):
            if h != digest:
                break
            streak += 1
        return streak
