"""The child-task executor — materialize a child routine from a workflow pattern and wire its
EngineLoop, shared by BOTH schedulers: parallel `spawn` (a subroutine) and sequential blocking
`subtask`. A subtask and a subroutine are the SAME thing — a child task run recursively from a
pattern — differing only in how the parent schedules it and how its budget is sliced.

Each child is a REAL routine on disk under `runs/<ts>/sub/<n>/` while it runs (its own main.md +
stages/ + instruction), so its module reads resolve under its own dir and it can itself
decompose (the tree is recursive; `sub_counter` is shared tree-wide so every node's `n` is
unique). Lifecycle (start/monitor/announce/kill) stays in `SubrunManager` (subruns.py), which
owns the exit-event machinery; this module only builds a child, not its thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .run_context import RunContext
from .transcript import Transcript

if TYPE_CHECKING:
    from .loop import EngineLoop

# Fallback body when the library (or the requested recipe) is unavailable — keeps child tasks
# functional on library-less installs and in tests.
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
    """One spawned child: its own RunContext + EngineLoop running in a thread, tracked until
    its exit is announced to the parent and its usage folded in. `mode` distinguishes a
    parallel subroutine (the parent keeps working) from a sequential subtask (the parent
    blocks on it).
    """

    n: int
    label: str
    workflow: str
    ctx: RunContext
    loop: EngineLoop                   # the child EngineLoop
    abort_event: threading.Event
    started_mono: float
    mode: str = "parallel"             # parallel (spawn) | sequential (subtask)
    note: str = ""                     # e.g. "recipe X unavailable — builtin fallback"
    thread: threading.Thread | None = None   # attached by SubrunManager just before start
    status: str = "running"            # running | ok | partial | failed | aborted
    summary: str = ""
    announced: bool = False            # parent notified of exit?
    collected: bool = False            # usage folded into the parent?
    done: threading.Event = field(default_factory=threading.Event)


def materialize_to_disk(server, slug: str, sub_dir, prompt: str) -> tuple[str, str]:
    """Write the child's files into sub_dir (main.md + instruction.md) so it is a real on-disk
    routine while it runs. Returns (effective slug, note). Permissions stay OFF — a child
    reports through its finish summary; it keeps no LEDGER/audit of its own.
    """
    from ..paths import atomic_write
    try:
        from ..workflows.adapt import materialize

        # a child is not decomposed (no per-spawn LLM) — the whole workflow is its main.md
        main_content, _ = materialize(server.library_home, slug)
        atomic_write(sub_dir / "main.md", main_content)
        atomic_write(sub_dir / "instruction.md", prompt)
        return slug, ""
    except Exception as exc:  # missing library/recipe/params → degrade, don't fail
        atomic_write(sub_dir / "main.md",
                     "---\nname: Fallback\nslug: fallback\n"
                     "materialized_from: {slug: fallback, commit: '', version: 0}\n---\n\n"
                     + FALLBACK_SUB_BODY + "\n")
        atomic_write(sub_dir / "instruction.md", prompt)
        return "(builtin-fallback)", f"recipe {slug!r} unavailable ({exc}) — builtin fallback"


def build_child(parent_ctx: RunContext, action: dict, *, mode: str,
                default_label: str, alloc_overrides: dict | None = None,
                emit) -> Subrun:
    """Materialize + wire ONE child task (not started). `mode` selects the scheduler and how the
    budget is sliced (`alloc_overrides` pins e.g. a subtask's explicit `turns` cap; otherwise
    the child gets half the parent's remainder). `emit` records the `subrun_start` event on the
    PARENT transcript (single writer). The caller starts + tracks the returned Subrun.
    """
    label = action.get("label") or default_label
    recipe_slug = action.get("workflow") or "general-task"

    with parent_ctx.sub_lock:   # tree-wide counter; parallel spawns race without it
        parent_ctx.sub_counter[0] += 1
        n = parent_ctx.sub_counter[0]
    sub_dir = parent_ctx.run_dir / "sub" / str(n)
    sub_dir.mkdir(parents=True, exist_ok=True)
    recipe_slug, note = materialize_to_disk(parent_ctx.server, recipe_slug, sub_dir,
                                            action["prompt"])
    transcript = Transcript(sub_dir / "transcript.jsonl")
    _, sub_ref = parent_ctx.registry.for_model("subroutine", parent_ctx.routine.models)
    child_budgets = parent_ctx.child_budgets(overrides=alloc_overrides)
    child_ctx = RunContext(
        routine=_sub_routine(parent_ctx.routine, sub_dir, sub_ref,
                             deliberation=parent_ctx.deliberation),
        server=parent_ctx.server, registry=parent_ctx.registry, run_ts=parent_ctx.run_ts,
        run_dir=sub_dir, transcript=transcript, budgets=child_budgets,
        depth=parent_ctx.depth + 1, parent_run_id=parent_ctx.run_id,
        sub_counter=parent_ctx.sub_counter, sub_lock=parent_ctx.sub_lock,
    )
    transcript.header(run_id=f"{parent_ctx.run_id}#sub{n}", routine=parent_ctx.routine.slug,
                      workflow={"slug": recipe_slug, "commit": "", "version": 0},
                      orchestrator={"endpoint": sub_ref.endpoint, "model": sub_ref.model},
                      depth=parent_ctx.depth + 1, parent=parent_ctx.run_id)
    from .loop import EngineLoop  # local import: loop imports this module
    from .runtime import load_workflow

    body, _prov, tools = load_workflow(sub_dir, child_ctx.routine)
    abort_event = threading.Event()
    # The child's workflow `tools:` allowlist binds the child exactly as a top-level run's
    # binds it — materialize carries the pattern's frontmatter through, load_workflow reads
    # it, and dropping it here would let any child use every kind.
    child_loop = EngineLoop(child_ctx, body, action["prompt"], abort_event=abort_event,
                            allowed_tools=tools)
    sub = Subrun(n=n, label=label, workflow=recipe_slug, mode=mode, note=note,
                 ctx=child_ctx, loop=child_loop, abort_event=abort_event,
                 started_mono=time.monotonic())
    # subrun_start carries `mode` (the tree read-model distinguishes sequential/parallel) and
    # the child's allocated budget (the per-node meter) — both are payload EXTENSIONS, so every
    # existing consumer keeps working; a new event type would have broken them.
    emit("subrun_start", {"n": n, "label": label, "workflow": recipe_slug, "mode": mode,
                          "depth": parent_ctx.depth + 1,
                          "budget": {"turns": child_budgets.max_turns,
                                     "tokens": child_budgets.max_total_tokens},
                          "transcript": f"sub/{n}/transcript.jsonl"})
    return sub


def _sub_routine(routine, sub_dir, ref, *, deliberation: str = ""):
    """A child's config: its OWN dir (so main.md + read_file/write_file resolve under sub_dir),
    the parent's fs roots inherited, the parent's SUBROUTINE model as the child's MAIN model
    (subroutine/tool_call inherited so the child can spawn/subtask/llm too), permissions and
    capabilities off (a child holds nothing gated: it reports through its finish summary and
    keeps no LEDGER/audit). The parent's LIVE deliberation level carries over (a mid-run
    switch reaches children spawned after it).
    """
    import copy

    r = copy.copy(routine)
    r.dir = sub_dir
    r.models = dict(routine.models)   # role → catalog NAME (subroutine/tool_call inherited)
    r.models["main"] = ref.name       # resolved subroutine catalog name → the child's main
    r.permissions = []
    r.capabilities = {}
    if deliberation:
        r.deliberation = deliberation
    return r
