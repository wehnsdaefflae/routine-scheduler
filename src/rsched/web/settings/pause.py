"""Settings: the global scheduling pause (D34). POST drops the durable pause sentinel
(daemon/pause.py) — the scheduler skips scheduled fires and defers trigger/one-shot
intake until DELETE removes it; manual "run now" stays available as the operator's
explicit override. /api/status reports the flag (`paused`); the dashboard polls it
for its banner. Both calls are idempotent, like the restart pair next door.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...daemon import lane_catchup
from ...daemon import pause as pause_ctl
from .common import server_of

log = logging.getLogger("rsched.settings.pause")

router = APIRouter()


@router.post("/settings/pause")
def pause_scheduling(request: Request) -> dict:
    server = server_of(request)
    pause_ctl.set_paused(server, True)
    # Only runs active at this click inherit the hold. A later explicit Run now
    # remains an override; children read their root run's control file too.
    from ... import registry
    from ..routines_common import merge_control

    roots = {run.dir for info in registry.scan(server).values()
             for run in info.runs if run.state in registry.ACTIVE_STATES}
    roots.update(run.run_dir for run in tuple(request.app.state.runner.active.values())
                 if run.run_dir.parent.parent.parent.resolve() == server.routines_home.resolve())
    for root in roots:
        merge_control(root, {"scheduling_pause": pause_ctl.generation(server)})
    return {"ok": True, "paused": True}


@router.delete("/settings/pause")
def resume_scheduling(request: Request) -> dict:
    server = server_of(request)
    pause_ctl.set_paused(server, False)
    # D156 option C: make up, right here, the fires the pause DROPPED — but only where waiting
    # costs more than firing late (lane_catchup.resume_catchup owns that judgement). Doing it on
    # resume rather than at the next boot is the operator's choice: a weekly lane skipped by a
    # 65-minute pause waited eight days for a restart to notice (F573), and the wait was the
    # whole cost. Failing to make one up must never fail the resume itself — the pause is lifted
    # the moment the sentinel is gone, and that is what the caller asked for.
    from datetime import UTC, datetime

    made_up: list[str] = []
    try:
        made_up = lane_catchup.resume_catchup(server, datetime.now(UTC))
    except Exception:
        # Broad on purpose, and the comment above says why: the pause is lifted the moment the
        # sentinel is gone, and that is what the caller asked for. A make-up failure must be
        # loud in the log and invisible to the resume.
        log.exception("lane resume catch-up failed after lifting the pause")
    return {"ok": True, "paused": False, "lanes_made_up": made_up}
