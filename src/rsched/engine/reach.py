"""Which routine dirs a run can REACH — its own, and for a child run every dir up its chain.

A child run's own dir is a workspace under `runs/<ts>/sub/<n>/`, but its roots extend the
parent's tree (F185), so anything anchored on `ctx.routine.dir` alone is anchored on the
workspace: the file seals sealed nothing a child could actually write until they asked here
(`fileops`), and a child could not find the routine's own `scripts/` (`executor`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .run_context import RunContext


def _is_workspace(d: Path) -> bool:
    """Is `d` a child run's workspace (`…/sub/<n>`)? The test `RunContext.root_run_dir` walks by."""
    return d.name.isdigit() and d.parent.name == "sub"


def routine_dirs(ctx: RunContext) -> list[Path]:
    """Every routine dir this run's file actions can reach, RESOLVED, own dir first: for a
    top-level run its own dir alone; for a CHILD its workspace, each ancestor child's
    workspace (the roots chain down to grandchildren, F185), and last the ROUTINE's own dir.
    Each holds engine- and operator-owned paths a run must leave alone.
    """
    own = ctx.routine.dir.resolve()
    if ctx.depth == 0:
        return [own]
    dirs = [own]
    d = ctx.run_dir.resolve()
    while _is_workspace(d):
        d = d.parent.parent                  # the parent's run dir: a workspace, or the root's
        dirs.append(d if _is_workspace(d) else d.parent.parent)
    return dirs


def routine_root(ctx: RunContext) -> Path:
    """The ROUTINE a run belongs to: its own dir for a top-level run, and for a child the
    routine at the end of its workspace chain.
    """
    return ctx.routine.dir if ctx.depth == 0 else routine_dirs(ctx)[-1]
