"""Boot catch-up for SCHEDULED lanes — the lane analog of `Scheduler.boot_catchup`.

A routine's own cron gets one make-up run at boot when its `catchup` is run_once and the
last due fire has no run covering it (`registry.missed_fire`). Lanes had nothing of the
kind: their fire table is process memory, so the daemon being down at 08:30 on a Tuesday
meant the Tue/Thu lane fired next on Thursday — if the daemon was up then — and nobody was
told. This module compares each scheduled lane's most recent DUE fire against the on-disk
watermark of its last arm (`rsched.lane_fires`, stamped by every `lane_runs.arm`) and arms
ONE make-up chain when the due fire is newer.

Bounded the way the routine rule is bounded: one chain per lane, however many fires were
missed, and never while a chain is already in flight (the arm is refused, exactly as a
due cron fire is). A lane with NO watermark is stamped now and left alone — the first boot
after the upgrade must not fire every lane at once, and from then on every arm leaves a mark.
A paused lane, an unscheduled lane, or a lane whose policy is `skip` is never made up.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .. import lane_fires, lane_runs, lanes, registry
from ..config import ServerConfig
from ..health_events import log_health_event

log = logging.getLogger("rsched.lane_catchup")


def missed_lanes(server: ServerConfig, now: datetime) -> list[dict]:
    """The scheduled lanes whose last due fire nobody armed — stamping (and skipping) any
    lane that has no watermark yet.
    """
    home = server.routines_home
    out: list[dict] = []
    for lane in lanes.list_lanes(home):
        due = registry.last_due_fire(lanes.schedulable(lane), now)
        if due is None:
            continue
        armed = lane_fires.last_armed(home, lane["id"])
        if armed is None:
            lane_fires.stamp(home, lane["id"], now.isoformat())
            continue
        if lane.get("catchup") != "run_once":
            continue
        if armed < due:
            out.append(lane)
    return out


def boot_catchup(server: ServerConfig, now: datetime) -> list[str]:
    """Arm one make-up chain per missed lane. Returns the lane ids armed."""
    home = server.routines_home
    armed: list[str] = []
    for lane in missed_lanes(server, now):
        rec = lane_runs.arm(home, lane, default_on_failure=lanes.default_on_failure(home),
                            armed_by="catchup")
        if rec is None:
            log.info("lane catch-up skipped — chain still in flight lane=%s", lane["id"])
            continue
        armed.append(lane["id"])
        log.info("lane catch-up armed lane=%s (%s)", lane["id"], lane.get("name"))
        log_health_event(home, "lane_fire_catchup", routine=lane["id"], run_id="",
                         detail=f"{lane.get('name') or lane['id']}: the last due fire was "
                                "not armed - one make-up chain armed at boot")
    return armed
