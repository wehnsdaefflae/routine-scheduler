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

from .. import firetimes, lane_fires, lane_runs, lanes
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
        due = firetimes.last_due_fire(lanes.schedulable(lane), now)
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


def resume_catchup(server: ServerConfig, now: datetime) -> list[str]:
    """Make up the fires the global pause skipped, at the moment it is LIFTED (D156, option C).

    The pause's own promise is that resuming does not backlog-fire (daemon/pause.py), and for a
    DAILY lane that promise is right: the next fire is hours away and a make-up chain would run
    a lane twice in one day for a pause measured in minutes. For a long-cadence lane it is
    exactly wrong. F573 measured the cost: a 65-minute pause on 2026-09-26 spanned the 05:00
    Saturday fire of a WEEKLY lane, and its two members went dark for eight days — the fire was
    not deferred, it was gone, and the wait for the next one was a whole week.

    So the make-up is decided per lane by ONE question — is the fire still EARLY in this lane's
    own period? A fire is made up while less than a QUARTER of the lane's cadence has passed
    since it was due: a daily lane tolerates six hours late, a weekly one forty-two, a
    fortnightly one three and a half days. Past that a make-up run is no longer "the fire,
    late" but an extra fire crowding the next one, and waiting is the smaller distortion.

    It is deliberately a RATIO and not a cadence threshold: a rule phrased as "weekly lanes are
    made up" is a rule a fortnightly lane falls through, and F573's lane was weekly only by
    accident of that week's configuration.

    Bounded exactly like `boot_catchup`: one chain per lane, never for a paused/unscheduled lane
    or one whose policy is `skip`, never while a chain is in flight, and the arm moves the
    watermark so nothing is made up twice.
    """
    home = server.routines_home
    armed: list[str] = []
    for lane in lanes.list_lanes(home):
        skipped = lane_fires.last_paused_skip(home, lane["id"])
        if skipped is None:
            continue
        lane_fires.clear_paused_skip(home, lane["id"])
        if lane.get("paused") or lane.get("catchup") == "skip":
            continue
        sched = lanes.schedulable(lane)
        # The lane's own CADENCE, measured as the gap between the two fires that bracket the
        # one that was dropped — NOT the distance from now to the next fire. Those two differ
        # in exactly the case that matters: an hour after a daily lane's 05:00 fire, the next
        # one is 23 hours away, and an hour is comfortably less than half of 23 — so a
        # distance-to-next test makes up a daily lane the pause barely inconvenienced. Against
        # the 24-hour INTERVAL the same hour is a twelfth, and the lane is correctly left alone
        # while a weekly lane's hour out of 168 is made up.
        nxt = firetimes.next_fire(sched, skipped)
        if nxt is None:
            continue
        interval = (nxt - skipped).total_seconds()
        owed = (now - skipped).total_seconds()
        # Made up only while the fire is still EARLY in its own period — a QUARTER of the
        # cadence, which is the same single ratio for every lane and reads as a sentence: a
        # daily lane tolerates being six hours late, a weekly one forty-two. Past that the
        # make-up run stops being "the fire, late" and becomes "an extra fire", and for a daily
        # lane the next one is close enough that waiting is the smaller distortion.
        #
        # Half the interval was the first attempt and it is wrong for the case that matters:
        # ten hours owed against a 24 h cadence is under half, so a daily lane paused for most
        # of a working day was made up at 15:00 and again at 05:00 the next morning. The
        # break-even framing ignored that the two runs are then only 14 h apart.
        if owed <= 0 or interval <= 0 or owed * 4 >= interval:
            log.info("lane resume catch-up declined lane=%s owed=%.0fs interval=%.0fs",
                     lane["id"], owed, interval)
            continue
        rec = lane_runs.arm(home, lane, default_on_failure=lanes.default_on_failure(home),
                            armed_by="catchup")
        if rec is None:
            log.info("lane resume catch-up skipped — chain still in flight lane=%s", lane["id"])
            continue
        armed.append(lane["id"])
        log.info("lane resume catch-up armed lane=%s (%s)", lane["id"], lane.get("name"))
        log_health_event(home, "lane_fire_catchup", routine=lane["id"], run_id="",
                         detail=f"{lane.get('name') or lane['id']}: the fire the global pause "
                                f"skipped at {skipped.isoformat()} was made up when the pause "
                                f"was lifted - {owed / 60:.0f} min late against this lane's own "
                                f"{interval / 3600:.1f} h cadence, so firing now is far closer "
                                f"to the intended time than waiting for the next one")
    return armed


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
