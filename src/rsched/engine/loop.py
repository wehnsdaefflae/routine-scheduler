"""The engine run loop — the workflow-as-harness core.

Turn cycle: budget check → pause gate → inbox drain → compaction → one completion
(schema-validated, ≤2 retries) → dispatch → observation. Control-flow kinds (ask_user,
subinstruction, finish) are handled here; effect kinds go through executor.dispatch.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from pathlib import Path

from ..config import RoutineConfig, ServerConfig, load_routine
from ..endpoints import EndpointRegistry
from ..endpoints.base import EndpointError
from ..frontmatter import load as load_frontmatter
from ..ids import question_id, run_ts as make_run_ts
from ..paths import read_json
from ..schema_guard import SchemaViolation, parse_reply, retry_message, validate
from . import executor, inbox
from .actions import ACTION_SCHEMA, validate_action
from .composer import (SUBRUN_WORKFLOW, build_system_prompt, format_observation,
                       kickoff_message, maybe_compact, state_digest, truncate)
from .run_context import Budgets, RunContext
from .transcript import Transcript

POLL_S = 2.0
MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn
REPEAT_WARN = 3
REPEAT_FAIL = 5

_ABORT = {"flag": False}


def request_abort() -> None:
    _ABORT["flag"] = True


class RunAborted(Exception):
    pass


class EngineLoop:
    def __init__(self, ctx: RunContext, workflow_body: str, instruction: str):
        self.ctx = ctx
        self.workflow_body = workflow_body
        self.instruction = instruction
        self.messages: list[dict] = []
        self.turn_records: list[dict] = []
        self.repeat_hashes: deque[str] = deque(maxlen=REPEAT_FAIL)
        self.consumed_dir = ctx.root_run_dir / "consumed"
        self.final_summary = ""

    # --- lifecycle ---------------------------------------------------------------

    def run(self) -> str:
        ctx = self.ctx
        try:
            self._boot()
            while True:
                if _ABORT["flag"]:
                    raise RunAborted()
                if violation := ctx.budget_violation():
                    return self._finish_run("partial", f"Run stopped by the engine: {violation}. "
                                                       "Progress so far is in the transcript and LEDGER.")
                self._pause_gate()
                self._drain_injections()
                action, usage = self._next_action()
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
                    self.final_summary = action["summary"]
                    return self._finish_run(action["status"], action["summary"], authored=True)
                if action["kind"] == "ask_user":
                    obs = self._handle_ask(action)
                elif action["kind"] == "subinstruction":
                    obs = self._handle_subrun(action)
                else:
                    obs = executor.dispatch(action, ctx)
                ctx.transcript.event("observation", obs, turn=ctx.turn)
                text = format_observation(obs)
                if REPEAT_WARN <= repeat_streak < REPEAT_FAIL:
                    text += (f"\n[ENGINE WARNING: this exact action has now run {repeat_streak} times "
                             f"in a row — {REPEAT_FAIL} identical actions fail the run. Change course.]")
                self.messages.append({"role": "user", "content": text})
                ctx.write_status()
        except RunAborted:
            return self._finish_run("aborted", "Run aborted by the user/daemon.")
        except EndpointError as exc:
            self.ctx.transcript.event("error", {"where": "endpoint", "message": str(exc)})
            hint = " Run `gu claude-login` to refresh the subscription token." if exc.auth else ""
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
        system = build_system_prompt(ctx, self.workflow_body, self.instruction, digest, msgs)
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": kickoff_message(ctx)}]
        ctx.write_status("running")

    def _finish_run(self, status: str, summary: str, *, authored: bool = False) -> str:
        ctx = self.ctx
        ctx.transcript.event("finish", {"status": status, "summary": summary, "authored": authored},
                             usage_total=dict(ctx.usage), turns=ctx.turn)
        self.final_summary = self.final_summary or summary
        if ctx.depth == 0:
            (ctx.run_dir / "result.md").write_text(summary + "\n", encoding="utf-8")
            state = {"ok": "finished", "partial": "finished", "failed": "failed",
                     "aborted": "aborted"}.get(status, "finished")
            ctx.write_status(state, question=None)
        return status

    # --- turn pieces -------------------------------------------------------------

    def _next_action(self) -> tuple[dict | None, dict]:
        ctx = self.ctx
        endpoint, ref = ctx.registry.for_role("orchestrator", ctx.routine.roles)
        self.messages, cinfo = maybe_compact(self.messages, self.turn_records, endpoint.context_chars)
        if cinfo:
            ctx.transcript.event("compaction", cinfo)
        usage_sum = {"in": 0, "out": 0}
        for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
            completion = endpoint.complete(self.messages, model=ref.model,
                                           schema=ACTION_SCHEMA, effort=ref.effort)
            usage_sum["in"] += completion.usage["in"]
            usage_sum["out"] += completion.usage["out"]
            try:
                if completion.parsed is not None:
                    problems = validate(completion.parsed, ACTION_SCHEMA) or validate_action(completion.parsed)
                    if problems:
                        raise SchemaViolation(problems)
                    return completion.parsed, usage_sum
                return parse_reply(completion.text, ACTION_SCHEMA, validate_action), usage_sum
            except SchemaViolation as exc:
                ctx.transcript.event("error", {"where": "schema", "attempt": attempt,
                                               "message": str(exc)[:500]})
                raw = completion.text or json.dumps(completion.parsed or {})
                self.messages.append({"role": "assistant", "content": raw[:4000]})
                self.messages.append({"role": "user", "content": retry_message(exc.problems)})
        return None, usage_sum

    def _record_turn(self, action: dict) -> None:
        brief_field = {"shell": "command", "read_file": "path", "write_file": "path",
                       "llm": "prompt", "subinstruction": "prompt", "ask_user": "question",
                       "finish": "status"}.get(action["kind"], "")
        brief = str(action.get(brief_field, ""))[:80]
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

    def _pause_gate(self) -> None:
        ctx = self.ctx
        control = ctx.root_run_dir / "control.json"
        obj = read_json(control)
        if not (isinstance(obj, dict) and obj.get("pause")):
            return
        ctx.write_status("paused")
        started = time.monotonic()
        while True:
            if _ABORT["flag"]:
                raise RunAborted()
            time.sleep(POLL_S)
            obj = read_json(control)
            if not (isinstance(obj, dict) and obj.get("pause")):
                break
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running")

    def _drain_injections(self) -> None:
        ctx = self.ctx
        if ctx.depth > 0:
            return
        for text in inbox.drain_messages(ctx.routine.dir, self.consumed_dir):
            ctx.transcript.event("user_injection", {"text": text})
            self.messages.append({"role": "user",
                                  "content": f"USER MESSAGE (injected mid-run):\n{text}"})

    # --- control-flow kinds ---------------------------------------------------------

    def _handle_ask(self, action: dict) -> dict:
        ctx = self.ctx
        qid = question_id(ctx.run_ts, ctx.turn)
        mode = action.get("mode") or "deferred"
        if ctx.depth > 0:
            mode = "deferred"  # subruns cannot block the run on the user
        options = list(action.get("options") or [])
        question = action["question"]
        ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                          "options": options})
        if mode == "deferred":
            inbox.file_deferred_question(ctx.routine.dir, qid, question, options, ctx.run_ts)
            return {"kind": "ask_user", "qid": qid, "mode": mode}
        ctx.write_status("waiting_user",
                         question={"qid": qid, "question": question, "options": options,
                                   "asked": ctx.run_ts})
        deadline = time.monotonic() + ctx.budgets.ask_timeout_h * 3600
        started = time.monotonic()
        answer = None
        while time.monotonic() < deadline:
            if _ABORT["flag"]:
                raise RunAborted()
            answer = inbox.take_answer(ctx.routine.dir, qid, self.consumed_dir)
            if answer:
                break
            time.sleep(POLL_S)
        ctx.credit_suspended(time.monotonic() - started)
        ctx.write_status("running", question=None)
        if answer:
            ctx.transcript.event("answer", {"qid": qid, "text": answer["text"],
                                            "source": answer.get("source", "web")})
            return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                    "answer": answer["text"]}
        inbox.file_deferred_question(ctx.routine.dir, qid, question, options, ctx.run_ts)
        return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
                "timeout_h": ctx.budgets.ask_timeout_h}

    def _handle_subrun(self, action: dict) -> dict:
        ctx = self.ctx
        label = action.get("label") or f"subrun-{ctx.sub_counter[0] + 1}"
        if ctx.sub_counter[0] >= ctx.budgets.max_subruns:
            return {"kind": "subinstruction", "label": label, "status": "failed", "turns": 0,
                    "summary": f"REJECTED: subrun budget ({ctx.budgets.max_subruns}) exhausted."}
        if ctx.depth + 1 > ctx.budgets.max_subrun_depth:
            return {"kind": "subinstruction", "label": label, "status": "failed", "turns": 0,
                    "summary": f"REJECTED: max subrun depth ({ctx.budgets.max_subrun_depth}) reached."}
        ctx.sub_counter[0] += 1
        n = ctx.sub_counter[0]
        sub_dir = ctx.run_dir / "sub" / str(n)
        rel = str(sub_dir.relative_to(ctx.root_run_dir) / "transcript.jsonl")
        ctx.transcript.event("subrun_start", {"n": n, "label": label, "depth": ctx.depth + 1,
                                              "transcript": rel})
        sub_transcript = Transcript(sub_dir / "transcript.jsonl")
        _, orch_ref = ctx.registry.for_role("subcall", ctx.routine.roles)
        child = RunContext(
            routine=ctx.routine, server=ctx.server, registry=ctx.registry,
            run_ts=ctx.run_ts, run_dir=sub_dir, transcript=sub_transcript,
            budgets=ctx.child_budgets(), depth=ctx.depth + 1,
            parent_run_id=ctx.run_id, sub_counter=ctx.sub_counter,
        )
        # Subruns orchestrate on the (cheaper) subcall role.
        child.routine = _with_subrun_roles(ctx.routine, orch_ref)
        sub_transcript.header(run_id=f"{ctx.run_id}#sub{n}", routine=ctx.routine.slug,
                              workflow={"slug": "(builtin-subrun)", "commit": "", "version": 0},
                              orchestrator={"endpoint": orch_ref.endpoint, "model": orch_ref.model},
                              depth=ctx.depth + 1, parent=ctx.run_id)
        try:
            child_loop = EngineLoop(child, SUBRUN_WORKFLOW, action["prompt"])
            status = child_loop.run()
            summary = child_loop.final_summary
        finally:
            sub_transcript.close()
        ctx.add_usage(child.usage)
        ctx.transcript.event("subrun_end", {"n": n, "label": label, "status": status,
                                            "summary": summary, "turns": child.turn,
                                            "usage": dict(child.usage)})
        if _ABORT["flag"]:
            raise RunAborted()
        summary_text, _ = truncate(summary, cap=4000)
        return {"kind": "subinstruction", "label": label, "status": status,
                "turns": child.turn, "summary": summary_text}


def _with_subrun_roles(routine: RoutineConfig, orch_ref) -> RoutineConfig:
    """A shallow copy whose orchestrator role is the parent's subcall role."""
    import copy

    r = copy.copy(routine)
    r.roles = dict(routine.roles)
    r.roles["orchestrator"] = orch_ref
    return r


# --- top-level entry ------------------------------------------------------------------


def load_workflow(routine_dir: Path) -> tuple[str, dict]:
    meta, body = load_frontmatter(routine_dir / "workflow.md")
    prov = meta.get("materialized_from") or {}
    return body, {"slug": prov.get("slug", ""), "commit": prov.get("commit", ""),
                  "version": prov.get("version", 0)}


def run_routine(routine_dir: Path, server: ServerConfig, *, run_ts: str | None = None,
                role_overrides: dict | None = None, on_event=None) -> tuple[str, Path]:
    """Execute one run of the routine at routine_dir. Returns (final status, run dir).
    on_event(obj) is called for every transcript event (used by `rsched run-once`)."""
    cfg, problems = load_routine(routine_dir)
    if cfg is None:
        raise RuntimeError("; ".join(problems))
    fatal = [p for p in problems if "missing" in p]
    if fatal:
        raise RuntimeError(f"routine {routine_dir.name}: " + "; ".join(fatal))
    if role_overrides:
        cfg.roles.update(role_overrides)
    registry = EndpointRegistry(server)
    ts = run_ts or make_run_ts()
    run_dir = routine_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript = Transcript(run_dir / "transcript.jsonl")
    if on_event:
        transcript.on_event = on_event  # type: ignore[attr-defined]
        _orig_write = transcript.write

        def write_and_echo(obj: dict) -> None:
            _orig_write(obj)
            on_event(obj)

        transcript.write = write_and_echo  # type: ignore[method-assign]
    _, orch_ref = registry.for_role("orchestrator", cfg.roles)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=ts,
                     run_dir=run_dir, transcript=transcript,
                     budgets=Budgets.from_config(cfg.budgets))
    body, prov = load_workflow(routine_dir)
    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8")
    transcript.header(run_id=ctx.run_id, routine=cfg.slug,
                      workflow=prov or {"slug": cfg.workflow_slug, "commit": cfg.workflow_commit},
                      orchestrator={"endpoint": orch_ref.endpoint, "model": orch_ref.model})
    status = EngineLoop(ctx, body, instruction).run()
    return status, run_dir
