"""The ENGINE-OWNED STORES a generic file action may not write, and why.

Two files under a routine's `state/` are the engine's own records rather than the routine's
workspace: the job store and the obligations store. Each is changed through its own action,
which keeps the run's ledger and the gates that hold its finish in step with the file — a
generic `write_file` could fake a checkpoint, or quietly drop a due job.

Extracted from `engine/fileops.py` (F752): the seal is consulted from TWO places — the write
gate and the removal gate, which asks the same question of everything a `delete` would take
with it — and `fileops.py` had no room left to make it dial-aware.

**The seal follows the DIAL, not the path** (F752). `state/jobs.json` was sealed for every
routine purely by name, so a routine with the job layer OFF was refused its own file and told
to use the `job` action — which, with the layer off, is projected out of its schema and does
not exist for it. `voice-model-trainer` hit exactly that: no `jobs:` key in its routine.yaml, a
174 KB hand-maintained record of remote training jobs at that path, and a recipe stage built
around writing it (R2787 → F752). Renaming either side would have fixed one routine and left
the collision for the next one; asking the dial fixes all of them at once.

The obligations half is unconditional: no setting turns obligations off, so there is no routine
for which that file is anything but the engine's.
"""
from __future__ import annotations

from ..jobs import JOBS_FILE
from . import obligations

JOBS_REFUSAL = ("state/jobs.json is the engine's job store — change a job with the `job` "
                "action (create / update / checkpoint / delete), which keeps the run's ledger "
                "and the gate that holds your finish in step with it")


def _keeps_jobs(ctx) -> bool:
    """Does this routine keep jobs — answered so that only POSITIVE knowledge of "off" unseals.

    `_jobs_gate` is one of the seals `_write_gate` applies ABOVE its `if grants is None` early
    return, because F698 found the strongest refusals were the ones a policy-less context
    skipped. A dial-aware seal must not undo that: with no policy there is nothing that says
    this routine has the layer off, so the seal stays. (`jobops.enabled` is the wrong question
    here for the same reason — it is False for a policy-less context AND for a child run,
    neither of which is a routine whose dial is off.)

    The setting itself is read through `engine/buildskew.py`: the policy answers first, and when
    a policy held across a release cannot answer that name, the routine's own capabilities can
    (F677 — `jobs_on` raised `AttributeError` in three runs mid-release, and failing open here
    would mean sealing nothing). Imported lazily, as the recipe seal does, because the write
    gate runs on every file action.
    """
    if ctx.grants is None:
        return True
    from . import buildskew

    return buildskew.setting_on(ctx, "jobs")


def stores(ctx) -> dict[str, str]:
    """The engine-owned stores THIS run may not write, each with the refusal that says why."""
    out = {str(obligations.FILE): obligations.REFUSAL}
    if _keeps_jobs(ctx):
        out[JOBS_FILE] = JOBS_REFUSAL
    return out
