"""The process-overhead invariant's PUSH half: `process_overhead_high`, filed at a run's end.

A run's own record lands in the usage stream at its end (engine/runtime.py), and that is the
one moment the routine's median can move — so that is when it is read
(`readmodels/overhead_reading.py`). Over the bar, the routine's runs are dominated by its own
process, and the event says so on the blocked-work surface
(`readmodels/health_stream.BLOCKED_EVENTS`), naming the median, the runs it was taken over and
the bar. Only runs of the routine's CURRENT recipe count, so a recipe revision silences it until
three runs under the new recipe say otherwise (`overhead_reading`'s docstring says why).

AT MOST ONCE PER DAY per routine, and the latch is the STREAM itself: an earlier row for the same
routine inside `LATCH_HOURS` keeps the next quiet. A run end has no long-lived object to latch
on (the `health_cooldown` idiom, which may not be reused here: nothing the blocked-work fold
COUNTS may sit in its table), and a latch kept in memory would re-file the whole fleet after
every restart. A routine that fires five times a day is one condition, not five reports of it.

Best-effort like every run-end telemetry write: it never raises into a finished run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

EVENT = "process_overhead_high"
#: One row per routine per day while the condition holds.
LATCH_HOURS = 24

log = logging.getLogger("rsched.overhead")


def filed_recently(routines_home: Path, slug: str, now: datetime | None = None) -> bool:
    """Was `EVENT` filed for `slug` inside the latch window? Read from the stream, so a restart
    remembers it. Fails CLOSED here (an unreadable stream files nothing) — this event repeats
    daily while the condition holds, so a missed row costs a day, never the signal.
    """
    from ..readmodels.health_stream import health_records
    from ..readmodels.stamps import instant

    cutoff = (now or datetime.now(UTC)) - timedelta(hours=LATCH_HOURS)
    for rec in reversed(health_records(Path(routines_home))):
        if rec.get("event") != EVENT or str(rec.get("routine") or "") != slug:
            continue
        when = instant(rec.get("ts"))
        if when is not None:
            return when >= cutoff
    return False


def file_if_high(routines_home: Path, slug: str, run_id: str,
                 routine_dir: Path | None = None) -> bool:
    """File `EVENT` when `slug`'s reading — over its runs of the CURRENT recipe — is over the bar
    and nothing was filed today. Returns whether a row was filed.
    """
    from ..health_events import log_health_event
    from ..readmodels import overhead_reading

    try:
        r = overhead_reading.reading(Path(routines_home), slug, routine_dir)
        if r is None or not r["over"] or filed_recently(routines_home, slug):
            return False
        log_health_event(Path(routines_home), EVENT, routine=slug, run_id=run_id,
                         detail=overhead_reading.describe(r) + " — revise the recipe so its "
                         "runs stop keeping records the task does not need",
                         median=r["median"], threshold=r["threshold"],
                         runs=[x["run_id"] for x in r["runs"]])
    except Exception:  # telemetry must never break a finished run — but leave a breadcrumb
        log.warning("run-end telemetry failed (process overhead)", exc_info=True)
        return False
    return True
