"""What the JAIL decided about a call's paths, as the fields its observation carries.

A jailed call (`util`, `script`, `shell`) is composed from a `sandbox.SandboxPolicy`, and two
of the decisions taken while composing it are invisible from inside the command: a granted fs
root the kernel cannot stat is DROPPED (R2260), and the previous run directories the
run-history depth withholds are never mounted (D207). The log carries both to the operator;
neither reaches the RUN, and a run told nothing reasons from a jail it believes it has — it
reads a util's "no such file" as the file being gone, and a kernel EACCES as a broken tool.

So the same policy object the jail was built from is asked here, and what it says rides the
call's own observation. Split out of `executor.py`, which is over the house line budget and
may only shrink (tests/test_file_size.py).
"""

from __future__ import annotations

from .. import rundepth, sandbox

DEAD_ROOTS_NOTE = (
    "the kernel cannot stat these granted fs roots (a dead or flaky mount). A READ root is "
    "dropped from this call's jail so the rest of your tools keep working — a util needing it "
    "fails on its own merits; a WRITE root refuses the call instead, because a write under it "
    "would land nowhere. Repair the mount or ask for the root to be removed from this "
    "routine's config.")


def jail_notes(policy) -> dict:
    """The observation fields for one jailed call — `{}` when the jail withheld nothing, which
    is the normal case and costs one stat per granted root.

    `run_depth` names the run dirs the depth refused and what the depth IS, because a util
    handed a deeper path meets a bare kernel permission error that teaches nothing by itself.
    `dead_fs_roots` names the roots the kernel could not stat.
    """
    notes = rundepth.note(policy.run_depth, policy.read_roots)
    if dead := sandbox.unreadable_roots(policy):
        notes["dead_fs_roots"] = {"paths": list(dead), "note": DEAD_ROOTS_NOTE}
    return notes
