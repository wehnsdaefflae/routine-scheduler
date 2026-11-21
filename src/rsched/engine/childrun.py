"""The CHILD RUN executor — materialize a child routine from a workflow pattern and wire its
EngineLoop. One builder for every in-engine scheduling MODE: parallel and sequential, the
`mode` of one `child` action. They are the same thing — a child run, as `engine/child.py`
defines it — differing only in how the parent schedules it and how its budget is sliced.

Each child is a REAL routine on disk under `runs/<ts>/sub/<n>/` while it runs, so its reads
resolve under its own dir and it can itself decompose. `sub_counter` COUNTS the children
admitted this leg against `max_child_runs`; `n` NAMES one, unique across every leg
(`claim_child`). Lifecycle (start/monitor/announce/kill) stays in `SubrunManager`
(subruns.py); this module only builds a child, not its thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .. import abortmark, entities
from ..config.base import supervisor_role
from . import child
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


def inheritable_resources(granted_now: set[str], granted_once: set[str]) -> set[str]:
    """The parent grants a child run inherits: RESOURCE entities only (sub-workflows run
    with capabilities off; entities.is_resource is that line) — and never a once-armed
    one (D76): "one action" must not become a child's whole-run grant.

    REACH IS NOT AUTHORITY (F589): a child REACHES what the routine reaches (its `scripts/`,
    roots, plain utils) but holds NO reserved capability — no `shell`, reserved util or verb,
    authoring permission. Brief a child in terms of reach; keep reserved acts in the parent.
    """
    return {e for e in granted_now
            if entities.is_resource(e) and e not in granted_once}


@dataclass
class Subrun:
    """One in-engine CHILD RUN: its own RunContext + EngineLoop running in a thread, tracked
    until its exit is announced to the parent and its usage folded in. `mode` (child.PARALLEL
    or child.SEQUENTIAL) says only how the parent SCHEDULES it — what it is, and what it owes
    its parent on exit, is the same either way (engine/child.py).
    """

    n: int
    label: str
    workflow: str
    ctx: RunContext
    loop: EngineLoop                   # the child EngineLoop
    abort_event: abortmark.AbortEvent    # set with the REASON the parent stopped it (F751)
    started_mono: float
    mode: str = child.PARALLEL         # a child.MODE_NOUN key — the scheduling mode only
    parent_dir: Path | None = None     # where the hand-back lands (child.handback_dirname)
    collected_paths: tuple = ()        # what actually landed there, parent-relative
    note: str = ""                     # e.g. "recipe X unavailable — builtin fallback"
    status: str = "running"            # running | ok | partial | failed | aborted
    summary: str = ""
    ended_mono: float | None = None    # when its thread finished — freezes `elapsed_s`
    announced: bool = False            # parent notified of exit?
    announced_start: bool = False      # the `spawn` hook point fired for it? (one boundary only)
    collected: bool = False            # usage folded into the parent?
    done: threading.Event = field(default_factory=threading.Event)


def _highest_child_number(run_dir: Path) -> int:
    """The highest child number any leg of this run has used, at any depth; 0 when none.

    The child directories ARE the record — `sub/<n>/` for the run's own children,
    `sub/<n>/sub/<m>/` for theirs — and they outlive the leg that made them. Only numbered
    directories are walked, so the cost is the size of the child tree, not of its files.
    """
    top, pending = 0, [run_dir / "sub"]
    while pending:
        here = pending.pop()
        try:
            numbered = [p for p in here.iterdir() if p.name.isdecimal()]
        except OSError:          # no `sub/` here (yet) — nothing numbered below it
            continue
        for p in numbered:
            top = max(top, int(p.name))
            pending.append(p / "sub")
    return top


def claim_child(parent_ctx: RunContext) -> int:
    """Count one more child against the tree's allowance and claim its NUMBER and directory.
    The caller holds `parent_ctx.sub_lock` and has checked the allowance under it.

    The number is one past the highest any child of this RUN has ever used — never the
    admission counter, which restarts at zero on every resumed leg while earlier legs' child
    directories remain on disk (a reused number would share a directory and transcript).
    """
    parent_ctx.sub_counter[0] += 1
    n = _highest_child_number(parent_ctx.root_run_dir) + 1
    while True:
        try:
            (parent_ctx.run_dir / "sub" / str(n)).mkdir(parents=True)
        except FileExistsError:  # unreachable after the scan — but never share a directory
            n += 1
            continue
        return n


def materialize_to_disk(server, slug: str, sub_dir, prompt: str) -> tuple[str, str]:
    """Write the child's files into sub_dir (main.md + instruction.md) so it is a real on-disk
    routine while it runs. Returns (effective slug, note). Permissions stay OFF — a child
    reports through its finish summary; it keeps no LEDGER/audit of its own.
    """
    from ..paths import atomic_write
    try:
        from ..workflows.adapt import materialize

        # a child is not decomposed (no per-spawn LLM) — the whole workflow is its main.md
        main_content, _ = materialize(server.libraries_home, slug)
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


def build_child(parent_ctx: RunContext, action: dict, *, n: int, label: str, mode: str,
                alloc_overrides: dict | None = None, emit) -> Subrun:
    """Materialize + wire ONE child task (not started) into the directory `claim_child` made
    for number `n`. `mode` selects the scheduler and how the budget is sliced
    (`alloc_overrides` pins e.g. a subtask's explicit `turns` cap; otherwise the child gets
    half the parent's remainder). `emit` records the `subrun_start` event on the PARENT
    transcript (single writer). The caller starts + tracks the returned Subrun.
    """
    recipe_slug = action.get("workflow") or "general-task"
    sub_dir = parent_ctx.run_dir / "sub" / str(n)
    recipe_slug, note = materialize_to_disk(parent_ctx.server, recipe_slug, sub_dir,
                                            action["prompt"])
    transcript = Transcript(sub_dir / "transcript.jsonl")
    # The optional `model` override: a ROLE (main/tool_call/uncensored/supervisor) or a
    # CATALOG NAME. Default MAIN — a child continues the routine's work. The subruns caller
    # pre-validates the override, so it never crashes the build here.
    sel = str(action.get("model") or "main")
    if sel == "uncensored":
        target = parent_ctx.registry.for_uncensored(parent_ctx.routine.models,
                                                    parent_ctx.routine.efforts)
        _, sub_ref = target if target is not None else parent_ctx.registry.for_model(
            "main", parent_ctx.routine.models, parent_ctx.routine.efforts)
    elif sel in ("main", "tool_call", "supervisor"):
        # `supervisor` (D164) resolves through MAIN while none is configured.
        _, sub_ref = parent_ctx.registry.for_model(
            supervisor_role(parent_ctx.routine.models) if sel == "supervisor" else sel,
            parent_ctx.routine.models, parent_ctx.routine.efforts)
    else:
        _, sub_ref = parent_ctx.registry.for_name(sel)
    child_budgets = parent_ctx.child_budgets(overrides=alloc_overrides)
    child_ctx = RunContext(
        routine=_sub_routine(parent_ctx.routine, sub_dir, sub_ref,
                             deliberation=parent_ctx.deliberation),
        server=parent_ctx.server, registry=parent_ctx.registry, run_ts=parent_ctx.run_ts,
        run_dir=sub_dir, transcript=transcript, budgets=child_budgets,
        depth=parent_ctx.depth + 1, sub_n=n, sub_label=label,
        sub_counter=parent_ctx.sub_counter, sub_lock=parent_ctx.sub_lock,
        # Children inherit their parent's RESOURCES (see inheritable_resources); a shared
        # store is an ordinary fs write root, so it flows down too.
        granted_now=inheritable_resources(parent_ctx.granted_now,
                                          parent_ctx.granted_once),
        grant_args={e: v for e, v in parent_ctx.grant_args.items()
                    if entities.is_resource(e)},
        # Share mounts are provisioned once for the whole run tree; a child inherits which
        # ones are live along with the roots that reach them (R514).
        mounted_shares=set(parent_ctx.mounted_shares),
        unavailable_shares=dict(parent_ctx.unavailable_shares),
    )
    transcript.header(run_id=f"{parent_ctx.run_id}#sub{n}", routine=parent_ctx.routine.slug,
                      workflow={"slug": recipe_slug, "commit": "", "version": 0},
                      orchestrator={"endpoint": sub_ref.endpoint, "model": sub_ref.model},
                      depth=parent_ctx.depth + 1, parent=parent_ctx.run_id)
    from .loop import EngineLoop  # local import: loop imports this module
    from .runtime import load_workflow

    body, _prov, tools = load_workflow(sub_dir, child_ctx.routine)
    # Carries WHY the parent set it (F751), so the child's close-out can name the actor.
    abort_event = abortmark.AbortEvent()
    # The workflow's `tools:` allowlist binds the child exactly as it binds a top-level run.
    child_loop = EngineLoop(child_ctx, body, action["prompt"], abort_event=abort_event,
                            allowed_tools=tools)
    sub = Subrun(n=n, label=label, workflow=recipe_slug, mode=mode, note=note,
                 ctx=child_ctx, loop=child_loop, abort_event=abort_event,
                 started_mono=time.monotonic(), parent_dir=parent_ctx.routine.dir)
    # `mode` and the allocated budget are payload EXTENSIONS of subrun_start, never a new
    # event type, so every existing consumer keeps working.
    emit("subrun_start", {"n": n, "label": label, "workflow": recipe_slug, "mode": mode,
                          "depth": parent_ctx.depth + 1,
                          "budget": {"turns": child_budgets.max_turns,
                                     "tokens": child_budgets.max_total_tokens},
                          "transcript": f"sub/{n}/transcript.jsonl"})
    return sub


def _sub_routine(routine, sub_dir, ref, *, deliberation: str = ""):
    """A child's config: its OWN dir, the parent's fs roots inherited, the RESOLVED child
    model as its main (other roles inherited), permissions and capabilities off (a child
    holds nothing gated), and the parent's LIVE deliberation level.
    """
    import copy

    r = copy.copy(routine)
    r.dir = sub_dir
    # Moving r.dir to sub/ must not drop the parent dir from the allowed roots (F185).
    # Extend — never replace — so configured extra roots still apply.
    r.fs_read_roots = [*routine.fs_read_roots, routine.dir]
    r.fs_write_roots = [*routine.fs_write_roots, routine.dir]
    r.models = dict(routine.models)   # role → catalog NAME (tool_call/uncensored inherited)
    r.models["main"] = ref.name       # the resolved child model's catalog name → its main
    r.permissions = []
    r.capabilities = {}
    if deliberation:
        r.deliberation = deliberation
    return r
