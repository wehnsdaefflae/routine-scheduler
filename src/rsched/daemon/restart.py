"""Graceful self-restart for the self-updating scheduler.

The self-audit routine (after committing new scheduler code) or the Settings page (its
restart button) drops a restart sentinel file.
The daemon notices it and, crucially, keeps scheduling normally — a pending restart NEVER
blocks starting a run or a conversation (operator, 2026-09-03). It just waits for a quiet gap:
once nothing has been active for `RESTART_IDLE_S` seconds it asks uvicorn to shut down, and the
supervisor (systemd `Restart=always`) relaunches the process on the freshly-committed code.
Two invariants make this safe:

  * a restart never fires while a run is parked in waiting_user/paused — it would restart out
    from under a conversation the user is mid-dialogue with, and freezes nothing on a human;
  * a restart never kills an active run — it fires only once `runner.active` is empty AND has
    stayed empty for the idle window, so a run in flight is never interrupted.

`restart_action` is a pure decision function so the state machine is unit-tested without
touching processes or signals. The sentinel lives under a dot-dir the registry scan ignores.
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

from ..config import ServerConfig
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

log = logging.getLogger("rsched.restart")

PARKED = ("waiting_user", "paused")

# A pending restart fires only after the system has been idle (nothing active) for this long, so
# it never blocks starting a run or conversation — it waits for a quiet gap instead (operator,
# 2026-09-03: "when the daemon is draining it should not prevent the start of a new conversation
# or routine run; it should just restart as soon as there's none running for 10 seconds").
RESTART_IDLE_S = 10


def sentinel_path(server: ServerConfig) -> Path:
    """Where a routine drops its restart request (registry.scan skips this dot-dir)."""
    return server.routines_home / ".control" / "restart.request"


def restart_requested(server: ServerConfig) -> bool:
    return sentinel_path(server).exists()


def clear_request(server: ServerConfig) -> None:
    try:
        sentinel_path(server).unlink()
    except FileNotFoundError:
        pass


def restart_action(requested: bool, active_states: list[str], idle_long_enough: bool) -> str:
    """Pure state machine. Returns one of:

    'idle'    — no restart request pending.
    'defer'   — request pending but a run is parked (waiting_user/paused): keep scheduling
                normally and do NOT restart — never restart out from under a conversation the
                user is mid-dialogue with, and never freeze scheduling on a human.
    'wait'    — request pending and runs are still active, OR nothing is active but the idle
                window has not elapsed yet: keep scheduling normally (fire new runs and
                conversations as usual) and do not restart yet. This is the operator's rule —
                a pending restart never blocks a start; it waits for a quiet gap instead.
    'restart' — request pending, nothing active, and the system has been idle long enough:
                exit so the supervisor relaunches the new code.

    `idle_long_enough` is the caller's clock: True once `active_states` has been empty for
    `RESTART_IDLE_S` (the scheduler tracks the idle-since stamp; this function stays pure).
    """
    if not requested:
        return "idle"
    if any(s in PARKED for s in active_states):
        return "defer"
    if active_states:
        return "wait"
    return "restart" if idle_long_enough else "wait"


def shutdown_mark_path(server: ServerConfig) -> Path:
    """Where a DELIBERATE shutdown leaves its breadcrumb for the next boot to read (F480)."""
    return server.routines_home / ".control" / "shutdown.mark"


def mark_deliberate_shutdown(server: ServerConfig, reason: str) -> None:
    """Record that THIS exit was asked for, so the next boot can tell a restart from a crash.

    Without it the boot reap has no way to establish why a pid is gone: a self-update restart,
    a container stop, a kernel OOM and a segfault all arrive as the same dead pid, and
    `recover_orphans` used to assert "orphaned by daemon restart" for every one of them — a
    symptom stated as an established cause, which sent three separate investigations (F480,
    R1501, R1515) at a drain that works correctly. The mark is the only evidence that
    distinguishes them, so it is written on the way out rather than inferred on the way in.

    Best-effort: a failure to write it must never block the shutdown it is describing. The
    cost of a missing mark is an orphan recorded as `unknown` — which is the honest reading
    when nothing established the cause.
    """
    path = shutdown_mark_path(server)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"ts": now_iso(), "reason": reason, "pid": os.getpid()})
    except OSError as exc:
        # Never block the shutdown this is describing — see the docstring.
        log.warning("could not write shutdown mark (%s): %s", path, exc)


def read_shutdown_mark(routines_home: Path) -> dict | None:
    """Read and CONSUME the breadcrumb a deliberate shutdown left (None if there is none).

    Consumed, not merely read: the mark describes exactly one exit, so leaving it in place
    would make every later crash read as that same restart — the failure mode this whole
    change exists to end. A mark that cannot be removed is still returned (the boot's reading
    is correct now); the stale-mark risk is logged rather than hidden.
    """
    path = routines_home / ".control" / "shutdown.mark"
    mark = read_json(path)
    if not isinstance(mark, dict):
        return None
    try:
        path.unlink()
    except OSError as exc:
        log.warning("shutdown mark %s could not be consumed: %s", path, exc)
    return mark


def trigger_shutdown(server: ServerConfig | None = None,
                     reason: str = "self-update restart") -> None:
    """Signal uvicorn to shut down gracefully (it handles SIGTERM); the process then exits and
    the supervisor relaunches with the new code. Isolated so tests patch it rather than
    signalling the test runner.

    `server` is what lets the exit leave its breadcrumb (F480). It is optional only because
    the signal must be raised even when no server config is at hand; an unmarked exit is
    reaped as `unknown`, never as a restart.
    """
    if server is not None:
        mark_deliberate_shutdown(server, reason)
    log.warning("self-update: drained — signalling graceful shutdown to restart on new code")
    signal.raise_signal(signal.SIGTERM)
