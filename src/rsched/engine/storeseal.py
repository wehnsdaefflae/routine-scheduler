"""The RESOLVED seal matching: which row of `rsched/sealtable.py` a file action's resolved path
falls under, in every routine dir the run can reach (`reach.routine_dirs`).

`engine/fileops._write_gate` and `_memory_gate` compose these; this module answers the per-row
questions the table's flags pose — at or beneath, nested, exact, exempt, by name, lifted — and
the REMOVAL question (`removal_refusal`): what a delete or a move's source would take with it.

**The jobs seal follows the DIAL, not the path** (F752): with the job layer off the `job` action
does not exist, so `state/jobs.json` is the routine's own file. Every other row's condition is
structural, except the recipe's, which the recipe-authoring permission lifts.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .. import sealtable
from .reach import routine_dirs

if TYPE_CHECKING:
    from pathlib import Path

    from .run_context import RunContext

#: What removing a routine dir itself is told — the one removal refusal that is not a row.
OWN_DIR_REFUSAL = ("this would remove a routine's own directory, and its config, recipe, "
                   "memory and run history with it — remove what is inside it instead")


def _keeps_jobs(ctx) -> bool:
    """Does this routine keep jobs — answered so that only POSITIVE knowledge of "off" unseals.

    With no policy nothing says the layer is off, so the seal stays (`_write_gate` applies it
    ABOVE its `grants is None` return). `jobops.enabled` is the wrong question: it is False for
    a policy-less context and a child run too.

    Read through `engine/buildskew.py`, which survives a policy held across a release (F677).
    Imported lazily because the write gate runs on every file action.
    """
    if ctx.grants is None:
        return True
    from . import buildskew

    return buildskew.setting_on(ctx, "jobs")


def _applies(seal: sealtable.Seal, ctx: RunContext) -> bool:
    """Is this row sealed for THIS run? Asked only when the gate reaches the row, so a policy
    held across a release is questioned no further than the old gate questioned it.
    """
    if seal.jobs_dial:
        return _keeps_jobs(ctx)
    if seal.recipe:
        return ctx.grants is not None and not ctx.grants.recipe_unlocked
    return True


def stores(ctx: RunContext) -> dict[str, str]:
    """The sealed paths a removal may not take with it, each with the refusal that says why."""
    return {s.name: s.refusal for s in sealtable.SEALS if s.removal and _applies(s, ctx)}


def within(resolved: Path, sealed: Path) -> bool:
    """Does the action path lie in (or at) `sealed`, taken at its own place or at what it
    resolves to? An action path is resolved — wholly, or up to its final entry for delete and
    move (`fsops._entry`) — so a sealed path that is itself a link is met at its target by one
    and at its own place by the other.
    """
    return resolved.is_relative_to(sealed) or resolved.is_relative_to(sealed.resolve())


def _holds(resolved: Path, name: str) -> bool:
    """Is there a `name` file anywhere in this tree? Removing the tree writes that file too."""
    return (resolved.is_dir() and not resolved.is_symlink()
            and any(name in files for _root, _dirs, files in os.walk(resolved)))


def _hit(seal: sealtable.Seal, resolved: Path, dirs: list[Path], *,
         creates: bool) -> str | None:
    """The path, relative to the routine dir it was met in, when `resolved` falls under `seal`
    — else None.
    """
    if seal.by_name:
        if resolved.name == seal.name or (not creates and _holds(resolved, seal.name)):
            return resolved.name
        return None
    if seal.recipe:
        from ..grantpolicy import is_recipe_path

        for d in dirs:
            if resolved.is_relative_to(d) and is_recipe_path(rel := str(resolved.relative_to(d))):
                return rel
        return None
    workspace = dirs[-2] if len(dirs) > 1 else None     # the top-most child workspace
    for d in dirs:
        at = d / seal.name
        if seal.exact:
            if resolved in (at, at.resolve()):
                return seal.name
            continue
        if within(resolved, at):
            if (seal.child_workspace_exempt and d == dirs[-1] and workspace is not None
                    and resolved.is_relative_to(workspace)):
                continue
            return seal.name
        if seal.nested:
            # …and a JOB's notebook (rsched/jobs.py): an open job's memory actions resolve in
            # its workspace, so every `.memory/` under the routine is the engine's alike
            for base in (d, d.resolve()):
                if resolved.is_relative_to(base) and seal.name in resolved.relative_to(base).parts:
                    return seal.name
    return None


def refusal(ctx: RunContext, resolved: Path, seals, *, creates: bool = True) -> str | None:
    """The refusal of the first row in `seals` that is sealed for this run and holds `resolved`."""
    dirs = routine_dirs(ctx)
    for seal in seals:
        if not _applies(seal, ctx):
            continue
        if (rel := _hit(seal, resolved, dirs, creates=creates)) is not None:
            g = ctx.grants
            route = "" if not seal.recipe or g is None else g.request_route(sealtable.RECIPE_ENTITY)
            return seal.worded(path=rel, route=route)
    return None


def removal_refusal(ctx: RunContext, resolved: Path) -> str | None:
    """What a REMOVAL — delete, a move's source — may not take with it. Every other seal asks
    whether a path lies INSIDE something sealed; a removal also takes everything inside the
    path. A routine dir the run can reach is never removed whole, and neither is a directory
    holding a `removal` row of the table (the finish line, an engine-owned store).
    """
    for d in routine_dirs(ctx):
        if d.is_relative_to(resolved):
            return OWN_DIR_REFUSAL
        for seal in sealtable.SEALS:
            if seal.removal and _applies(seal, ctx) and (at := d / seal.name).exists() \
                    and at.is_relative_to(resolved):
                return f"this would remove {seal.name}: {seal.refusal}"
    return None
