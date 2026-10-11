"""A STANDING CONDITION is filed once per window; an INCIDENT is filed every time.

`cost_trend_degraded` is read at RUN END, and a run ends once per finish — so a resumed run
filed one row per resume: 26 rows over 5h36m from llmsectest-weekday:20261008-040001, 22 from
the next day's, 173 of the live stream's 1,289 rows for an event only 80 runs had produced.
An operator reading that sees a fleet problem where there are a handful of readings.

Deduping the payload cannot collapse them — each finish appends its own usage row, which shifts
the window the trend is read over, so those 22 rows carried six distinct `detail` strings. The
suppression is therefore on the CLOCK, per (event, routine), in the one writer, so no call site
can forget it; and it governs only the events named in `REPEAT_COOLDOWN_HOURS`, because a
failure or a refusal that happened twice is two facts.
"""

from __future__ import annotations

import json

from conftest import finish
from helpers import health_events
from rsched.engine.runtime import run_routine
from rsched.health_events import REPEAT_COOLDOWN_HOURS, log_health_event
from rsched.readmodels.health_stream import BLOCKED_EVENTS
from test_loop import TS, _server, probe


def _home(tmp_path):
    (tmp_path / ".control").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _rows(home, **match):
    return health_events(home, **match)


def test_a_standing_condition_is_filed_once_inside_its_window(tmp_path):
    """The resumed-run case, at the writer: the second reading of the same condition for the
    same routine inside the window is dropped, whatever its detail says."""
    home = _home(tmp_path)
    for n in range(4):
        log_health_event(home, "cost_trend_degraded", routine="trender", run_id="trender:r1",
                         detail=f"reading {n}", window=5)

    got = _rows(home, event="cost_trend_degraded")
    assert len(got) == 1, f"one row per window, got {len(got)}"
    assert got[0]["detail"] == "reading 0"          # the FIRST reading is the one kept


def test_the_cooldown_is_per_routine(tmp_path):
    """One routine's standing condition must never silence another's — the suppression key is
    (event, routine), not the event alone."""
    home = _home(tmp_path)
    for slug in ("alpha", "beta", "gamma"):
        log_health_event(home, "cost_trend_degraded", routine=slug, run_id=f"{slug}:r1",
                         detail="ballooned")
        log_health_event(home, "cost_trend_degraded", routine=slug, run_id=f"{slug}:r1",
                         detail="ballooned again")

    assert len(_rows(home, event="cost_trend_degraded")) == 3
    assert {r["routine"] for r in _rows(home, event="cost_trend_degraded")} == {
        "alpha", "beta", "gamma"}


def test_an_incident_is_never_governed_by_a_cooldown(tmp_path):
    """Two failures are two facts. Only a reading of a standing condition repeats itself for
    no reason, so only those are named in REPEAT_COOLDOWN_HOURS."""
    home = _home(tmp_path)
    for n in range(3):
        log_health_event(home, "run_failed", routine="flaky", run_id=f"flaky:r{n}",
                         detail=f"attempt {n}")

    assert len(_rows(home, event="run_failed")) == 3


def test_no_blocked_event_is_ever_cooled_down(tmp_path):
    """The console's blocked-fleet fold counts rows: suppressing one of those events would
    make the fleet look less stuck than it is. Nothing in BLOCKED_EVENTS may be governed."""
    assert not (set(REPEAT_COOLDOWN_HOURS) & set(BLOCKED_EVENTS)), (
        "a blocked-fleet event must not be cooled down — blocked_fleet() reports its count")


def test_the_window_expires_so_the_next_reading_is_reported(tmp_path):
    """A cooldown that never ends is a mute. Once the window has passed, the condition is
    reported again — that is what separates it from a per-run latch."""
    home = _home(tmp_path)
    hours = REPEAT_COOLDOWN_HOURS["cost_trend_degraded"]
    stale = {"ts": "2026-01-01T00:00:00+01:00", "event": "cost_trend_degraded",
             "routine": "trender", "run_id": "trender:old", "detail": "last year"}
    path = home / ".control" / "health-events.jsonl"
    path.write_text(json.dumps(stale) + "\n", encoding="utf-8")

    log_health_event(home, "cost_trend_degraded", routine="trender", run_id="trender:new",
                     detail="today")

    got = _rows(home, event="cost_trend_degraded")
    assert len(got) == 2 and got[-1]["detail"] == "today", (
        f"a reading {hours} h after the last must be filed, not swallowed")


def test_an_unstamped_row_does_not_date_the_window(tmp_path):
    """A row whose ts names no instant dates nothing: the search keeps looking rather than
    treating it as either fresh or stale."""
    home = _home(tmp_path)
    path = home / ".control" / "health-events.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in [
        {"ts": "2026-01-01T00:00:00+01:00", "event": "cost_trend_degraded",
         "routine": "trender", "run_id": "o", "detail": "last year"},
        {"ts": "not a date", "event": "cost_trend_degraded", "routine": "trender",
         "run_id": "x", "detail": "unstamped"},
    ]), encoding="utf-8")

    log_health_event(home, "cost_trend_degraded", routine="trender", run_id="n",
                     detail="today")

    assert _rows(home, event="cost_trend_degraded")[-1]["detail"] == "today"


def _flagged_routine(make_routine, slug="trender"):
    """A routine whose usage stream already shows the regression the trend flag fires on —
    the fixture test_loop.py uses for the run-end cost flag."""
    d = make_routine(slug=slug)
    stream = d.parent / ".control" / "workflow-usage.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    rows = ([{"routine": slug, "depth": 0, "status": "ok", "turns": 10, "tokens": 10_000}] * 5
            + [{"routine": slug, "depth": 0, "status": "ok", "turns": 100,
                "tokens": 500_000}] * 4)
    stream.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


def test_two_finishes_of_one_degraded_routine_file_one_row(make_routine, scripted):
    """End to end, the shape that produced 26 rows from one run: the condition is read again
    at every finish, and every reading after the first inside the window is dropped."""
    d = _flagged_routine(make_routine)
    scripted([probe(), finish(), probe(), finish()])
    server = _server(d)

    assert run_routine(d, server, run_ts=TS)[0] == "ok"
    assert run_routine(d, server, run_ts="20260102-030405")[0] == "ok"

    flagged = _rows(d.parent, event="cost_trend_degraded")
    assert len(flagged) == 1, (
        f"the standing condition belongs in the stream once per window, got {len(flagged)}")
    assert flagged[0]["routine"] == "trender" and flagged[0]["window"] == 5
