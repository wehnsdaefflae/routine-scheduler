"""CHILD RUN lifecycle for both `child` modes — PARALLEL and SEQUENTIAL;
the concept is `engine/child.py`, the build `childrun.build_child`. Start each in a thread,
monitor, `wait`, `kill`, announce exits at a turn boundary; children never outlive the parent.

Threading: each child EngineLoop writes ONLY its own transcript under sub/<n>/; parent events
come from the parent thread (one writer per file). A per-loop abort Event kills one child alone.
The tree-wide allowance and child numbers are decided under the tree's lock in one step
(`_admit`).
"""

from __future__ import annotations

import threading
import time

from . import child, subsalvage
from .childrun import Subrun, build_child, claim_child
from .observations import truncate

MAX_PARALLEL = 4
#: How long `kill` and the parent's exit wait for children — ONE deadline for all, never per
#: child. Shorter than `procgroup.TERM_GRACE_S` on purpose: a straggler finishes in its own
#: thread, and procgroup's armed backstop delivers the SIGKILL.
KILL_JOIN_S = 12.0


def _usage_snapshot(usage: dict) -> dict:
    """Copy a possibly-still-mutating usage dict (a child that ignored its abort): retry a
    concurrent-insert RuntimeError, then fall back to the guaranteed keys. Later spend is lost.
    """
    for _ in range(3):
        try:
            return dict(usage)
        except RuntimeError:
            continue
    return {"in": int(usage.get("in", 0)), "out": int(usage.get("out", 0))}


class SubrunManager:
    """The parent loop's window onto its CHILD RUNS. Both modes share the caps, the executor
    and the hand-back; they differ only in scheduling (engine/child.py).
    """

    def __init__(self, parent_loop):
        self.parent = parent_loop
        self.subruns: dict[int, Subrun] = {}
        # set by every child exit, so a parent blocked in `wait` wakes immediately
        self.exit_event = threading.Event()

    # -- caps + start (shared by both schedulers) -----------------------------------

    def _cap_reason(self, *, noun: str) -> str | None:
        """Why a new child cannot start now, or None. Budget + depth bound the WHOLE tree
        (parallel and sequential children alike); the parallel cap bounds concurrency.
        """
        ctx = self.parent.ctx
        if ctx.sub_counter[0] >= ctx.budgets.max_child_runs:
            # Say the budget is SHARED and never refills (D147-A), or a child refused at its
            # first spawn reads its siblings' spend as a fault in its own call.
            return (f"{noun} budget exhausted — all {ctx.budgets.max_child_runs} are spent. That "
                    f"budget is shared across this run tree (every node's children count "
                    f"against one total, and it never refills), so this is not a fault in your "
                    f"call: do the work here instead of decomposing it")
        if ctx.depth + 1 > ctx.budgets.max_child_depth:
            return f"max {noun} depth ({ctx.budgets.max_child_depth}) reached"
        if (running := self._running()) >= MAX_PARALLEL:
            return (f"{running} child-tasks already running (parallel cap {MAX_PARALLEL}) — "
                    "wait for or kill one first")
        return None

    def _model_reason(self, action: dict) -> str | None:
        """Why the action's `model` override (a ROLE or a CATALOG name) cannot serve a child,
        or None — a teaching rejection instead of a crash inside the child build.
        """
        ctx = self.parent.ctx
        sel = str(action.get("model") or "")
        if not sel or sel in ("main", "tool_call", "supervisor"):
            return None      # `supervisor` (D164) resolves through MAIN while unset
        if sel == "uncensored":
            if ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts) is None:
                return ("model role 'uncensored' is not configured for this routine — "
                        "it needs a models.uncensored catalog entry (routine page → "
                        "Models). Drop the override, or ask the user to set one.")
            return None
        if "/" in sel:
            # an endpoint spec is `llm`-only: a child resolves everything through the catalog
            return (f"model {sel!r} is an '<endpoint>/<model id>' spec, which only the `llm` "
                    "action takes — a child run's model must be a catalog name (its whole "
                    "config resolves through the catalog). Name a catalog model, or make the "
                    "off-catalog call yourself with `llm`.")
        if sel not in ctx.server.models:
            avail = ", ".join(sorted(ctx.server.models)) or "none configured"
            return (f"model {sel!r} is neither a role (main/tool_call/uncensored) nor a "
                    f"catalog model name. Catalog models: {avail}. The list_models "
                    "action shows each one's endpoint and attributes.")
        return None

    def _admit(self, action: dict) -> int | str:
        """Admit ONE new child — its claimed number — or the reason it cannot start.

        The `model` override is judged first (the call's own fault; a cap refusal says the call
        was fine). Check and claim then happen in one step under the tree's lock, or two
        siblings could both take the last child.
        """
        if reason := self._model_reason(action):
            return reason
        ctx = self.parent.ctx
        with ctx.sub_lock:
            if reason := self._cap_reason(noun="child-task"):
                return reason
            return claim_child(ctx)

    def _start_child(self, action: dict, *, mode: str, prefix: str,
                     overrides: dict | None = None) -> Subrun | str:
        """Admit, build and start ONE child in either mode — or the reason it cannot start.
        An unnamed child is labelled by its number (`sub-3`, `task-3`).
        """
        admitted = self._admit(action)
        if isinstance(admitted, str):
            return admitted
        ctx = self.parent.ctx
        label = action.get("label") or f"{prefix}-{admitted}"
        sub = build_child(ctx, action, n=admitted, label=label, mode=mode,
                          alloc_overrides=overrides, emit=ctx.transcript.event)
        self._start(sub)
        return sub

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
                sub.ended_mono = time.monotonic()
                sub.done.set()
                self.exit_event.set()

        self.subruns[sub.n] = sub
        threading.Thread(target=run_child, name=f"child-{sub.n}", daemon=True).start()

    def _running(self) -> int:
        return sum(1 for s in self.subruns.values() if s.status == "running")

    # -- spawn (parallel) -----------------------------------------------------------

    def spawn(self, action: dict) -> dict:
        running = self._running()
        sub = self._start_child(action, mode=child.PARALLEL, prefix="sub")
        if isinstance(sub, str):
            return {"kind": "child", "mode": child.PARALLEL, "rejected": True,
                    "label": action.get("label") or "", "reason": sub}
        return {"kind": "child", "mode": child.PARALLEL, "n": sub.n, "label": sub.label,
                "workflow": sub.workflow, "note": sub.note, "running": running + 1}

    # -- subtask (sequential) -------------------------------------------------------

    def subtask(self, action: dict) -> dict:
        """Start ONE SEQUENTIAL child — NON-BLOCKING, so the conversation stays live. Its finish
        arrives at a turn boundary (`announce_finished_subruns`); the parent keeps order by
        `wait n=N`. `turns` pins its budget (else half the parent's remainder).
        """
        turns = action.get("turns")
        overrides = {"turns": int(turns)} if isinstance(turns, int) and turns > 0 else None
        sub = self._start_child(action, mode=child.SEQUENTIAL, prefix="task",
                                overrides=overrides)
        if isinstance(sub, str):
            return {"kind": "child", "mode": child.SEQUENTIAL, "rejected": True,
                    "label": action.get("label") or "", "reason": sub}
        # `note` says when the child fell back to the builtin pattern
        return {"kind": "child", "mode": child.SEQUENTIAL, "n": sub.n, "label": sub.label,
                "workflow": sub.workflow, "note": sub.note, "started": True}

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
            # The hand-back happens HERE, the single finalization point: two paths report an
            # exit (`wait` and the turn boundary), and collecting in either raced. Without a
            # parent_dir only the summary is handed back.
            if sub.parent_dir is not None:
                sub.collected_paths = child.collect_handback(
                    sub.ctx.routine.dir / "artifacts", sub.parent_dir, child.SUB, sub.n)
            # kill_all may collect a child still running — snapshot so fold and event agree
            usage = _usage_snapshot(sub.ctx.usage)
            self.parent.ctx.add_usage(usage)
            self.parent.ctx.referrals += sub.ctx.referrals
            # `collected` rides the event so a resumed leg's replay names the same hand-back
            self.parent.ctx.transcript.event("child_end", {
                "n": sub.n, "label": sub.label, "workflow": sub.workflow, "mode": sub.mode,
                "status": sub.status, "summary": sub.summary,
                "turns": sub.ctx.turn, "usage": usage,
                **({"collected": list(sub.collected_paths)} if sub.collected_paths else {})})
            # children feed workflow-library optimization like any other run
            from ..usage_log import log_workflow_usage

            pctx = self.parent.ctx
            log_workflow_usage(pctx.server.routines_home, routine=pctx.routine.slug,
                               run_id=f"{pctx.run_id}#sub{sub.n}", workflow=sub.workflow,
                               depth=sub.ctx.depth, status=sub.status or "unknown",
                               turns=sub.ctx.turn, usage=usage,
                               referrals=sub.ctx.referrals,
                               # util counts are the child's OWN: Stats sums every depth,
                               # so a parent fold would double count
                               recipe_commit=pctx.recipe_commit, utils=sub.ctx.util_stats,
                               asks_deferred=sub.ctx.asks_deferred,
                               compression=sub.ctx.compression_stats)

    def status_table(self) -> dict:
        # backgrounded calls too (same shape to the reader), under their own key so `count`
        # keeps meaning children
        from . import background_status

        rows = subsalvage.rows(self.subruns, self.parent.ctx.run_dir)
        return {"kind": "children", "count": len(rows), "rows": rows,
                "background": background_status.status_rows(self.parent)}

    def kill(self, n: int) -> dict:
        sub = self.subruns.get(int(n))
        if sub is None:
            return {"kind": "kill", "n": n,
                    "error": subsalvage.no_such_child(self.parent.ctx.run_dir, int(n))}
        if sub.done.is_set():
            return {"kind": "kill", "n": n, "already_finished": True, "status": sub.status}
        sub.abort_event.set("the parent run killed this child-task")   # F751: the actor
        sub.done.wait(timeout=KILL_JOIN_S)
        return {"kind": "kill", "n": n, "killed": True,
                "status": sub.status if sub.done.is_set() else "stopping"}

    def wait(self, action: dict, *, poll_s: float, aborted) -> dict:
        """Block until a child exits or a backgrounded call lands — `engine/waitop.py`."""
        from . import waitop

        return waitop.wait(self, action, poll_s=poll_s, aborted=aborted)

    @staticmethod
    def finished_rows(finished: list) -> list[dict]:
        return [{"n": s.n, "label": s.label, "status": s.status, "turns": s.ctx.turn,
                 "mode": s.mode, "summary": truncate(s.summary, cap=3000)[0],
                 **({"collected": list(s.collected_paths)} if s.collected_paths else {})}
                for s in finished]

    def kill_all(self, *, reason: str) -> int:
        """Parent is exiting — children never outlive it. All are told to stop at once and
        share ONE `KILL_JOIN_S` (a model call ignores abort; per-child waits would multiply it).
        """
        running = [sub for sub in self.subruns.values() if not sub.done.is_set()]
        for sub in running:
            sub.abort_event.set(reason)   # F751: the child's close-out names why it stopped
        deadline = time.monotonic() + KILL_JOIN_S
        for sub in self.subruns.values():
            sub.done.wait(timeout=max(0.0, deadline - time.monotonic()))
            if not sub.announced:
                sub.announced = True
                if not sub.done.is_set():
                    sub.status = "aborted"
                    sub.summary = f"killed: {reason} (did not stop in time)"
                self._collect(sub)
        return len(running)
