"""CHILD RUN scheduling: a parent routine runs child routines materialized from workflow
patterns, in either in-engine MODE — PARALLEL (`spawn`, non-blocking) or SEQUENTIAL (`subtask`,
the parent waits). One concept, defined once in `engine/child.py`; both modes are built by the
shared executor (childrun.build_child). This module owns their LIFECYCLE: start each in a
thread, monitor (`subruns`), block or `wait`, `kill`, and announce every exit to the parent at a
turn boundary. Children never outlive the parent: its finish/abort kills them.

Threading model: each child EngineLoop runs in its own thread and writes ONLY its own transcript
under sub/<n>/; all parent-transcript events are emitted from the parent thread (single writer
per file). Children carry a per-loop abort Event so one can be killed without touching siblings.
The one state the whole tree shares — the `max_subruns` allowance and the child numbers — is
decided under the tree's one lock, in one step (`_admit`).
"""

from __future__ import annotations

import threading
import time

from . import child, inbox
from .childrun import Subrun, build_child, claim_child
from .observations import truncate

MAX_PARALLEL = 4
#: How long `kill` and the parent's exit wait for children to stop — ONE deadline for the
#: parent's exit however many are still running, never one per child. A child whose abort is
#: ending a util or script group (`utils_run.run_jailed`) may take up to
#: `procgroup.TERM_GRACE_S` (30 s) — longer, on purpose: after a `kill` it finishes in its own
#: thread; after the parent's exit the backstop `procgroup.terminate` armed delivers the
#: group's SIGKILL.
KILL_JOIN_S = 12.0


def _usage_snapshot(usage: dict) -> dict:
    """Copy a possibly-still-mutating usage dict (a child that ignored its abort long
    enough for kill_all to give up on it). A concurrent key insert can make dict(x)
    raise — retry, then fall back to the guaranteed keys. Spend the straggler adds
    AFTER the snapshot is lost; that is the price of not blocking the parent's exit.
    """
    for _ in range(3):
        try:
            return dict(usage)
        except RuntimeError:
            continue
    return {"in": int(usage.get("in", 0)), "out": int(usage.get("out", 0))}


class SubrunManager:
    """The parent loop's window onto its CHILD RUNS: start one in either mode (`spawn`
    parallel, `subtask` sequential), monitor (`subruns`/`wait`), `kill`, auto-announce exits at
    turn boundaries. Both modes share the budget/depth/parallel caps, the child-run executor
    and the hand-back — they differ only in scheduling (engine/child.py).
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
            # Say that the budget is SHARED (D147-A). It is one cumulative lifetime total for
            # the whole tree — `sub_counter` is shared and never decremented — so a child
            # refused at its own FIRST spawn reads a ceiling its siblings spent. Without this,
            # the refusal is indistinguishable from a fault in the child's own call: the
            # specimen (R1870/F549) is three children numbered 5, 7 and 8, every one refused
            # at its first attempt and every one finishing partial.
            return (f"{noun} budget exhausted — all {ctx.budgets.max_subruns} are spent. That "
                    f"budget is shared across this run tree (every node's children count "
                    f"against one total, and it never refills), so this is not a fault in your "
                    f"call: do the work here instead of decomposing it")
        if ctx.depth + 1 > ctx.budgets.max_subrun_depth:
            return f"max {noun} depth ({ctx.budgets.max_subrun_depth}) reached"
        if (running := self._running()) >= MAX_PARALLEL:
            return (f"{running} child-tasks already running (parallel cap {MAX_PARALLEL}) — "
                    "wait for or kill one first")
        return None

    def _model_reason(self, action: dict) -> str | None:
        """Why the action's `model` override cannot serve a child, or None. The field
        takes a ROLE (main/tool_call; uncensored needs the role configured) or a CATALOG
        model NAME — validated here so a bad override is a teaching rejection naming the
        alternatives, never a crash inside the child build.
        """
        ctx = self.parent.ctx
        sel = str(action.get("model") or "")
        if not sel or sel in ("main", "tool_call"):
            return None
        if sel == "uncensored":
            if ctx.registry.for_uncensored(ctx.routine.models) is None:
                return ("model role 'uncensored' is not configured for this routine — "
                        "it needs a models.uncensored catalog entry (routine page → "
                        "Models). Drop the override, or ask the user to set one.")
            return None
        if sel not in ctx.server.models:
            avail = ", ".join(sorted(ctx.server.models)) or "none configured"
            return (f"model {sel!r} is neither a role (main/tool_call/uncensored) nor a "
                    f"catalog model name. Catalog models: {avail}. The list_models "
                    "action shows each one's endpoint and attributes.")
        return None

    def _admit(self, action: dict) -> int | str:
        """Admit ONE new child — its claimed number — or the reason it cannot start.

        The `model` override is judged first (D81 extended, 2026-08-22): it is the CALL's own
        fault, and the cap refusal tells the run its call was fine. The allowance check and
        the claim then happen in one step under the tree's lock: `sub_counter` is shared by
        every node of the tree and parallel children reach it from their own threads, so an
        allowance read outside the lock that guards the increment let two siblings both take
        the last child.
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
            return {"kind": "spawn", "rejected": True, "label": action.get("label") or "",
                    "reason": sub}
        return {"kind": "spawn", "n": sub.n, "label": sub.label, "workflow": sub.workflow,
                "note": sub.note, "running": running + 1}

    # -- subtask (sequential) -------------------------------------------------------

    def subtask(self, action: dict) -> dict:
        """Start ONE SEQUENTIAL child — NON-BLOCKING, so the conversation stays live while it
        runs. Mechanically a subrun tagged `sequential` + a `turns` budget: it runs in its own
        thread and its finish is delivered by the turn-boundary hook (`announce_finished_subruns`),
        never by monopolizing this turn. The parent keeps sequential order by WAITING for it (a
        responsive `wait n=N`, which yields to user input) before starting the next subtask, and
        folds the announced result into that next brief. `turns` pins its budget (else half the
        parent's remainder).
        """
        turns = action.get("turns")
        overrides = {"turns": int(turns)} if isinstance(turns, int) and turns > 0 else None
        sub = self._start_child(action, mode=child.SEQUENTIAL, prefix="task",
                                overrides=overrides)
        if isinstance(sub, str):
            return {"kind": "subtask", "rejected": True, "label": action.get("label") or "",
                    "reason": sub}
        # `note` says when the requested pattern was unavailable and the child runs on the
        # builtin fallback — the parent has to know its child is not running what it asked for.
        return {"kind": "subtask", "n": sub.n, "label": sub.label, "workflow": sub.workflow,
                "note": sub.note, "started": True}

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
            # The hand-back happens HERE, at the child's single finalization point, because
            # there are two paths that can REPORT an exit — `wait` (which consumes finished
            # children directly) and the turn-boundary announcement — and a child that
            # finished during a wait must hand its files back exactly like one that finished
            # between turns. Collecting in either reporter made it a race (F338).
            from .control import collect_child_artifacts

            sub.collected_paths = collect_child_artifacts(sub)
            # kill_all can collect a child that REFUSED to stop (still running in its
            # thread, still mutating its usage dict) — snapshot defensively so the fold
            # and the event agree, and a concurrent key insert can't blow the copy.
            usage = _usage_snapshot(sub.ctx.usage)
            self.parent.ctx.add_usage(usage)
            self.parent.ctx.referrals += sub.ctx.referrals
            # `collected` rides the event because the announcement a resumed leg rebuilds
            # from it (history.replay_messages) has to name the same hand-back the live one
            # did — a payload EXTENSION, present only when something was handed back.
            self.parent.ctx.transcript.event("subrun_end", {
                "n": sub.n, "label": sub.label, "workflow": sub.workflow, "mode": sub.mode,
                "status": sub.status, "summary": sub.summary,
                "turns": sub.ctx.turn, "usage": usage,
                **({"collected": list(sub.collected_paths)} if sub.collected_paths else {})})
            # children feed workflow-library optimization like any other run
            from ..health_events import log_workflow_usage

            pctx = self.parent.ctx
            log_workflow_usage(pctx.server.routines_home, routine=pctx.routine.slug,
                               run_id=f"{pctx.run_id}#sub{sub.n}", workflow=sub.workflow,
                               depth=sub.ctx.depth, status=sub.status or "unknown",
                               turns=sub.ctx.turn,
                               tokens=int(usage.get("in", 0)) + int(usage.get("out", 0)),
                               cost=float(usage.get("cost") or 0.0),
                               referrals=sub.ctx.referrals,
                               # the child ran under the parent's recipe version; its util
                               # counts are its OWN (parents never fold them — the Stats
                               # read-model sums records at every depth, so folding would
                               # double count)
                               recipe_commit=pctx.recipe_commit, utils=sub.ctx.util_stats,
                               asks_deferred=sub.ctx.asks_deferred,
                               compression=sub.ctx.compression_stats)

    def status_table(self) -> dict:
        now = time.monotonic()
        rows = [{"n": sub.n, "label": sub.label, "workflow": sub.workflow,
                 "mode": sub.mode,
                 "state": sub.status if sub.done.is_set() else "running",
                 "turns": sub.ctx.turn,
                 # a finished child's elapsed is how long it RAN, not how long ago it started
                 "elapsed_s": round((sub.ended_mono or now) - sub.started_mono, 1),
                 "summary_head": (truncate(sub.summary, cap=200)[0]
                                  if sub.done.is_set() else "")}
                for sub in self.subruns.values()]
        return {"kind": "subruns", "count": len(rows), "rows": rows}

    def kill(self, n: int) -> dict:
        sub = self.subruns.get(int(n))
        if sub is None:
            return {"kind": "kill", "n": n,
                    "error": f"no sub-workflow {n} — the `subruns` action lists every child "
                             "this run has spawned, with its number and state"}
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
            return {"kind": "wait", "error":
                    "no such sub-workflow to wait for — the `subruns` action lists every "
                    "child this run has spawned, with its number and state"
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
                    and inbox.has_pending_messages(self.parent.ctx.routine.dir,
                                                   vias=inbox.LIVE_MESSAGE_VIAS)):
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
                 "mode": s.mode, "summary": truncate(s.summary, cap=3000)[0],
                 **({"collected": list(s.collected_paths)} if s.collected_paths else {})}
                for s in finished]

    def kill_all(self, *, reason: str) -> int:
        """Parent is exiting — children never outlive it. Every running child is told to stop
        at once, and they share ONE `KILL_JOIN_S` to do it: a wait per child let four children
        stuck in model calls (which no abort interrupts) hold the parent's finish for four
        times the grace the rest of the system plans around (procgroup's backstop arithmetic).
        """
        running = [sub for sub in self.subruns.values() if not sub.done.is_set()]
        for sub in running:
            sub.abort_event.set()
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
