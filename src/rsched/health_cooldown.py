"""Which health events are STANDING CONDITIONS, and how long one keeps the next quiet.

Most health events are INCIDENTS: a run failed, a fire was refused, a trigger hit its cap.
Two of them are not — `cost_trend_degraded` and `cache_read_degraded` report a condition that
is simply TRUE for a while, and both are read at RUN END. A run end is not a run: a resumed
run finishes once per resume, so one `llmsectest-weekday` run filed 26 cost-trend rows over
5h36m and the next day's filed 22 — 173 of the live stream's 1,289 rows for a condition only
80 runs had ever produced. An operator reading "173 cost events" sees a fleet problem where
there are 25 routines and a handful of readings.

Deduping the PAYLOAD cannot collapse them, and that dead end is worth recording: each finish
appends its own usage row, which shifts the 5-run window the trend is read over, so those 22
rows carried SIX distinct `detail` strings. The reading itself changes between resumes, so
(run_id, detail) matches nothing and run_id alone would suppress a genuinely new reading.

The suppression is therefore on the CLOCK, per (event, routine) — the
`daemon/backup_watch`/`tickguard` idiom of one row a day while the condition holds. It differs
in where the memory lives: those two latch on a long-lived daemon object, and a run-end writer
has none, so the window is read back out of the stream itself.

12 hours was MEASURED against the real stream, not guessed. It collapses the worst resumed run
from 26 rows to 1 and reaches the same 80 rows a per-run latch would, while swallowing the
first row of only 2 of 80 flagged runs. A day is too coarse here: at 24 h, 19 of 80 runs lose
their first row, and a routine that fires twice a day would never report its second reading.

Nothing in `readmodels.health_stream.BLOCKED_EVENTS` may ever be named here (a test pins it):
the console's blocked-fleet fold COUNTS those rows, so suppressing one would make the fleet
look less stuck than it is. An incident that happened twice is two facts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

#: event → hours one filing keeps the next quiet for the same routine. An event absent from
#: this table is filed every time it is reported, which is the right default: see the module
#: docstring for what makes an event belong here.
REPEAT_COOLDOWN_HOURS: dict[str, float] = {
    "cost_trend_degraded": 12,
    "cache_read_degraded": 12,
}


def blocks(routines_home: Path, event: str, routine: str) -> bool:
    """True when `event` was already filed for `routine` inside its cooldown window — the
    newest such row decides, and an event not named in `REPEAT_COOLDOWN_HOURS` never blocks.

    Read through the stream's ONE parser (`readmodels.health_stream.health_records`, memoized
    on the file's stat fingerprint, so a burst of finishes costs one parse), imported lazily
    because that module imports `health_events`, which imports this one.

    Stamps are compared as INSTANTS (`readmodels.stamps.instant`), never as strings:
    `ids.now_iso` writes the host's local time with its offset, so a string compare moves the
    window's edge by that offset and two stamps either side of a DST change compare in the
    wrong order.

    Fails OPEN: any trouble reading the stream files the event. A suppressor that cannot read
    its own history must not become the thing that silences the log.
    """
    hours = REPEAT_COOLDOWN_HOURS.get(event)
    if hours is None:
        return False
    try:
        from .readmodels.health_stream import health_records
        from .readmodels.stamps import instant

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        for rec in reversed(health_records(Path(routines_home))):
            if rec.get("event") != event or str(rec.get("routine") or "") != routine:
                continue
            when = instant(rec.get("ts"))
            if when is None:
                continue                      # an unstamped row dates nothing; keep looking
            return when >= cutoff
    except Exception:                         # fail OPEN — see the docstring
        return False
    return False
