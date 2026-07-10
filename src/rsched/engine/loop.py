"""The engine run loop — the workflow-as-harness core.

Turn cycle: budget check → pause gate → inbox drain → sub-workflow exit notifications →
compaction → one completion (schema-validated, ≤2 retries) → dispatch → observation.
Control-flow kinds (spawn/subruns/kill/wait, ask_user, finish) are handled here; effect
kinds go through executor.dispatch. Sub-workflows run in parallel threads (subruns.py)
and never outlive the parent.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from pathlib import Path

from ..config import ServerConfig, load_routine
from ..endpoints import EndpointRegistry
from ..endpoints.base import EndpointError
from ..frontmatter import load as load_frontmatter
from ..ids import question_id, run_ts as make_run_ts
from ..paths import read_json
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import executor, inbox
from .actions import ACTION_SCHEMA, normalize_action, validate_action
from .composer import (COMPACT_AT_FRACTION, KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, build_system_prompt,
                       compact_to_history, format_observation, kickoff_message, maybe_compact,
                       messages_size, replay_messages, state_digest, truncate)
from .run_context import Budgets, RunContext
from .subruns import SubrunManager
from .transcript import Transcript

POLL_S = 2.0
MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn
REPEAT_WARN = 3
REPEAT_FAIL = 5

_ABORT = {"flag": False}
_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm")


def request_abort() -> None:
    _ABORT["flag"] = True


def _is_approval(text: str) -> bool:
    return text.strip().lower().split()[0] in _APPROVE_WORDS if text.strip() else False


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


class RunAborted(Exception):
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
        # is always permitted so a run can end. None = every tool allowed.
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
                self._pause_gate()
                self._apply_model_switch()
                self._drain_injections()
                self._announce_finished_subruns()
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
                if self.allowed_tools is not None and action["kind"] not in self.allowed_tools:
                    obs = {"kind": action["kind"], "not_allowed": True,
                           "allowed": sorted(self.allowed_tools)}
                    ctx.transcript.event("observation", obs, turn=ctx.turn)
                    self.messages.append({"role": "user", "content":
                        f"OBSERVATION ({action['kind']} NOT AVAILABLE): this workflow permits only "
                        f"{sorted(self.allowed_tools)}. Use one of those — do not attempt other tools."})
                    ctx.write_status()
                    continue
                if action["kind"] == "ask_user":
                    obs = self._handle_ask(action)
                elif action["kind"] == "write_util":
                    obs = self._handle_write_util(action)
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

    def _announce_finished_subruns(self) -> None:
        """Turn-boundary notification: children that exited since the last boundary."""
        for sub in self.subruns.take_finished_unannounced():
            summary, _ = truncate(sub.summary, cap=4000)
            self.messages.append({"role": "user", "content":
                f"SUB-WORKFLOW FINISHED — #{sub.n} {sub.label!r} (workflow {sub.workflow}, "
                f"status {sub.status}, {sub.ctx.turn} turns):\n{summary}"})

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
            try:
                candidate = (completion.parsed if completion.parsed is not None
                             else extract_json(completion.text))
                candidate = normalize_action(candidate)
                problems = validate(candidate, ACTION_SCHEMA) or validate_action(candidate)
                if problems:
                    raise SchemaViolation(problems)
                return candidate, usage_sum
            except SchemaViolation as exc:
                raw = completion.text or json.dumps(completion.parsed or {})
                ctx.transcript.event("error", {"where": "schema", "attempt": attempt,
                                               "message": str(exc)[:500], "raw": raw[:1500]})
                self.messages.append({"role": "assistant", "content": raw[:4000]})
                self.messages.append({"role": "user", "content": retry_message(exc.problems)})
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
        brief_field = {"util": "name", "write_util": "name", "read_file": "path",
                       "write_file": "path", "llm": "prompt", "spawn": "label", "kill": "n",
                       "wait": "n", "ask_user": "question", "finish": "status"}.get(action["kind"], "")
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

    def _apply_model_switch(self) -> None:
        """Turn-boundary: honour a mid-run model switch written to control.json by the web layer.
        Edge-triggered on the signal's `ts` so the engine never has to write control.json (which
        stays web-owned). The switch lands on the NEXT completion, since for_model re-resolves
        ctx.routine.models every turn — the model, its context size, and effort all self-correct."""
        from ..config import ModelRef

        ctx = self.ctx
        obj = read_json(ctx.root_run_dir / "control.json")
        sw = obj.get("switch_model") if isinstance(obj, dict) else None
        if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == self._last_switch_ts:
            return
        self._last_switch_ts = str(sw["ts"])
        applied = []
        for kind in ("main", "subroutine", "tool_call"):
            spec = sw.get(kind)
            if (isinstance(spec, dict) and spec.get("endpoint") in ctx.server.endpoints
                    and spec.get("model")):
                ctx.routine.models[kind] = ModelRef(endpoint=str(spec["endpoint"]),
                                                    model=str(spec["model"]), effort=spec.get("effort"))
                applied.append(f"{kind} → {spec['endpoint']}/{spec['model']}")
        if applied:
            note = "model switched mid-run: " + "; ".join(applied)
            ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
            self.messages.append({"role": "user", "content":
                f"ENGINE NOTE: {note}. Continue the run on the new model."})

    def _pause_gate(self) -> None:
        ctx = self.ctx
        control = ctx.root_run_dir / "control.json"
        obj = read_json(control)
        if not (isinstance(obj, dict) and obj.get("pause")):
            return
        ctx.write_status("paused")
        started = time.monotonic()
        while True:
            if self._aborted():
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

    def _handle_write_util(self, action: dict) -> dict:
        from .. import utils_lib

        ctx = self.ctx
        name, content = action["name"], action["content"]
        if ctx.depth > 0:
            return {"kind": "write_util", "name": name, "declined": True,
                    "reason": "sub-workflows cannot create/revise utils — use existing ones"}
        home = ctx.server.utils_home
        utils_lib.ensure_library(home, remote=ctx.server.utils_remote)
        creating = not utils_lib.exists(home, name)
        if ctx.routine.confirm_utils(ctx.server):
            verb = "create" if creating else "revise"
            ask = self._handle_ask({
                "question": f"Approve {verb} of global util '{name}'? First lines:\n"
                            f"{content.strip()[:400]}",
                "mode": "blocking", "options": ["approve", "decline"]})
            if not ask.get("answered"):
                return {"kind": "write_util", "name": name, "pending_approval": True,
                        "qid": ask.get("qid")}
            if not _is_approval(ask["answer"]):
                return {"kind": "write_util", "name": name, "declined": True}
        utils_lib.write_util_file(home, name, content)
        ok, output = utils_lib.selftest(home, name)
        if not ok:
            return {"kind": "write_util", "name": name, "created": creating,
                    "selftest_ok": False, "output": output[:2000]}
        utils_lib.git_commit(home, f"{'create' if creating else 'revise'} {name}")
        return {"kind": "write_util", "name": name, "created": creating, "selftest_ok": True}

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
            if self._aborted():
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

# --- top-level entry ------------------------------------------------------------------


def _ensure_decomposed(routine_dir: Path, cfg, server) -> None:
    """A routine created as (workflow + instruction) but not yet turned into files — the wizard's
    clarify session is exactly this — has no main.md. Decompose its workflow against its instruction
    now (the SAME operation scaffold does at creation), so the run follows tailored MARKDOWN, never a
    raw pattern. Degrades to the whole workflow rendered as main.md if no endpoint is available."""
    if (routine_dir / "main.md").exists() or not cfg.workflow_slug:
        return
    from .. import frontmatter
    from ..workflows import library
    from ..workflows.adapt import decompose

    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8") \
        if (routine_dir / "instruction.md").exists() else ""
    result = decompose(server, cfg.workflow_slug, instruction)
    try:
        meta, _, _ = library.read_workflow(server.library_home, cfg.workflow_slug)
    except FileNotFoundError:
        meta = {}
    main_meta = {"name": cfg.name, "slug": cfg.slug,
                 "materialized_from": {"slug": cfg.workflow_slug,
                                       "commit": library.head_commit(server.library_home),
                                       "version": meta.get("version", 0)},
                 "modules": sorted(result["modules"])}
    if meta.get("tools") is not None:
        main_meta["tools"] = meta["tools"]
    if meta.get("includes"):
        main_meta["includes"] = list(meta["includes"])
    (routine_dir / "steps").mkdir(exist_ok=True)
    for mod_name, mod_body in result["modules"].items():
        (routine_dir / "steps" / f"{mod_name}.md").write_text(mod_body.rstrip() + "\n", encoding="utf-8")
    (routine_dir / "main.md").write_text(frontmatter.dump(main_meta, result["main"]), encoding="utf-8")


def load_workflow(routine_dir, cfg, server) -> tuple[str, str, dict, list[str] | None]:
    """Load the routine's OWN main.md body (the recipe was materialized into it at generation)
    plus its active FRAGMENTS. Returns (main_body, fragments_text, provenance, allowed_tools).

    A routine is self-contained: nothing is read from the workflow library at run time. Fragments
    are the routine's editable copies under fragments/; if that dir is empty we fall back to the
    library by cfg.fragments (transitional)."""
    from .. import fragments_lib

    # The recipe is materialized into the routine's OWN main.md at generation, so a routine is
    # self-contained — the workflow library is NOT read at run time. The model reads the step
    # modules under steps/ on demand via read_file (main.md routes to them).
    main = routine_dir / "main.md"
    if not main.exists():
        raise RuntimeError(f"routine {cfg.slug!r} has no main.md — cannot run")
    meta, mbody = load_frontmatter(main)
    body = mbody.strip()
    src = meta.get("materialized_from") if isinstance(meta.get("materialized_from"), dict) else {}
    prov = {"slug": src.get("slug", cfg.workflow_slug),
            "commit": src.get("commit", cfg.workflow_commit), "version": src.get("version", 0)}

    # active fragments: prefer the routine's editable copies, else the library
    parts: list[str] = []
    frag_dir = routine_dir / "fragments"
    files = sorted(frag_dir.glob("*.md")) if frag_dir.is_dir() else []
    if files:
        parts = [fragments_lib.fragment_body(p.read_text(encoding="utf-8")).strip() for p in files]
    else:
        for slug in cfg.fragments:
            content = fragments_lib.read_fragment(server.fragments_home, slug)
            if content:
                parts.append(fragments_lib.fragment_body(content).strip())
    tools = meta.get("tools") if isinstance(meta.get("tools"), list) else None
    return body, "\n\n".join(parts), prov, tools


def run_routine(routine_dir: Path, server: ServerConfig, *, run_ts: str | None = None,
                model_overrides: dict | None = None, on_event=None,
                resume_from: str | None = None) -> tuple[str, Path]:
    """Execute one run of the routine at routine_dir. Returns (final status, run dir).
    on_event(obj) is called for every transcript event (used by `rsched run-once`). When
    resume_from is a prior run's ts, that run dir is reused and its transcript is rehydrated
    into the prompt so the run continues where it left off (with a fresh budget window)."""
    cfg, problems = load_routine(routine_dir)
    if cfg is None:
        raise RuntimeError("; ".join(problems))
    fatal = [p for p in problems if "missing" in p]
    if fatal:
        raise RuntimeError(f"routine {routine_dir.name}: " + "; ".join(fatal))
    if model_overrides:
        cfg.models.update(model_overrides)
    registry = EndpointRegistry(server)
    ts = resume_from or run_ts or make_run_ts()
    run_dir = routine_dir / "runs" / ts
    if resume_from and not run_dir.is_dir():
        raise RuntimeError(f"cannot resume {ts}: run dir not found")
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript = Transcript(run_dir / "transcript.jsonl")   # append mode — resume adds after the tail
    if on_event:
        transcript.on_event = on_event  # type: ignore[attr-defined]
        _orig_write = transcript.write

        def write_and_echo(obj: dict) -> None:
            _orig_write(obj)
            on_event(obj)

        transcript.write = write_and_echo  # type: ignore[method-assign]
    _, orch_ref = registry.for_model("main", cfg.models)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=ts,
                     run_dir=run_dir, transcript=transcript,
                     budgets=Budgets.from_config(cfg.budgets))
    if not resume_from:
        _ensure_decomposed(routine_dir, cfg, server)   # workflow + instruction → main.md, if not yet
    body, fragments_text, prov, allowed_tools = load_workflow(routine_dir, cfg, server)
    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8")
    if not resume_from:            # a resumed run keeps the original header (transcript is append-only)
        transcript.header(run_id=ctx.run_id, routine=cfg.slug, workflow=prov,
                          orchestrator={"endpoint": orch_ref.endpoint, "model": orch_ref.model})
    status = EngineLoop(ctx, body, instruction, fragments_text=fragments_text,
                        allowed_tools=allowed_tools, resume=bool(resume_from)).run()
    return status, run_dir
