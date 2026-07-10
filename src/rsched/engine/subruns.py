"""Parallel sub-routines: a parent routine spawns child routines that run concurrently.

Each sub-routine is a REAL routine materialized on disk while it runs — its own main.md + steps/
+ instruction under runs/<ts>/sub/<n>/ — so its module reads resolve under its own dir. The parent
controls the lifecycle: `spawn` starts a child (non-blocking), `subruns` monitors, `kill`
terminates, `wait` blocks for completion — and every child that exits is announced to the parent at
the next turn boundary. Children never outlive the parent: its finish/abort kills them.

Threading model: each child EngineLoop runs in its own thread and writes ONLY its own transcript
under sub/<n>/; all parent-transcript events are emitted from the parent thread (single writer per
file). Children carry a per-loop abort Event so one can be killed without touching its siblings.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .composer import truncate
from .run_context import RunContext
from .transcript import Transcript

MAX_PARALLEL = 4
KILL_JOIN_S = 12.0

# Fallback body when the library (or the requested recipe) is unavailable — keeps
# sub-routines functional on library-less installs and in tests.
FALLBACK_SUB_BODY = """## Run flow
1. Read your instruction carefully; orient with the cheapest possible looks.
2. Do the work it describes, step by step. Prefer `gu` utils; verify what you produce.
3. If something the instruction assumes is missing or broken, say so in your summary.
## Phases
- **only** — single phase.
## Completion criteria
- Finish as soon as the instruction is fulfilled (status ok) or precisely blocked
  (partial/failed). Your finish summary is the ONLY thing the parent sees — pack the
  result, key facts, and file paths into it."""


@dataclass
class Subrun:
    n: int
    label: str
    workflow: str
    thread: threading.Thread
    ctx: RunContext
    loop: object                       # the child EngineLoop
    abort_event: threading.Event
    started_mono: float
    status: str = "running"            # running | ok | partial | failed | aborted
    summary: str = ""
    announced: bool = False            # parent notified of exit?
    collected: bool = False            # usage folded into the parent?
    done: threading.Event = field(default_factory=threading.Event)


class SubrunManager:
    def __init__(self, parent_loop):
        self.parent = parent_loop
        self.subruns: dict[int, Subrun] = {}

    # -- spawn ----------------------------------------------------------------------

    def spawn(self, action: dict) -> dict:
        ctx: RunContext = self.parent.ctx
        label = action.get("label") or f"sub-{ctx.sub_counter[0] + 1}"
        recipe_slug = action.get("workflow") or "general-task"
        if ctx.sub_counter[0] >= ctx.budgets.max_subruns:
            return {"kind": "spawn", "rejected": True, "label": label,
                    "reason": f"sub-routine budget ({ctx.budgets.max_subruns}) exhausted"}
        if ctx.depth + 1 > ctx.budgets.max_subrun_depth:
            return {"kind": "spawn", "rejected": True, "label": label,
                    "reason": f"max sub-routine depth ({ctx.budgets.max_subrun_depth}) reached"}
        running = sum(1 for s in self.subruns.values() if s.status == "running")
        if running >= MAX_PARALLEL:
            return {"kind": "spawn", "rejected": True, "label": label,
                    "reason": f"{running} sub-routines already running (parallel cap "
                              f"{MAX_PARALLEL}) — wait for or kill one first"}

        ctx.sub_counter[0] += 1
        n = ctx.sub_counter[0]
        sub_dir = ctx.run_dir / "sub" / str(n)
        sub_dir.mkdir(parents=True, exist_ok=True)
        # materialize the recipe into the sub-routine ON DISK — a real routine (main.md + steps/ +
        # instruction) that exists while it runs, so its module reads resolve under its own dir.
        recipe_slug, note = self._materialize_to_disk(recipe_slug, sub_dir, action["prompt"])
        transcript = Transcript(sub_dir / "transcript.jsonl")
        _, sub_ref = ctx.registry.for_model("subroutine", ctx.routine.models)
        child_ctx = RunContext(
            routine=_sub_routine(ctx.routine, sub_dir, sub_ref),
            server=ctx.server, registry=ctx.registry, run_ts=ctx.run_ts,
            run_dir=sub_dir, transcript=transcript, budgets=ctx.child_budgets(),
            depth=ctx.depth + 1, parent_run_id=ctx.run_id, sub_counter=ctx.sub_counter,
        )
        transcript.header(run_id=f"{ctx.run_id}#sub{n}", routine=ctx.routine.slug,
                          workflow={"slug": recipe_slug, "commit": "", "version": 0},
                          orchestrator={"endpoint": sub_ref.endpoint, "model": sub_ref.model},
                          depth=ctx.depth + 1, parent=ctx.run_id)
        from .loop import EngineLoop, load_workflow  # local import: loop imports this module

        body, _frag, _prov, _tools = load_workflow(sub_dir, child_ctx.routine, ctx.server)
        abort_event = threading.Event()
        child_loop = EngineLoop(child_ctx, body, action["prompt"], abort_event=abort_event)
        sub = Subrun(n=n, label=label, workflow=recipe_slug, thread=None,  # type: ignore[arg-type]
                     ctx=child_ctx, loop=child_loop, abort_event=abort_event,
                     started_mono=time.monotonic())

        def run_child() -> None:
            try:
                sub.status = child_loop.run()
                sub.summary = child_loop.final_summary
            except Exception as exc:  # noqa: BLE001 — a child crash must never kill the parent
                sub.status = "failed"
                sub.summary = f"sub-routine crashed: {exc}"
            finally:
                transcript.close()
                sub.done.set()

        sub.thread = threading.Thread(target=run_child, name=f"subrun-{n}", daemon=True)
        self.subruns[n] = sub
        ctx.transcript.event("subrun_start", {"n": n, "label": label, "workflow": recipe_slug,
                                              "depth": ctx.depth + 1,
                                              "transcript": f"sub/{n}/transcript.jsonl"})
        sub.thread.start()
        return {"kind": "spawn", "n": n, "label": label, "workflow": recipe_slug,
                "note": note, "running": running + 1}

    def _materialize_to_disk(self, slug: str, sub_dir, prompt: str) -> tuple[str, str]:
        """Write the sub-routine's files into sub_dir (main.md + steps/ + instruction.md) so it is
        a real on-disk routine while it runs. Returns (effective slug, note). Fragments stay OFF —
        a sub-routine reports through its finish summary; it keeps no LEDGER/audit of its own."""
        try:
            from ..workflows.adapt import materialize

            # a sub-routine is not decomposed (no per-spawn LLM) — the whole workflow is its main.md
            main_content, _ = materialize(self.parent.ctx.server.library_home, slug)
            (sub_dir / "main.md").write_text(main_content, encoding="utf-8")
            (sub_dir / "instruction.md").write_text(prompt, encoding="utf-8")
            return slug, ""
        except Exception as exc:  # missing library/recipe/params → degrade, don't fail
            (sub_dir / "main.md").write_text(
                "---\nname: Fallback\nslug: fallback\nstatus: stable\n"
                "materialized_from: {slug: fallback, commit: '', version: 0}\n---\n\n"
                + FALLBACK_SUB_BODY + "\n", encoding="utf-8")
            (sub_dir / "instruction.md").write_text(prompt, encoding="utf-8")
            return "(builtin-fallback)", f"recipe {slug!r} unavailable ({exc}) — builtin fallback"

    # -- lifecycle ------------------------------------------------------------------

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
            self.parent.ctx.transcript.event("subrun_end", {
                "n": sub.n, "label": sub.label, "workflow": sub.workflow,
                "status": sub.status, "summary": sub.summary,
                "turns": sub.ctx.turn, "usage": dict(sub.ctx.usage)})

    def status_table(self) -> dict:
        rows = []
        for sub in self.subruns.values():
            rows.append({"n": sub.n, "label": sub.label, "workflow": sub.workflow,
                         "state": sub.status if sub.done.is_set() else "running",
                         "turns": sub.ctx.turn,
                         "elapsed_s": round(time.monotonic() - sub.started_mono, 1),
                         "summary_head": truncate(sub.summary, cap=200)[0] if sub.done.is_set() else ""})
        return {"kind": "subruns", "count": len(rows), "rows": rows}

    def kill(self, n: int) -> dict:
        sub = self.subruns.get(int(n))
        if sub is None:
            return {"kind": "kill", "n": n, "error": f"no sub-routine {n}"}
        if sub.done.is_set():
            return {"kind": "kill", "n": n, "already_finished": True, "status": sub.status}
        sub.abort_event.set()
        sub.done.wait(timeout=KILL_JOIN_S)
        return {"kind": "kill", "n": n, "killed": True,
                "status": sub.status if sub.done.is_set() else "stopping"}

    def wait(self, action: dict, *, poll_s: float, aborted) -> dict:
        """Block until a target child (n), all children, or any child finishes."""
        n = action.get("n")
        want_all = bool(action.get("all"))
        timeout = float(action.get("timeout_s") or 600)
        deadline = time.monotonic() + timeout
        already_done = {k for k, s in self.subruns.items() if s.done.is_set()}
        if not self.subruns or (n is not None and int(n) not in self.subruns):
            return {"kind": "wait", "error": "no such sub-routine to wait for"
                    if n is not None else "no sub-routines have been spawned"}

        def satisfied() -> bool:
            if n is not None:
                return self.subruns[int(n)].done.is_set()
            if want_all:
                return all(s.done.is_set() for s in self.subruns.values())
            return any(k for k, s in self.subruns.items()
                       if s.done.is_set() and k not in already_done)

        while not satisfied() and time.monotonic() < deadline:
            if aborted():
                break
            time.sleep(poll_s)
        finished = self.take_finished_unannounced()
        return {"kind": "wait", "timed_out": not satisfied(),
                "finished": [{"n": s.n, "label": s.label, "status": s.status,
                              "turns": s.ctx.turn,
                              "summary": truncate(s.summary, cap=3000)[0]} for s in finished],
                "still_running": [s.n for s in self.subruns.values() if not s.done.is_set()]}

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


def _sub_routine(routine, sub_dir, ref):
    """A child sub-routine config: its OWN dir (so main.md + read_file/write_file resolve under
    sub_dir), the parent's fs roots inherited, the parent's SUBROUTINE model as the child's MAIN
    model (subroutine/tool_call inherited so the child can spawn/llm too), fragments off (a
    sub-routine reports through its finish summary and keeps no LEDGER/audit)."""
    import copy

    r = copy.copy(routine)
    r.dir = sub_dir
    r.models = dict(routine.models)
    r.models["main"] = ref
    r.fragments = []
    return r
