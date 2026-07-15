"""Settings: graceful daemon restart, to pick up committed code. POST drops the SAME
sentinel the self-audit routine uses (daemon/restart.py); the scheduler's tick loop does
the rest — drain (deferred while a run is parked on a human), clean exit, supervisor
relaunch. DELETE withdraws a still-pending request; the sentinel state and the process
start time live in /api/status (`restart_requested`, `started`), which the UI polls to
show the drain → down → back-up cycle.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...daemon import restart as restart_ctl
from ...ids import now_iso
from ...paths import atomic_write_json
from .common import server_of

router = APIRouter()


@router.post("/settings/restart")
def request_restart(request: Request) -> dict:
    """Idempotent — re-posting while one is pending just refreshes the sentinel. The
    response says what stands between the request and the actual exit, so the UI can
    tell 'restarting momentarily' from 'draining' from 'deferred on a parked run'.
    """
    atomic_write_json(restart_ctl.sentinel_path(server_of(request)),
                      {"ts": now_iso(), "via": "web-settings"})
    states = request.app.state.runner.active_states()
    return {"ok": True, "active_runs": len(states),
            "parked": sum(s in restart_ctl.PARKED for s in states)}


@router.delete("/settings/restart")
def withdraw_restart(request: Request) -> dict:
    """Also idempotent — withdrawing an already-consumed (or never-made) request is a no-op;
    the scheduler notices the missing sentinel next tick and resumes normal scheduling.
    """
    restart_ctl.clear_request(server_of(request))
    return {"ok": True}
