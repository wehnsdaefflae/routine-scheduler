"""Graceful self-restart for the self-updating scheduler.

The self-audit routine (after committing new scheduler code) or the Settings page (its
restart button) drops a restart sentinel file.
The daemon notices it, DRAINS (stops firing new runs, refuses new wizard builds, and waits for
the active runs AND any in-flight new-routine builds to finish), then asks uvicorn to shut down —
and the supervisor (systemd `Restart=always`) relaunches the process on the freshly-committed
code. Three invariants make this safe:

  * a restart is never *begun* while a run is parked in waiting_user/paused — that would freeze
    scheduling waiting on a human; the request is deferred until the system is cleanly drainable;
  * a restart never kills an active run — it waits for `runner.active` to empty first;
  * a restart waits for in-flight wizard builds too — a build is an unpersisted web-process
    background task, so restarting mid-build would strand a half-scaffolded routine.

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


def restart_action(requested: bool, active_states: list[str], draining: bool,
                   builds_active: int = 0) -> str:
    """Pure state machine. Returns one of:

      'idle'    — no request pending (resume normal scheduling if we had been draining)
      'defer'   — request pending but a run is parked (waiting_user/paused): do NOT freeze
                  scheduling on a human; wait for the system to become cleanly drainable
      'drain'   — request pending, only finishable work active (engine runs AND/OR in-flight
                  wizard builds): start draining — fire nothing new, accept no new builds
      'restart' — draining and nothing left active (no runs, no builds): exit so the supervisor
                  relaunches new code

    `builds_active` counts in-flight new-routine wizard builds (web-process background tasks the
    runner does not track). A build is short but unpersisted, so a restart mid-build strands it
    (see wizard_store.recover_orphan_builds); draining until builds finish avoids creating that
    orphan in the first place. A build is finishable work, never 'parked', so it can only hold us
    in 'drain', never 'defer'.
    """
    if not requested:
        return "idle"
    # only *starting* a drain is blocked by parked runs; once draining we wait them out
    if not draining and any(s in PARKED for s in active_states):
        return "defer"
    if not active_states and not builds_active:
        return "restart"
    return "drain"


def trigger_shutdown() -> None:
    """Signal uvicorn to shut down gracefully (it handles SIGTERM); the process then exits and
    the supervisor relaunches with the new code. Isolated so tests patch it rather than
    signalling the test runner.
    """
    log.warning("self-update: drained — signalling graceful shutdown to restart on new code")
    signal.raise_signal(signal.SIGTERM)
