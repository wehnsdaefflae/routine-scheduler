"""Child-task scheduling: a parent routine runs child routines materialized from workflow
patterns — PARALLEL subroutines (`spawn`, non-blocking) and SEQUENTIAL subtasks (`subtask`,
blocking). Both are built by the shared executor (childrun.build_child); this module owns their
LIFECYCLE: start each in a thread, monitor (`subruns`), block on a subtask or `wait` for
parallel children, `kill`, and announce every exit to the parent at a turn boundary. Children
never outlive the parent: its finish/abort kills them.

Threading model: each child EngineLoop runs in its own thread and writes ONLY its own transcript
under sub/<n>/; all parent-transcript events are emitted from the parent thread (single writer
per file). Children carry a per-loop abort Event so one can be killed without touching siblings.
"""

from __future__ import annotations

import threading
import time

from . import inbox
from .childrun import Subrun, build_child
from .observations import truncate

MAX_PARALLEL = 4
KILL_JOIN_S = 12.0
POLL_S = 2.0
# Below this many tokens left, skip in-run workflow generation (≈2 full-context system-model
# calls) and fall back to the default pattern — generation must not tip a run over its budget.
GEN_FLOOR_TOKENS = 20_000


class SubrunManager:
    """The parent loop's window onto its children: `spawn` (a parallel subroutine), `subtask` (a
    sequential subtask the parent blocks on), monitor (`subruns`/`wait`), `kill`, auto-announce
    exits at turn boundaries. Both schedulers share the budget/depth/parallel caps and the
    child-run executor — a subtask and a subroutine differ only in scheduling.
    """

    def __init__(self, parent_loop):
        self.parent = parent_loop
        self.subruns: dict[int, Subrun] = {}
        # Completion hook: every child exit sets this, so a parent blocked in `wait` or on a
        # `subtask` wakes IMMEDIATELY instead of sleeping out a poll interval (or a whole timeout).
        self.exit_event = threading.Event()

    # -- caps + start (shared by both schedulers) -----------------------------------

    def _cap_reason(self, *, noun: str) -> str | None:
        """Why a new child cannot start now, or None. Budget + depth bound the WHOLE tree
        (parallel and sequential children alike); the parallel cap bounds concurrency.
        """
        ctx = self.parent.ctx
        if ctx.sub_counter[0] >= ctx.budgets.max_subruns:
            return f"{noun} budget ({ctx.budgets.max_subruns}) exhausted"
        if ctx.depth + 1 > ctx.budgets.max_subrun_depth:
            return f"max {noun} depth ({ctx.budgets.max_subrun_depth}) reached"
        running = sum(1 for s in self.subruns.values() if s.status == "running")
        if running >= MAX_PARALLEL:
            return (f"{running} child-tasks already running (parallel cap {MAX_PARALLEL}) — "
                    "wait for or kill one first")
        return None

    def _start(self, sub: Subrun) -> None:
        """Register the child and run its EngineLoop in a daemon thread; its exit sets the
        completion events so a blocked parent wakes at once.
        """
        def run_child() -> None:
            try:
                sub.status = sub.loop.run()
                sub.summary = sub.loop.final_summary
            except Exception as exc:
                sub.status = "failed"
                sub.summary = f"sub-routine crashed: {exc}"
            finally:
                sub.ctx.transcript.close()
                sub.done.set()
                self.exit_event.set()

        thread = threading.Thread(target=run_child, name=f"child-{sub.n}", daemon=True)
        sub.thread = thread
        self.subruns[sub.n] = sub
        thread.start()

    # -- spawn (parallel) -----------------------------------------------------------

    def spawn(self, action: dict) -> dict:
        ctx = self.parent.ctx
        default_label = f"sub-{ctx.sub_counter[0] + 1}"
        if reason := self._cap_reason(noun="child-task"):
            return {"kind": "spawn", "rejected": True,
                    "label": action.get("label") or default_label, "reason": reason}
        running = sum(1 for s in self.subruns.values() if s.status == "running")
        sub = build_child(ctx, action, mode="parallel", default_label=default_label,
                          emit=ctx.transcript.event)
        self._start(sub)
        return {"kind": "spawn", "n": sub.n, "label": sub.label, "workflow": sub.workflow,
                "note": sub.note, "running": running + 1}

    # -- subtask (sequential, blocking) ---------------------------------------------

    def subtask(self, action: dict) -> dict:
        """Start ONE SEQUENTIAL child — NON-BLOCKING, so the conversation stays live while it
        runs. Mechanically a subrun tagged `sequential` + a `turns` budget: it runs in its own
        thread and its finish is delivered by the turn-boundary hook (`announce_finished_subruns`),
        never by monopolizing this turn. The parent keeps sequential order by WAITING for it (a
        responsive `wait n=N`, which yields to user input) before starting the next subtask, and
        folds the announced result into that next brief. `turns` pins its budget (else half the
        parent's remainder).
        """
        ctx = self.parent.ctx
        default_label = f"task-{ctx.sub_counter[0] + 1}"
        if reason := self._cap_reason(noun="child-task"):
            return {"kind": "subtask", "rejected": True,
                    "label": action.get("label") or default_label, "reason": reason}
        action, gen_note = self._maybe_generate(dict(action))
        turns = action.get("turns")
        overrides = {"turns": int(turns)} if isinstance(turns, int) and turns > 0 else None
        sub = build_child(ctx, action, mode="sequential", default_label=default_label,
                          alloc_overrides=overrides, emit=ctx.transcript.event)
        self._start(sub)
        return {"kind": "subtask", "n": sub.n, "label": sub.label, "workflow": sub.workflow,
                "note": gen_note, "started": True}

    def _maybe_generate(self, action: dict) -> tuple[dict, str]:
        """Resolve a `workflow: "generate"` request into a concrete library slug. When the
        routine holds the `workflows: generate` capability and the budget allows, DRAFT a new
        pattern for the subtask's brief (folding the generation call's spend into the run via
        ctx.add_usage); otherwise fall back to the default pattern with a note. Returns
        (action, note).
        """
        if action.get("workflow") != "generate":
            return action, ""
        ctx = self.parent.ctx
        grants = getattr(self.parent, "grants", None)
        if grants is None or not grants.may_generate_workflow():
            action["workflow"] = None
            return action, ("workflow generation is off for this routine (capability "
                            "'workflows: generate') — used the default pattern")
        remaining = ctx.tokens_remaining()
        if remaining is not None and remaining < GEN_FLOOR_TOKENS:
            action["workflow"] = None
            return action, ("skipped workflow generation — token budget nearly spent; "
                            "used the default pattern")
        try:
            from ..workflows.generate import generate

            slug, _ = generate(ctx.server, action["prompt"], on_usage=ctx.add_usage)
            action["workflow"] = slug
            return action, f"generated a new pattern '{slug}' for this subtask"
        except Exception as exc:
            action["workflow"] = None
            return action, f"workflow generation failed ({exc}) — used the default pattern"

    # -- lifecycle (shared) ---------------------------------------------------------

    def take_finished_unannounced(self) -> list[Subrun]:
        out = []
        for sub in self.subruns.values():
            if sub.done.is_set() and not sub.announced:
                sub.announced = True
                self._collect(sub)
                out.append(sub)
        return out

    def _collect(self, sub: Subrun) -> None:
        if not sub.collected:
            sub.collected = True
            self.parent.ctx.add_usage(sub.ctx.usage)
            self.parent.ctx.referrals += sub.ctx.referrals
            self.parent.ctx.transcript.event("subrun_end", {
                "n": sub.n, "label": sub.label, "workflow": sub.workflow, "mode": sub.mode,
                "status": sub.status, "summary": sub.summary,
                "turns": sub.ctx.turn, "usage": dict(sub.ctx.usage)})
            # children feed workflow-library optimization like any other run
            from ..health_events import log_workflow_usage

            pctx = self.parent.ctx
            log_workflow_usage(pctx.server.routines_home, routine=pctx.routine.slug,
                               run_id=f"{pctx.run_id}#sub{sub.n}", workflow=sub.workflow,
                               depth=sub.ctx.depth, status=sub.status or "unknown",
                               turns=sub.ctx.turn,
                               tokens=int(sub.ctx.usage.get("in", 0))
                                      + int(sub.ctx.usage.get("out", 0)),
                               cost=float(sub.ctx.usage.get("cost") or 0.0),
                               referrals=sub.ctx.referrals)

    def status_table(self) -> dict:
        rows = [{"n": sub.n, "label": sub.label, "workflow": sub.workflow,
                 "mode": sub.mode,
                 "state": sub.status if sub.done.is_set() else "running",
                 "turns": sub.ctx.turn,
                 "elapsed_s": round(time.monotonic() - sub.started_mono, 1),
                 "summary_head": (truncate(sub.summary, cap=200)[0]
                                  if sub.done.is_set() else "")}
                for sub in self.subruns.values()]
        return {"kind": "subruns", "count": len(rows), "rows": rows}

    def kill(self, n: int) -> dict:
        sub = self.subruns.get(int(n))
        if sub is None:
            return {"kind": "kill", "n": n, "error": f"no sub-workflow {n}"}
        if sub.done.is_set():
            return {"kind": "kill", "n": n, "already_finished": True, "status": sub.status}
        sub.abort_event.set()
        sub.done.wait(timeout=KILL_JOIN_S)
        return {"kind": "kill", "n": n, "killed": True,
                "status": sub.status if sub.done.is_set() else "stopping"}

    def wait(self, action: dict, *, poll_s: float, aborted) -> dict:
        """Block until a target child (n), all children, or any unreported exit. Wakes on the
        child's completion event, not on a poll tick — and an exit the parent has not been
        told about yet satisfies an any-wait immediately (a child that finished while the
        parent was composing this very action must not cost a full timeout).
        """
        n = action.get("n")
        want_all = bool(action.get("all"))
        timeout = float(action.get("timeout_s") or 600)
        deadline = time.monotonic() + timeout
        if not self.subruns or (n is not None and int(n) not in self.subruns):
            return {"kind": "wait", "error": "no such sub-workflow to wait for"
                    if n is not None else "no sub-workflows have been spawned"}

        def satisfied() -> bool:
            if n is not None:
                return self.subruns[int(n)].done.is_set()
            if want_all:
                return all(s.done.is_set() for s in self.subruns.values())
            # any-mode: an unreported exit satisfies at once; and once nothing is running
            # any longer, no future exit can arrive — blocking would burn the whole timeout.
            return (any(s.done.is_set() and not s.announced for s in self.subruns.values())
                    or all(s.done.is_set() for s in self.subruns.values()))

        while time.monotonic() < deadline:
            if aborted():
                break
            # RESPONSIVE: a user message arriving mid-wait must not be starved. Yield control
            # back to the turn loop (which drains it and lets the parent reply) instead of
            # freezing the conversation until the child finishes — the child keeps running and
            # is announced when it exits. Root runs only (children don't drain the routine inbox).
            if (self.parent.ctx.depth == 0
                    and inbox.has_pending_messages(self.parent.ctx.routine.dir)):
                finished = self.take_finished_unannounced()
                return {"kind": "wait", "interrupted_by_user": True, "timed_out": False,
                        "finished": self._finished_rows(finished),
                        "still_running": [s.n for s in self.subruns.values()
                                          if not s.done.is_set()]}
            self.exit_event.clear()
            # re-check after clear: an exit between check and wait must not be lost
            if satisfied():
                break
            self.exit_event.wait(timeout=min(poll_s, max(0.0, deadline - time.monotonic())))
        sat = satisfied()   # before collection below flips `announced` on the exits we report
        finished = self.take_finished_unannounced()
        return {"kind": "wait", "timed_out": not sat,
                "finished": self._finished_rows(finished),
                "still_running": [s.n for s in self.subruns.values() if not s.done.is_set()]}

    @staticmethod
    def _finished_rows(finished: list) -> list[dict]:
        return [{"n": s.n, "label": s.label, "status": s.status, "turns": s.ctx.turn,
                 "mode": s.mode, "summary": truncate(s.summary, cap=3000)[0]} for s in finished]

    def kill_all(self, *, reason: str) -> int:
        """Parent is exiting — children never outlive it."""
        killed = 0
        for sub in self.subruns.values():
            if not sub.done.is_set():
                sub.abort_event.set()
                killed += 1
        for sub in self.subruns.values():
            sub.done.wait(timeout=KILL_JOIN_S)
            if not sub.announced:
                sub.announced = True
                if not sub.done.is_set():
                    sub.status = "aborted"
                    sub.summary = f"killed: {reason} (did not stop in time)"
                self._collect(sub)
        return killed
