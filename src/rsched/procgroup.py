"""Ending a subprocess's PROCESS GROUP the way its members can clean up after.

Two runners start their child as the leader of a group of its own (`start_new_session=True`)
so a deadline can end the whole tree: `libgit.git` and `utils_run.run_jailed`, the one runner
of the util, script and `shell` kinds. Both end a call that outlived its deadline HERE, one
way: SIGTERM to every member, up to `TERM_GRACE_S` for all of them to exit, then SIGKILL for
whatever is left. `run_jailed` ends a call its run was aborted under the same way.

SIGTERM comes first because a process cleans up only in its SIGTERM handler. Git deletes its
`index.lock` there and nowhere else: the SIGKILL `subprocess.run` sends at its timeout left an
empty lock that failed every later write in two routine repos on 2026-09-30
(docs/architecture.md, "Git writes"). A util, a script or a `shell` command runs the same git
under its own deadline — the library's `git` util, a routine's script that manages a repo of
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
call that has already spent its deadline or whose run is already stopping.

A member that has exited but not been collected (a zombie) still answers `killpg(pgid, 0)`.
The leader is reaped here; an orphan is reaped by PID 1 — tini in the container
(`init: true`), systemd on the host. Without a reaping init the wait runs the whole grace and
the SIGKILL, a no-op on a zombie, ends it.

The SIGKILL does not depend on the CALLER living through the grace. A caller can die inside
it: the daemon SIGKILLs an aborted engine `runner_state.KILL_GRACE_S` (10 s) after its SIGTERM,
a parent run's exit abandons a child run's thread `subruns.KILL_JOIN_S` (12 s) after asking it
to stop, the OOM killer picks its own moment. The group sits in a session of its own, so none of
those kills reaches it; a member that ignores SIGTERM would then run on with no deadline at all.
So `terminate` arms a BACKSTOP before it waits: an interpreter in a session of its own that
SIGKILLs the group one second after the caller would have — stopped by the caller once the group
is gone. The graces therefore need no order between them. The daemon's stays short, so an abort
that lands during a model call — which no signal interrupts — is not made to wait out a grace
sized to the disk, while the group still gets all of its own. A backstop that fires after its
caller died signals the group id it was given. The kernel hands that id out again only once the
group is empty and the pid counter has wrapped (`pid_max` is 4,194,304 on this host), which
cannot happen inside the second it waits beyond the grace.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import time

log = logging.getLogger("rsched.procgroup")

#: How long a process group gets between SIGTERM and SIGKILL (the module docstring says why).
TERM_GRACE_S = 30
#: How often the wait looks whether the group has emptied.
_POLL_S = 0.05
#: What the backstop runs (module docstring): out-sleep the caller's SIGKILL, then send its own.
#: Isolated (`-I -S`), so it reads no environment and imports nothing outside the interpreter.
_BACKSTOP = """\
# rsched procgroup backstop: SIGKILL process group argv[2] after argv[1] seconds
import os, signal, sys, time
time.sleep(float(sys.argv[1]))
try:
    os.killpg(int(sys.argv[2]), signal.SIGKILL)
except ProcessLookupError:
    pass
"""


def terminate(proc: subprocess.Popen[str]) -> bool:
    """End the process group `proc` leads: SIGTERM to every member, up to `TERM_GRACE_S` for
    all of them to exit, SIGKILL for whatever is left. The leader is reaped either way.

    True when the group emptied on SIGTERM alone, so every member ran its own cleanup; False
    when SIGKILL ended it. Signalling the group after its leader is reaped is safe: the kernel
    keeps a group's id reserved while any member lives and answers ESRCH once none does.

    The backstop is armed only while the wait runs. An exception out of the wait leaves it
    armed: a caller interrupted inside the grace is exactly the caller it stands in for.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    if not _occupied(proc):
        return True
    backstop = _arm(proc.pid)
    deadline = time.monotonic() + TERM_GRACE_S
    while _occupied(proc):
        if time.monotonic() >= deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            _disarm(backstop)
            return False
        time.sleep(_POLL_S)
    _disarm(backstop)
    return True


def _arm(pgid: int) -> subprocess.Popen[bytes] | None:
    """Start the backstop for group `pgid`, or None when no interpreter could be started — the
    caller then ends the group alone, which holds for as long as the caller lives.
    """
    try:
        return subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _BACKSTOP, str(TERM_GRACE_S + 1), str(pgid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except (OSError, ValueError) as exc:
        log.warning("no SIGKILL backstop for process group %s: %s", pgid, exc)
        return None


def _disarm(backstop: subprocess.Popen[bytes] | None) -> None:
    """The caller saw the group end: stop its backstop before that one signals anything."""
    if backstop is not None:
        backstop.kill()
        backstop.wait()


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
