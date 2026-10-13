"""What the GLOBAL PAUSE does with a due fire it drops — for a routine and for a lane.

The pause (D34) skips its own fires and keeps advancing the fire table, because resuming must
not backlog-fire everything that came due. That leaves one question per dropped fire: what
record is there that it was due at all? Both answers live here because for as long as they
lived in `Scheduler.run_forever` they DRIFTED, and the drift was invisible:

  - the LANE path has written an event since F573 and moves two watermarks with it;
  - the ROUTINE path wrote `log.info` and nothing else, and the journal is the one surface
    this instance's own audit cannot read.

Measured 2026-10-14: a pause held from ~03:00 to 21:52 on 2026-10-13 and ate eight due fires.
The six LANE fires are each on the record (`lane_fire_paused`, and two were made up at the
resume). The two ROUTINE fires — `scheduler-builder`, cron `0 6,14`, in no lane — left nothing:
no event, no watermark, no run. The audit run that found it had to prove the fires were owed
from the ABSENCE of a run directory, and the only visible symptom was that the builder was
holding five hand-overs and had shipped nothing. Two routines on this instance fire outside
every lane, so this was the one class of due work that could vanish leaving no trace a reader
could reach.

The asymmetry that remains is deliberate and the details say so: a LANE's dropped fire is
recorded as owed (`lane_fires.stamp_paused_skip`) and `lane_catchup.resume_catchup` makes it up
when the pause lifts while it is still early in the lane's own cadence. There is no routine
analog, so a routine's dropped fire is simply gone: a daily routine is a day late, a weekly one
a week.
"""

from __future__ import annotations

from datetime import datetime

from .. import lane_fires
from ..config import ServerConfig
from ..health_events import log_health_event


def routine_fire_dropped(server: ServerConfig, slug: str, cron: str,
                         next_fire: datetime) -> None:
    """Record that the pause skipped `slug`'s due fire — `fire_paused`, keyed on the slug.

    Carries `cron` and `next_fire` for the same reason the lane event does: the COST of the
    skip is the cron interval, not the pause, so a reader who cannot see the cadence cannot
    tell a ten-minute inconvenience from a lost week.
    """
    log_health_event(
        server.routines_home, "fire_paused", routine=slug, run_id="",
        detail=f"due fire skipped because scheduling is paused - a routine skip is not made "
               f"up when the pause lifts, so {slug} next runs at its following cron fire",
        cron=str(cron or ""), next_fire=next_fire.isoformat())


def lane_fire_dropped(server: ServerConfig, lane: dict, due: datetime,
                      next_fire: datetime) -> None:
    """Record that the pause skipped this lane's due fire, and move both watermarks.

    The ARM watermark moves because a deliberate skip is a HANDLED fire: only an arm stamped it
    before, so the operator's pause left the lane's last due fire reading as unarmed, and the
    next boot — nightly, here — made it up as a whole chain and blamed the daemon for being
    down. Pause's own promise is that resuming does not backlog-fire (`daemon/pause.py`).

    The OWED stamp records what the watermark handled, so lifting the pause can make it up
    (D156 option C). The watermark alone says "handled" and cannot say "handled by dropping
    it", which is why F573's weekly lane lost fire was invisible to both catch-up paths.
    """
    lane_fires.stamp(server.routines_home, lane["id"])
    lane_fires.stamp_paused_skip(server.routines_home, lane["id"], due.isoformat())
    log_health_event(
        server.routines_home, "lane_fire_paused", routine=lane["id"], run_id="",
        detail=f"{lane.get('name') or lane['id']}: due lane fire skipped because scheduling "
               f"is paused - the watermark moves with it, so this fire is GONE rather than "
               f"deferred and the next one is a whole cron interval away",
        cron=str(lane.get("cron") or ""), next_fire=next_fire.isoformat())
