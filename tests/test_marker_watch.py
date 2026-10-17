"""A migration deadline reaches a surface someone looks at (F702).

The policy had its enforcement (`test_policy`) and its 21-day lead time (F628) and neither
reached a reader: a warning on a PASSING test is folded into a summary xdist suppresses, and the
written window sits beside gate evidence nothing consumes. Both times a deadline came due it was
found by a blocked release — `pending_edits.py` on the morning it expired, and the five
settings-patterns markers by hand at an audit's orientation three days out.

So these tests assert the DELIVERY, in the two places that make a health event readable: the
machine-checked vocabulary and the console's blocked-work fold. The computation is
`test_policy.py`'s subject and the grammar is `migration_markers`'.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched import health_events, migration_markers
from rsched.daemon import marker_watch
from rsched.readmodels import health_stream

EVENTS = ("migration_due_soon", "migration_expired")


def _server(tmp_path: Path) -> SimpleNamespace:
    (tmp_path / ".control").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(routines_home=tmp_path)


def _rows(tmp_path: Path) -> list[dict]:
    stream = tmp_path / ".control" / health_events.HEALTH_EVENTS_FILE
    if not stream.exists():
        return []
    return [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines() if line]


def _marker_file(tmp_path: Path, expires: str) -> Path:
    path = tmp_path / "migrate_thing.py"
    path.write_text(f'"""A one-shot thing — MIGRATION(expires={expires}).\n"""\n\n'
                    "def run_migration(server):\n    return {}\n", encoding="utf-8")
    return path


def test_both_events_are_in_the_documented_vocabulary():
    """`health_events.py`'s header enum IS the vocabulary and is machine-checked both ways; an
    event missing from it is read by nobody — the `prompt_oversize_shrunk` failure."""
    doc = health_events.__doc__ or ""
    for event in EVENTS:
        assert f'"{event}"' in doc, f"{event} is emitted and not in the header enum"


def test_both_events_reach_the_blocked_work_surface():
    """`GET /api/health/blocked` folds exactly `BLOCKED_EVENTS`, and that fold is the only
    in-product reader. An unlisted event is written and read by nobody (the F678 shape)."""
    for event in EVENTS:
        assert event in health_stream.BLOCKED_EVENTS, f"{event} is invisible to the console"
        assert health_stream.BLOCKED_EVENTS[event].strip(), "a label a reader can act on"


def test_neither_event_is_repeat_suppressed_by_the_cooldown_table():
    """Nothing in BLOCKED_EVENTS may be named in `REPEAT_COOLDOWN_HOURS` — the blocked-fleet
    fold COUNTS those rows, so a suppressed one makes the fleet look less stuck than it is. The
    daily cadence is the watch's own latch instead."""
    for event in EVENTS:
        assert event not in health_events.REPEAT_COOLDOWN_HOURS


@pytest.mark.asyncio
async def test_an_expired_marker_is_filed_as_blocked_work(tmp_path, monkeypatch):
    """The condition that twice cost a release: the date has passed, so every gate on the host is
    red on files nobody touched. The row must name the file to delete."""
    marker = _marker_file(tmp_path, "2026-01-02")
    monkeypatch.setattr(marker_watch, "expired",
                        lambda today: migration_markers.expired(today, [marker]))
    monkeypatch.setattr(marker_watch, "due_soon",
                        lambda today: migration_markers.due_soon(today, paths=[marker]))
    await marker_watch.MarkerWatch(_server(tmp_path)).tick()
    rows = _rows(tmp_path)
    assert [r["event"] for r in rows] == ["migration_expired"]
    assert rows[0]["markers"] == 1 and rows[0]["expires"] == "2026-01-02"
    assert "migrate_thing.py" in rows[0]["detail"], "the row says WHAT to delete"
    assert not rows[0]["routine"] and not rows[0]["run_id"], "an instance fact, not a routine's"


@pytest.mark.asyncio
async def test_a_marker_inside_the_window_is_filed_with_the_days_left(tmp_path, monkeypatch):
    """The lead time, which is the whole point: filed while there is still room to act. The days
    left is the actionable part, so it must be in the row and not only the date."""
    soon = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
    marker = _marker_file(tmp_path, soon)
    monkeypatch.setattr(marker_watch, "expired", lambda today: [])
    monkeypatch.setattr(marker_watch, "due_soon",
                        lambda today: migration_markers.due_soon(today, paths=[marker]))
    await marker_watch.MarkerWatch(_server(tmp_path)).tick()
    rows = _rows(tmp_path)
    assert [r["event"] for r in rows] == ["migration_due_soon"]
    assert rows[0]["soonest"] == soon and rows[0]["markers"] == 1
    assert "3 day(s)" in rows[0]["detail"] and "migrate_thing.py" in rows[0]["detail"]


@pytest.mark.asyncio
async def test_the_condition_is_reported_once_a_day_not_once_a_tick(tmp_path, monkeypatch):
    """The daemon ticks every 5 s; a deadline a month out would write 17,280 rows a day and bury
    the stream it exists to make legible. One row a day while the condition holds."""
    marker = _marker_file(tmp_path, "2026-01-02")
    monkeypatch.setattr(marker_watch, "expired",
                        lambda today: migration_markers.expired(today, [marker]))
    monkeypatch.setattr(marker_watch, "due_soon", lambda today: [])
    watch = marker_watch.MarkerWatch(_server(tmp_path))
    for _ in range(4):
        await watch.tick()
    assert len(_rows(tmp_path)) == 1
    watch._checked = date(2000, 1, 1)       # the next day comes: the condition is re-reported
    await watch.tick()
    assert len(_rows(tmp_path)) == 2


@pytest.mark.asyncio
async def test_nothing_due_files_nothing(tmp_path, monkeypatch):
    marker = _marker_file(tmp_path, "2099-12-31")
    monkeypatch.setattr(marker_watch, "expired",
                        lambda today: migration_markers.expired(today, [marker]))
    monkeypatch.setattr(marker_watch, "due_soon",
                        lambda today: migration_markers.due_soon(today, paths=[marker]))
    await marker_watch.MarkerWatch(_server(tmp_path)).tick()
    assert _rows(tmp_path) == []


@pytest.mark.asyncio
async def test_a_raising_read_does_not_reach_the_tick(tmp_path, monkeypatch):
    """A check about a deadline must never be the thing that stops the scheduler."""
    def boom(today):
        raise OSError("the tree went away")
    monkeypatch.setattr(marker_watch, "expired", boom)
    await marker_watch.MarkerWatch(_server(tmp_path)).tick()      # no raise is the assertion
    assert _rows(tmp_path) == []


def test_the_watch_is_mounted_beside_the_other_off_tick_checks():
    """A watch nothing calls is prose. It runs on the off-tick path (`_off_tick`), never inside
    the tick, like the backup check it copies: a file read must not hold a fire behind it."""
    source = Path(marker_watch.__file__).with_name("scheduler.py").read_text(encoding="utf-8")
    assert "MarkerWatch(server)" in source
    assert '_off_tick("marker-watch", self.markers.tick)' in source
