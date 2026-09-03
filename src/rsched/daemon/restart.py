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
import signal
from pathlib import Path

from ..config import ServerConfig

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


def trigger_shutdown() -> None:
    """Signal uvicorn to shut down gracefully (it handles SIGTERM); the process then exits and
    the supervisor relaunches with the new code. Isolated so tests patch it rather than
    signalling the test runner.
    """
    log.warning("self-update: drained — signalling graceful shutdown to restart on new code")
    signal.raise_signal(signal.SIGTERM)
