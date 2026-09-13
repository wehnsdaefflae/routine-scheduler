"""Settings: the global scheduling pause (D34). POST drops the durable pause sentinel
(daemon/pause.py) — the scheduler skips scheduled fires and defers trigger/one-shot
intake until DELETE removes it; manual "run now" stays available as the operator's
explicit override. /api/status reports the flag (`paused`); the dashboard polls it
for its banner. Both calls are idempotent, like the restart pair next door.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...daemon import pause as pause_ctl
from .common import server_of

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
    pause_ctl.set_paused(server_of(request), False)
    return {"ok": True, "paused": False}
