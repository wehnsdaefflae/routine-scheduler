"""Make up the ROUTINE fires a global pause dropped — the routine analog of `lane_catchup`
(D183, operator-answered 2026-10-15).

A pause-skipped LANE fire is always made up when the pause lifts or at the next boot, while a
pause-skipped ROUTINE fire was never made up at all: `scheduler.run_forever` advanced the fire
table, recorded the skip and moved on. Measured 2026-10-14 on this instance: a pause held from
~03:00 to 21:52 on 10-13 and another on 10-14 ate FOUR consecutive fires of `scheduler-builder`
(cron `0 6,14`, in no lane) — it held five handed-over items and shipped nothing for three days,
and the only visible symptom was the absence of a run directory.

D34 chose "skip, don't backlog" for the pause and that reasoning holds for a frequent cron. It
does not hold for a twice-daily routine, which loses half a day per skip. The cost is not the
pause, it is the CRON INTERVAL — which is exactly the judgement `lane_catchup.resume_catchup`
already makes per lane, so this module makes it per routine with the same ratio:

    a dropped fire is made up while less than a QUARTER of this routine's own cadence has
    passed since it was due.

A daily routine tolerates being six hours late, a twice-daily one three, a weekly one
forty-two. Past that the make-up stops being "the fire, late" and becomes an extra fire
crowding the next one. It is a RATIO and not a cadence threshold for `resume_catchup`'s
measured reason: a rule phrased as "twice-daily routines are made up" is a rule the next
cadence falls through.

TWO DELIBERATE DIFFERENCES from the lane path, each measured:

1. **The boot `catchup` policy does not gate this.** `registry.missed_fire` honours
   `cfg.catchup` because a daemon that was DOWN may have missed many fires and the operator
   may not want them replayed. A pause-skipped fire is a different fact: the daemon was up,
   the fire was due, and the operator's own pause dropped it. Gating on that field would have
   shipped this decision as a no-op — all 34 routines on this instance read `catchup: skip`,
   `scheduler-builder` included, so not one of the fires the decision exists to recover would
   have been recovered.
2. **The make-up is armed as a ONE-SHOT** (`schedule_once.arm`, `fire_at` now), not fired
   inline. The resume runs in a sync web handler off the daemon's loop, and the lane path's
   equivalent (`lane_runs.arm`) is likewise a durable record the daemon ticks. So the one-run
   -per-routine rule, `max_concurrent_runs` and the restart drain stay the daemon's, exactly
   as for a cron fire — and a make-up decided while the daemon is busy is not lost.

A DECLINE is an event, never a log line alone (`routine_catchup_declined`). That is
`lane_catchup`'s own lesson, measured 2026-10-15: the absence of a catch-up event reads the
same as a daemon that never got there, so "it was not made up, and here is why" has to be
something a reader can fetch.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .. import firetimes, registry, routine_fires, schedule_once
from ..config import ServerConfig
from ..health_events import log_health_event

log = logging.getLogger("rsched.routine_catchup")

#: Made up while less than this fraction of the routine's own cadence has passed since the
#: dropped fire was due. `lane_catchup` uses the same quarter, arrived at by measurement: half
#: the interval made up a daily lane that had been paused for most of a working day, putting
#: two runs 14 h apart.
EARLY_FRACTION = 4


def _cadence_s(cfg, skipped: datetime) -> float:
    """This routine's own cadence, as the gap between the dropped fire and the one AFTER it.

    Not the distance from now to the next fire: an hour after a daily routine's 06:00 fire the
    next one is 23 h away and an hour is a small part of that, so a distance-to-next test makes
    up a daily routine a pause barely inconvenienced. Against the 24 h INTERVAL the same hour
    is a twenty-fourth and the routine is correctly left alone.
    """
    nxt = firetimes.next_fire(cfg, skipped)
    return (nxt - skipped).total_seconds() if nxt is not None else 0.0


def _make_up(server: ServerConfig, slug: str, cfg, skipped: datetime, now: datetime,
             *, at: str) -> bool:
    """Decide and record ONE routine's owed fire. True when a make-up was armed.

    The owed stamp is cleared either way — a decision taken is a decision taken, and an owed
    fire surviving the resume that declined it would be made up by the next resume, at a time
    nobody skipped anything.
    """
    routine_fires.clear_paused_skip(server.routines_home, slug)
    interval = _cadence_s(cfg, skipped)
    owed_s = (now - skipped).total_seconds()
    nxt_from_now = firetimes.next_fire(cfg, now)
    nxt_iso = nxt_from_now.isoformat() if nxt_from_now else ""
    if owed_s <= 0 or interval <= 0 or owed_s * EARLY_FRACTION >= interval:
        log.info("routine catch-up declined slug=%s owed=%.0fs interval=%.0fs at=%s",
                 slug, owed_s, interval, at)
        log_health_event(
            server.routines_home, "routine_catchup_declined", routine=slug, run_id="",
            detail=f"the fire the pause skipped at {skipped.isoformat()} was NOT made up - "
                   f"{owed_s / 3600:.1f} h owed is past a quarter of this routine's own "
                   f"{interval / 3600:.1f} h cadence, so a make-up run would crowd the next "
                   f"fire rather than stand in for the lost one. It next runs on its cron",
            cron=str(getattr(cfg, "cron", "") or ""), next_fire=nxt_iso)
        return False
    schedule_once.arm(server.routines_home, slug, fire_at=now,
                      reason=f"make-up fire: the global pause skipped this routine's "
                             f"{skipped.isoformat()} cron fire, which is {owed_s / 60:.0f} min "
                             f"late against its own {interval / 3600:.1f} h cadence. Nothing "
                             f"else is owed — this stands in for that one fire.",
                      requested_by=f"catchup:{at}")
    log.info("routine catch-up armed slug=%s owed=%.0fs interval=%.0fs at=%s",
             slug, owed_s, interval, at)
    log_health_event(
        server.routines_home, "fire_catchup", routine=slug, run_id="",
        detail=f"the fire the global pause skipped at {skipped.isoformat()} was made up "
               f"{at} - {owed_s / 60:.0f} min late against this routine's own "
               f"{interval / 3600:.1f} h cadence, so firing now is far closer to the intended "
               f"time than waiting for the next one",
        cron=str(getattr(cfg, "cron", "") or ""), next_fire=nxt_iso)
    return True


def catchup(server: ServerConfig, now: datetime | None = None, *, at: str) -> list[str]:
    """Make up every owed routine fire, at most one per routine. Returns the slugs armed.

    `at` says which moment asked ("when the pause was lifted" / "at boot") and reaches the
    reader in the event detail: both are legitimate and they mean different things about how
    long the work has been waiting.

    Called at the resume (the operator's choice, as for lanes) AND at boot, because a pause
    lifted while the daemon was down leaves the owed stamp on disk with nobody to read it.
    """
    now = now or datetime.now(UTC)
    home = server.routines_home
    pending = routine_fires.owed(home)
    if not pending:
        return []
    catalog = registry.scan(server)
    armed: list[str] = []
    for slug, skipped in sorted(pending.items()):
        info = catalog.get(slug)
        if info is None:
            # A routine that no longer exists cannot be fired, and its stamp must not outlive
            # it: clear it so the file does not accumulate names nothing can resolve.
            routine_fires.clear_paused_skip(home, slug)
            continue
        if not info.fireable:
            # Switched off or finished since the skip — the operator's state wins over an
            # owed fire, exactly as `boot_catchup` reads `fireable`.
            routine_fires.clear_paused_skip(home, slug)
            continue
        if _make_up(server, slug, info.cfg, skipped, now, at=at):
            armed.append(slug)
    return armed
