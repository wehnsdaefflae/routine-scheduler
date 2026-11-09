"""Every answered decision says WHO reads it and WHEN — or that nobody will (2026-11-09).

Two conversation answers sat unread for seven weeks while the Decisions page called them
"answered · queued", exactly as it called answers a run would read that night. `pickup` is the
difference, and `never` is the one state that hands a decision back to the operator — so it
must be stated only when it is KNOWN, never because a clock had not been computed yet.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from conftest import authed_client, make_test_server
from rsched.paths import atomic_write_json
from rsched.readmodels.answer_pickup import routine_pickup

AT = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)


def _info(*, cron="", enabled=True, retired=False, triggers=()):
    cfg = SimpleNamespace(cron=cron, triggers=list(triggers))
    return SimpleNamespace(cfg=cfg, retired=retired, fireable=enabled and not retired)


@pytest.mark.parametrize(("info", "lane", "own", "lane_at", "one_shot", "want"), [
    (_info(enabled=False), None, None, None, None, "never"),
    (_info(retired=True), None, None, None, None, "never"),
    (_info(cron="0 3 * * *"), None, AT, None, None, "scheduled"),
    (_info(), {"id": "g", "name": "L"}, None, AT, None, "scheduled"),
    (_info(), None, None, None, AT, "scheduled"),
    (_info(triggers=[{"kind": "report"}]), None, None, None, None, "trigger"),
    (_info(), {"id": "g", "name": "L", "paused": True}, None, None, None, "never"),
    (_info(), None, None, None, None, "never"),
])
def test_the_reader_of_a_routine_answer(info, lane, own, lane_at, one_shot, want):
    got = routine_pickup(info, lane, own=own, lane_at=lane_at, one_shot=one_shot)
    assert got["state"] == want
    assert ("why" in got) == (want == "never")


def test_a_clock_the_scheduler_has_not_computed_is_unknown_not_never():
    """At boot, and in a scheduler-less app, `next_fires` is empty. A routine WITH a cron (or
    in an unpaused lane) is then unknown: calling it undelivered would hand the operator a
    decision a run reads tonight."""
    assert routine_pickup(_info(cron="0 3 * * *"), None, own=None, lane_at=None,
                          one_shot=None) is None
    assert routine_pickup(_info(), {"id": "g", "name": "L"}, own=None, lane_at=None,
                          one_shot=None) is None
    assert routine_pickup(None, None, own=None, lane_at=None, one_shot=None) is None


def test_an_earlier_one_shot_beats_the_clock():
    early = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    got = routine_pickup(_info(cron="0 3 * * *"), None, own=AT, lane_at=None, one_shot=early)
    assert got == {"state": "scheduled", "at": early.isoformat()}


@pytest.fixture
def client(tmp_path, make_routine):
    d = make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, d


def _answered(d, qid, *, cron=None):
    if cron is not None:
        import yaml
        cfg = yaml.safe_load((d / "routine.yaml").read_text())
        cfg["schedule"]["cron"] = cron
        (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    atomic_write_json(d / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": "Go?", "options": [], "asked": "20261109",
                       "mode": "deferred"})
    atomic_write_json(d / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": "yes", "source": "web", "ts": "t"})


def _row(c, qid):
    return next(q for q in c.get("/api/questions").json()["items"] if q["qid"] == qid)


def test_the_route_stamps_scheduled_and_never(client):
    c, d = client
    _answered(d, "q-p1")
    assert "pickup" not in _row(c, "q-p1")        # a cron, uncomputed: unknown
    c.app.state.scheduler.next_fires["apir"] = AT
    assert _row(c, "q-p1")["pickup"] == {"state": "scheduled", "at": AT.isoformat()}
    _answered(d, "q-p2", cron="")
    c.app.state.scheduler.next_fires.pop("apir")
    assert _row(c, "q-p2")["pickup"]["state"] == "never"


def test_an_open_question_carries_no_pickup(client):
    c, d = client
    atomic_write_json(d / "questions" / "pending" / "q-open.json",
                      {"qid": "q-open", "question": "?", "options": [], "asked": "1",
                       "mode": "deferred"})
    assert "pickup" not in _row(c, "q-open")
