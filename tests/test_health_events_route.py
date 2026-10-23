"""The health EVENT STREAM, served — `GET /api/health/events` (R2764).

The documented way to inspect gate and fire events from inside a run was to read
`.control/health-events.jsonl`, and from inside a run that raises:

    PermissionError: [Errno 13] Permission denied:
        '/home/mark/routines/.control/health-events.jsonl'

The `.control` DIRECTORY is not listable for uid 1000 even though the file itself is 0644, so
the stream is unreachable for EVERY routine run on this instance — not a per-routine grant
problem, and nothing a run can fix for itself.

The web process already reads that exact file on every `/api/health/blocked` call
(`readmodels/health_stream`, the ONE parser), so the reachable path exists and is proven: what
was missing is a route that serves the EVENTS rather than one 7-day fold of fourteen of them.
`blocked_fleet` answers "what did the fleet owe and not deliver" — it drops every event outside
`BLOCKED_EVENTS` (a gate decision, a run start, a commit that landed) and collapses what remains
into one row per (event, subject). Neither is the raw record a reader is after when asking what
happened at 03:00.
"""

import json

import pytest

from conftest import authed_client, make_test_server
from rsched.readmodels import memo

TS = "2026-10-23T15:00:00+02:00"


def _write(tmp_path, rows):
    control = tmp_path / "routines" / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


@pytest.fixture
def streamed(tmp_path, make_routine):
    make_routine(slug="alpha")
    make_routine(slug="beta")
    _write(tmp_path, [
        {"ts": "2026-10-23T09:00:00+02:00", "event": "fire_refused", "routine": "alpha",
         "run_id": "", "detail": "the routine was still active"},
        {"ts": "2026-10-23T10:00:00+02:00", "event": "run_finished", "routine": "beta",
         "run_id": "beta:20261023-100000", "detail": "ok"},
        {"ts": TS, "event": "trigger_capped", "routine": "alpha", "run_id": "",
         "detail": "daily cap of 5 reached"},
    ])
    with authed_client(make_test_server(tmp_path)) as c:
        yield c


def test_the_stream_is_served_newest_first(streamed):
    """The whole stream, not one fold of it: `run_finished` is not blocked work and no other
    surface carries it as an EVENT, which is exactly why a run went to the file."""
    r = streamed.get("/api/health/events")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["event"] for e in body["events"]] == [
        "trigger_capped", "run_finished", "fire_refused"]
    assert body["events"][0]["ts"] == TS
    assert body["events"][0]["detail"] == "daily cap of 5 reached"
    assert body["total"] == 3


def test_the_answer_is_bounded_by_limit(streamed):
    """The live stream is ~280 KB and grows forever: a reader takes the newest N, never the
    file. `total` still counts what matched, so a bounded answer says what it left out."""
    body = streamed.get("/api/health/events?limit=1").json()
    assert [e["event"] for e in body["events"]] == ["trigger_capped"]
    assert body["total"] == 3
    assert body["limit"] == 1


def test_one_routine_or_one_event_can_be_asked_for(streamed):
    """The two filters a reader actually wants: this routine's events, or this event's."""
    body = streamed.get("/api/health/events?routine=alpha").json()
    assert [e["event"] for e in body["events"]] == ["trigger_capped", "fire_refused"]
    assert body["total"] == 2
    body = streamed.get("/api/health/events?event=run_finished").json()
    assert [e["routine"] for e in body["events"]] == ["beta"]
    assert body["total"] == 1
    # both together narrow, they do not widen
    assert streamed.get(
        "/api/health/events?routine=alpha&event=run_finished").json()["total"] == 0


def test_a_limit_outside_the_bar_is_refused_not_clamped_silently(streamed):
    """The same contract `/health/blocked` gives its window: a nonsense bound is a 422."""
    assert streamed.get("/api/health/events?limit=0").status_code == 422
    assert streamed.get("/api/health/events?limit=100000").status_code == 422


def test_an_instance_that_never_wrote_an_event_answers_empty(tmp_path, make_routine):
    """No file is a healthy new instance, not an error — the same reading `health_records`
    gives the folds."""
    make_routine(slug="alpha")
    memo.reset()
    with authed_client(make_test_server(tmp_path)) as c:
        body = c.get("/api/health/events").json()
        assert body == {"events": [], "total": 0, "limit": body["limit"]}
