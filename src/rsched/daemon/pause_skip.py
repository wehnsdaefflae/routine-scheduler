"""What the GLOBAL PAUSE does with a due fire it drops — for a routine and for a lane.

The pause (D34) skips its own fires and keeps advancing the fire table. A skipped fire is GONE:
nothing makes it up when the pause lifts or at the next boot (D237, operator 2026-11-05: "Remove
all catch-up: a fire missed for any reason (pause, restart, crash) is skipped." — reversing the
make-ups D156 and D183 had added). So the one thing left to do with a dropped fire is to RECORD
it, and both records live here because for as long as they lived in `Scheduler.run_forever`
they DRIFTED, and the drift was invisible:

  - the LANE path wrote an event since F573;
  - the ROUTINE path wrote `log.info` and nothing else, and the journal is the one surface
    this instance's own audit cannot read.

Measured 2026-10-14: a pause held from ~03:00 to 21:52 on 2026-10-13 ate eight due fires. The
two ROUTINE fires — `scheduler-builder`, cron `0 6,14`, in no lane — left nothing: no event, no
run. The audit run that found it had to prove the fires were due from the ABSENCE of a run
directory. Each event therefore carries the subject's `cron` and `next_fire`: the COST of a skip
is the cron interval, not the pause, so a reader who cannot see the cadence cannot tell a
ten-minute inconvenience from a lost week.
"""

from __future__ import annotations

from datetime import datetime

from ..config import ServerConfig
from ..health_events import log_health_event


def routine_fire_dropped(server: ServerConfig, slug: str, cron: str, due: datetime,
                         next_fire: datetime) -> None:
    """Record that the pause skipped `slug`'s fire due at `due` — `fire_paused`, keyed on the
    slug. Nothing owes it afterwards: the routine next runs on its cron (D237).
    """
    log_health_event(
        server.routines_home, "fire_paused", routine=slug, run_id="",
        detail=f"due fire ({due.isoformat()}) skipped because scheduling is paused - it is "
               f"not made up (D237); {slug} next runs on its cron",
        cron=str(cron or ""), next_fire=next_fire.isoformat())


def lane_fire_dropped(server: ServerConfig, lane: dict, due: datetime,
                      next_fire: datetime) -> None:
    """Record that the pause skipped this lane's fire due at `due` — `lane_fire_paused`, keyed
    on the lane id. Nothing owes it afterwards: the lane next fires on its cron (D237).
    """
    log_health_event(
        server.routines_home, "lane_fire_paused", routine=lane["id"], run_id="",
        detail=f"{lane.get('name') or lane['id']}: due lane fire ({due.isoformat()}) skipped "
               f"because scheduling is paused - it is not made up (D237), so the next one is "
               f"a whole cron interval away",
        cron=str(lane.get("cron") or ""), next_fire=next_fire.isoformat())
