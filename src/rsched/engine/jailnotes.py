"""What the JAIL decided about a call's paths, as the fields its observation carries.

Two jail decisions are invisible from inside the command: an unstat-able granted root is
DROPPED, and run dirs beyond the run-history depth are never mounted (D207). Without being
told, a run misreads "no such file" or EACCES; so the same `sandbox.SandboxPolicy` is asked
here and its answer rides the call's observation.
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
    """The observation fields for one jailed call — `{}` when the jail withheld nothing.
    `run_depth` names the refused run dirs and the depth; `dead_fs_roots` the unstat-able roots.
    """
    notes = rundepth.note(policy.run_depth, policy.read_roots)
    if dead := sandbox.unreadable_roots(policy):
        notes["dead_fs_roots"] = {"paths": list(dead), "note": DEAD_ROOTS_NOTE}
    return notes
