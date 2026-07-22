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


# A clarify run whose engine has not yet taken over ('starting', no pid) counts as active
# only while its status stamp is this fresh — so an orphaned session (e.g. killed by a
# previous restart) can never block every future restart.
CLARIFY_FRESH_S = 15 * 60


def clarify_states(server: ServerConfig) -> list[str]:
    """Live clarify-run states of in-flight new-routine wizard sessions.

    A clarify session's engine subprocess is spawned DIRECTLY by the web layer
    (wizard_sessions.start_clarify), never via Runner.fire — so it is invisible to
    runner.active_states(). Its run lives under the protected `clarification` template
    routine (`<home>/clarification/runs/<ts>`, D13=B) while the engine executes in the
    hidden .wizard-<ts> workspace; a restart mid-clarification kills the user's setup
    conversation (observed 2026-07-16: a drain fired while a fresh clarify run was still
    decomposing and orphaned it at turn 0). Folding these states into restart_action's
    active_states gives clarify runs the same protection ordinary runs have: waiting_user
    defers the restart, running/starting drains it.

    Two guards keep a dead session from parking restarts forever: a run WITH a pid counts
    only while that pid is alive; a run with no pid yet ('starting' — the engine subprocess
    is booting/decomposing) counts only while its status stamp is fresh (CLARIFY_FRESH_S).
    """
    from datetime import UTC, datetime

    from ..paths import read_json
    from .registry import TERMINAL_STATES
    from .runner import _pid_alive

    out: list[str] = []
    runs_dir = server.routines_home / "clarification" / "runs"
    for rd in sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []:
        st = read_json(rd / "status.json")
        if not isinstance(st, dict) or st.get("state") in TERMINAL_STATES:
            continue
        state = str(st.get("state") or "unknown")
        if st.get("pid"):
            if _pid_alive(st["pid"]):
                out.append(state)
            continue
        try:
            updated = datetime.fromisoformat(str(st.get("updated") or ""))
        except ValueError:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if abs((datetime.now(UTC) - updated).total_seconds()) <= CLARIFY_FRESH_S:
            out.append(state)
    return out


def trigger_shutdown() -> None:
    """Signal uvicorn to shut down gracefully (it handles SIGTERM); the process then exits and
    the supervisor relaunches with the new code. Isolated so tests patch it rather than
    signalling the test runner.
    """
    log.warning("self-update: drained — signalling graceful shutdown to restart on new code")
    signal.raise_signal(signal.SIGTERM)
