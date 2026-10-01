"""Ending a subprocess's PROCESS GROUP the way its members can clean up after.

Two runners start their child as the leader of a group of its own (`start_new_session=True`)
so a deadline can end the whole tree: `libgit.git` and `utils_run.run_jailed`, the one runner
of the util, script and `shell` kinds. Both end a call that outlived its deadline HERE, one
way: SIGTERM to every member, up to `TERM_GRACE_S` for all of them to exit, then SIGKILL for
whatever is left.

SIGTERM comes first because a process cleans up only in its SIGTERM handler. Git deletes its
`index.lock` there and nowhere else: the SIGKILL `subprocess.run` sends at its timeout left an
empty lock that failed every later write in two routine repos on 2026-09-30
(docs/architecture.md, "Git writes"). A util, a script or a `shell` command runs the same git
under its own deadline — the library's `git-sync`, a routine's script that manages a repo of
its own, self-audit's scripts in the scheduler checkout — and `run_jailed` SIGKILLed it at that
deadline just as `subprocess.run` did.

The wait covers the GROUP, not its leader, because the member that needs the time is rarely
the leader. In a util git runs under `uv run` → python → git; the leader exits within
milliseconds of SIGTERM, so a wait for the leader alone SIGKILLs git in the middle of its
handler and leaves the lock the SIGTERM was sent to remove. Measured on the production host on
2026-10-01: the `uv run` leader was gone 0.01 s after SIGTERM; its grandchild's one-second
cleanup never finished and the lock it was removing stayed.

The grace is sized to the disk, not to a program: `/home` is a USB-attached SSD whose bridge
aborts a stalled command at the 30 s SCSI timeout. A process blocked in that I/O runs its
handler only once the I/O returns — ending it sooner means ending it without its cleanup. The
wait ends the moment the group is empty, so a call whose members exit on SIGTERM (nearly every
call) spends none of it. A member that IGNORES SIGTERM costs its caller the whole grace, on a
call that has already spent its deadline.

A member that has exited but not been collected (a zombie) still answers `killpg(pgid, 0)`.
The leader is reaped here; an orphan is reaped by PID 1 — tini in the container
(`init: true`), systemd on the host. Without a reaping init the wait runs the whole grace and
the SIGKILL, a no-op on a zombie, ends it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time

#: How long a process group gets between SIGTERM and SIGKILL (the module docstring says why).
TERM_GRACE_S = 30
#: How often the wait looks whether the group has emptied.
_POLL_S = 0.05


def terminate(proc: subprocess.Popen[str]) -> bool:
    """End the process group `proc` leads: SIGTERM to every member, up to `TERM_GRACE_S` for
    all of them to exit, SIGKILL for whatever is left. The leader is reaped either way.

    True when the group emptied on SIGTERM alone, so every member ran its own cleanup; False
    when SIGKILL ended it. Signalling the group after its leader is reaped is safe: the kernel
    keeps a group's id reserved while any member lives and answers ESRCH once none does.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_S
    while _occupied(proc):
        if time.monotonic() >= deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return False
        time.sleep(_POLL_S)
    return True


def _occupied(proc: subprocess.Popen[str]) -> bool:
    """Whether any member of `proc`'s group is left. The leader is reaped first: until it is,
    its zombie answers for the group.
    """
    proc.poll()
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    return True
