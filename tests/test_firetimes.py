"""firetimes — when a cron fires, and the one rule croniter does not keep: a fixed wall-clock
time the clock repeats when daylight saving ends fires ONCE (Vixie cron's rule), while an
interval cron keeps firing through the repeated hour.

The fixture night is 2026-10-25 in Europe/Berlin: at 03:00 CEST (01:00 UTC) the clock goes
back to 02:00 CET, so every wall time from 02:00 to 02:59 happens twice — first at UTC+2, then
an hour later at UTC+1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import islice
from types import SimpleNamespace

import pytest

from rsched import firetimes
from rsched.web.api_schedule import _cron_fires

BERLIN = "Europe/Berlin"


def _z(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _fires(cron: str, after: datetime, n: int) -> list[datetime]:
    return [t.astimezone(UTC) for t in islice(firetimes.iter_fires(cron, BERLIN, after), n)]


def _sched(cron: str) -> SimpleNamespace:
    return SimpleNamespace(cron=cron, tz=BERLIN, enabled=True)


def test_a_daily_time_the_clock_repeats_fires_once():
    """02:30 happens at 00:30Z and again at 01:30Z; croniter yields both, and a daily 02:30
    routine ran twice that night. Only the first occurrence fires — from the scheduler's own
    `next_fire(now)` at any moment inside the repeated hour too."""
    assert _fires("30 2 * * *", _z(24, 12), 3) == [_z(25, 0, 30), _z(26, 1, 30), _z(27, 1, 30)]
    for now in (_z(25, 0, 30), _z(25, 0, 31), _z(25, 1, 0), _z(25, 1, 29)):
        assert firetimes.next_fire(_sched("30 2 * * *"), now) == _z(26, 1, 30), now


def test_a_fixed_hour_list_fires_each_wall_time_once():
    """01:30, 02:30 and 03:30 each fire once: the second 02:30 (01:30Z) is the repeat."""
    assert _fires("30 1-3 * * *", _z(24, 12), 4) == [
        _z(24, 23, 30), _z(25, 0, 30), _z(25, 2, 30), _z(26, 0, 30)]


@pytest.mark.parametrize(("cron", "expected"), [
    # hour wildcard: one fire per REAL hour — 02:00 CEST, 02:00 CET, 03:00 CET
    ("0 * * * *", [_z(25, 0), _z(25, 1), _z(25, 2)]),
    # minute wildcard: an interval inside hour 2 — it runs through both passes (Vixie)
    ("*/20 2 * * *", [_z(25, 0), _z(25, 0, 20), _z(25, 0, 40),
                      _z(25, 1), _z(25, 1, 20), _z(25, 1, 40)]),
])
def test_an_interval_cron_keeps_firing_through_the_repeated_hour(cron, expected):
    assert _fires(cron, _z(24, 23, 59), len(expected)) == expected


def test_spring_forward_fires_the_skipped_time_at_the_jump():
    """2026-03-29: 02:00 CET jumps to 03:00 CEST, so 02:30 never happens; it fires at the jump
    and the day is not lost."""
    assert _fires("30 2 * * *", _z(28, 12, month=3), 2) == [
        _z(29, 1, month=3), _z(30, 0, 30, month=3)]


@pytest.mark.parametrize(("cron", "wall"), [
    ("30 2 * * *", True), ("0 9,17 * * 1-5", True), ("30 1-3 * * *", True),
    ("@daily", True), ("@weekly", True),
    ("0 * * * *", False), ("*/20 2 * * *", False), ("15 */2 * * *", False), ("@hourly", False),
])
def test_names_wall_times(cron, wall):
    assert firetimes.names_wall_times(cron) is wall


def test_the_week_view_draws_the_fires_the_scheduler_makes():
    drawn = _cron_fires("30 2 * * *", BERLIN, _z(24, 12), _z(26, 12))
    assert [datetime.fromisoformat(t).astimezone(UTC) for t in drawn] == [
        _z(25, 0, 30), _z(26, 1, 30)]
    assert _cron_fires("not a cron", BERLIN, _z(24, 12), _z(26, 12)) == []
    assert _cron_fires("30 2 * * *", "Mars/Olympus", _z(24, 12), _z(26, 12)) == []
