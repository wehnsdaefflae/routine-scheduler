"""The ENGINE-OWNED STORES a generic file action may not write, and why.

Two files under a routine's `state/` are the engine's own records rather than the routine's
workspace: the job store and the obligations store. Each is changed through its own action,
which keeps the run's ledger and the gates that hold its finish in step with the file — a
generic `write_file` could fake a checkpoint, or quietly drop a due job.

Consulted by the write gate and the removal gate (which asks the same of everything a `delete`
would take with it).

**The jobs seal follows the DIAL, not the path** (F752): with the job layer off the `job` action
does not exist, so `state/jobs.json` is the routine's own file.

The obligations seal is unconditional, and so is the PROCESS RECORD (`rsched/runledger.py`):
`LEDGER.md` and `.ledger/` are written by the engine from each finished run's summary. A store
is a file OR a directory — the gates test "at or beneath it".
"""
from __future__ import annotations

from .. import runledger
from ..jobs import JOBS_FILE
from . import obligations

JOBS_REFUSAL = ("state/jobs.json is the engine's job store — change a job with the `job` "
                "action (create / update / checkpoint / delete), which keeps the run's ledger "
                "and the gate that holds your finish in step with it")


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


def stores(ctx) -> dict[str, str]:
    """The engine-owned stores THIS run may not write, each with the refusal that says why."""
    out = {str(obligations.FILE): obligations.REFUSAL, runledger.FILE: runledger.REFUSAL,
           runledger.ARCHIVE_DIR: runledger.ARCHIVE_REFUSAL}
    if _keeps_jobs(ctx):
        out[JOBS_FILE] = JOBS_REFUSAL
    return out
