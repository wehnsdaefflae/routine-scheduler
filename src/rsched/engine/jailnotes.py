"""What the JAIL decided about a call's paths, as the fields its observation carries.

Three jail decisions are invisible from inside the command: an unstat-able granted root is
DROPPED, run dirs beyond the run-history depth are never mounted (D207), and a path OUTSIDE
every mounted root fails with a bare `Permission denied` that names nothing. Without being
told, a run misreads "no such file" or EACCES; so the same `sandbox.SandboxPolicy` is asked
here and its answer rides the call's observation.

The third is why `blocked_paths` exists. Landlock has no deny rule and no diagnostics: a read
it refuses raises EACCES, and the message carries the path but not the reason, so "the jail
does not contain this" is indistinguishable from "the file's permissions forbid it" — and a
run that has just written the file concludes neither, and doubts the path instead. The answer
is cheap and exact: the paths the failed call NAMED, that exist on the host, and that no
mounted root covers.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import rundepth, sandbox

#: An absolute path as a command's own diagnostics print one — quoted, bare, or trailing a
#: colon. Deliberately crude: a candidate is only kept when it exists on disk, so a
#: mis-captured fragment names no file and drops out on its own.
_PATH = re.compile(r"/[^\s'\"<>:,)]+")

#: How many to name. A failing command can print the same path hundreds of times; the first
#: few distinct ones are the diagnosis, and the cap keeps a diagnostic out of the budget.
MAX_BLOCKED = 4

BLOCKED_NOTE = (
    "these paths exist on the host but are OUTSIDE this call's filesystem jail, so the kernel "
    "refused them — the error you see is a jail denial, not a missing or mis-typed file. A "
    "util, script or shell command sees only the routine's own dir, its unsealed children and "
    "the fs roots this routine was granted. Ask for the root with a deferred ask_user, or work "
    "on a copy inside your working directory.")

DEAD_ROOTS_NOTE = (
    "the kernel cannot stat these granted fs roots (a dead or flaky mount). A READ root is "
    "dropped from this call's jail so the rest of your tools keep working — a util needing it "
    "fails on its own merits; a WRITE root refuses the call instead, because a write under it "
    "would land nowhere. Repair the mount or ask for the root to be removed from this "
    "routine's config.")


def blocked_paths(policy, output: str) -> list[str]:
    """The absolute paths `output` names that EXIST on the host and no mounted root covers.

    Asked only of a FAILED call, and only of what the command itself printed: a path it never
    mentioned is not what it tripped on, and guessing would turn a diagnostic into noise.
    Existence is what separates a jail denial from a typo — a path that is not there is a
    missing file whatever the jail says, and saying "outside the jail" about it would send the
    reader after the wrong repair.

    Total: any trouble composing the jail's root list answers `[]`, because a diagnostic may
    never be the reason a call's observation fails to render.
    """
    try:
        roots = [Path(p).resolve() for p in sandbox.mounted_roots(policy)]
    except Exception:
        return []
    out: list[str] = []
    for raw in dict.fromkeys(_PATH.findall(str(output or ""))):
        if len(out) >= MAX_BLOCKED:
            break
        try:
            path = Path(raw)
            if not path.exists():
                continue                      # a missing file is a missing file, not a jail
            resolved = path.resolve()
            if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
                out.append(str(path))
        except (OSError, ValueError):
            continue
    return out


def jail_notes(policy, out: str = "", err: str = "", code: int = 0) -> dict:
    """The observation fields for one jailed call — `{}` when the jail withheld nothing.
    `run_depth` names the refused run dirs and the depth; `dead_fs_roots` the unstat-able
    roots; `blocked_paths` the paths a FAILED call named that the jail does not contain.

    The call's own streams and exit code go in, not a prepared string: whether a call FAILED
    is the condition for asking at all, so it is decided here rather than at each of the three
    call sites — one of which would otherwise be the one that forgets, and a successful call
    naming an unreachable path in passing is not a diagnosis of anything.
    """
    notes = rundepth.note(policy.run_depth, policy.read_roots)
    if dead := sandbox.unreadable_roots(policy):
        notes["dead_fs_roots"] = {"paths": list(dead), "note": DEAD_ROOTS_NOTE}
    if code and (blocked := blocked_paths(policy, f"{out}\n{err}")):
        notes["blocked_paths"] = {"paths": blocked, "note": BLOCKED_NOTE}
    return notes
