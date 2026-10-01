"""When a cron fires — the one place a fire instant is computed.

The scheduler's fire table, both catch-up paths (a routine's `registry.missed_fire`, a lane's
watermark check and its resume make-up) and the console's week view read their instants here,
so the week strip draws exactly what the daemon will fire.

**A wall-clock time that happens twice fires once.** When daylight saving ends the clock repeats
an hour: 02:30 in Europe/Berlin happens at 00:30 UTC and again at 01:30 UTC, and croniter yields
both, so a routine set to run daily at 02:30 ran twice that night. The rule is the one Vixie cron
has kept for decades. A cron whose minute AND hour fields both name fixed values (`30 2 * * *`,
`0 9,17 * * 1-5`) describes WALL-CLOCK TIMES and fires at the first occurrence only. A cron whose
minute or hour field is a wildcard (`0 * * * *`, `*/20 2 * * *`) describes an INTERVAL and keeps
firing through the repeated hour, once per real hour. When the clock jumps FORWARD, a fixed time
inside the skipped hour fires at the jump (croniter's own answer, and Vixie's), so a daily routine
loses no day in either direction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from croniter import croniter

if TYPE_CHECKING:
    from collections.abc import Iterator


class Schedulable(Protocol):
    """What the fire math needs. RoutineConfig satisfies it directly; a LANE's cron/tz is
    adapted to this shape (`lanes.schedulable`), so both ride the same rules.
    """

    cron: str
    tz: str
    enabled: bool


def names_wall_times(cron: str) -> bool:
    """True when `cron` names fixed wall-clock times: neither its minute nor its hour field is
    a wildcard. The `@` shorthands all name midnight except `@hourly`, which is an interval.
    """
    fields = cron.split()
    if fields and fields[0].startswith("@"):
        return fields[0].lower() != "@hourly"
    return len(fields) >= 2 and not fields[0].startswith("*") and not fields[1].startswith("*")


def _repeats_a_wall_time(t: datetime, tz: ZoneInfo) -> bool:
    """True when `t` is the SECOND occurrence of a local time the clock passes twice.

    Decided from the instant alone: croniter's `fold` attribute is not reliable on what it
    returns, so the two readings of the wall time are rebuilt and `t` compared with the later.
    A repeated time reads EARLIER at fold=0 than at fold=1; a skipped one (spring forward) reads
    the other way round and is never a repeat.
    """
    wall = t.replace(tzinfo=None)
    first = wall.replace(tzinfo=tz, fold=0).astimezone(UTC)
    second = wall.replace(tzinfo=tz, fold=1).astimezone(UTC)
    return first < second and t.astimezone(UTC) == second


def _fires(cron: str, tz_name: str, start: datetime, *, backwards: bool) -> Iterator[datetime]:
    """`cron`'s fire instants in `tz_name`, moving away from `start` (exclusive) in either
    direction, the repeated occurrence of a fixed wall time left out (see the module docstring).
    """
    tz = ZoneInfo(tz_name)
    it = croniter(cron, start.astimezone(tz))
    step = it.get_prev if backwards else it.get_next
    fixed = names_wall_times(cron)
    while True:
        t = step(datetime)
        if not (fixed and _repeats_a_wall_time(t, tz)):
            yield t


def iter_fires(cron: str, tz_name: str, after: datetime) -> Iterator[datetime]:
    """Every instant `cron` fires at in `tz_name` after `after`, in order — the week view's
    enumeration, so it draws what the scheduler fires. Endless: the caller bounds it.
    """
    return _fires(cron, tz_name, after, backwards=False)


def next_fire(cfg: Schedulable, after: datetime) -> datetime | None:
    """The first instant `cfg` fires at after `after`; None for a schedule that is switched
    off (a PAUSED lane reads the same way) or has no cron.
    """
    if not cfg.cron or not cfg.enabled:
        return None
    return next(_fires(cfg.cron, cfg.tz, after, backwards=False))


def last_due_fire(cfg: Schedulable, before: datetime) -> datetime | None:
    """The most recent instant `cfg`'s cron came due before `before`.

    Reads `enabled` like `next_fire` does: a schedule that is switched off — or a lane that is
    PAUSED, which `lanes.schedulable` presents the same way — has no due fire to have missed.
    The repeated occurrence of a fixed wall time is skipped here too, or a boot inside the
    repeated hour would find the first occurrence's run too old and make it up.
    """
    if not cfg.cron or not cfg.enabled:
        return None
    return next(_fires(cfg.cron, cfg.tz, before, backwards=True))
