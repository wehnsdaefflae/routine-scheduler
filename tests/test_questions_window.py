"""`GET /api/questions` is BOUNDED (F810, `web/decisions_window.py`).

Measured on this instance on 2026-11-05: the response carried every routine's and every
conversation's settled archive — 389 rows, 1.08 MB of the ~1.1 MB — and `static/app.js`
refetches it every 30 s per open tab for a badge that counts none of them. So: open decisions
whole, always; settled ones only as the window a caller asks for (none by default), newest
first across the whole instance, projected to the fields their receipt reads, with the total
said so the page can offer more instead of silently stopping.
"""

import pytest

from conftest import authed_client, make_test_server
from rsched import answerfile
from rsched.paths import atomic_write_json
from rsched.web import decisions_window


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="alpha")
    make_routine(slug="beta")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _pending(tmp, slug, qid, **extra):
    atomic_write_json(tmp / "routines" / slug / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": f"Open {qid}?", "options": ["yes", "no"],
                       "asked": "20261101", "mode": "deferred", **extra})


def _archived(tmp, slug, qid, consumed):
    """A settled record exactly as `_archive_answer` leaves it: the ask's machinery included."""
    atomic_write_json(tmp / "routines" / slug / "questions" / "answered" / f"{qid}.json",
                      {"qid": qid, "question": f"Settled {qid}?", "options": ["a", "b"],
                       "default": "a", "batch": "b-1", "class": "closing",
                       "request": ["util:x"], "asked": "20261001", "mode": "deferred",
                       "answered": True, "answer": "a", "answer_source": "web",
                       "answered_ts": consumed, "consumed": consumed,
                       "decision": "declined", "reason": "not now"})


def _get(c, **params):
    r = c.get("/api/questions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_default_read_carries_every_open_decision_and_no_history(client):
    """The badge's 30 s read: what waits, whole — and none of the archive."""
    c, tmp = client
    _pending(tmp, "alpha", "q-open")
    _archived(tmp, "alpha", "q-old", "2026-10-01T08:00:00+02:00")
    page = _get(c)
    assert [q["qid"] for q in page["items"]] == ["q-open"]
    assert page["items"][0]["options"] == ["yes", "no"], "an OPEN ask is never projected"
    assert page["settled_total"] == 1, "the archive is counted even when none of it is sent"
    assert "?settled=" in page["settled_note"], "a routine reading the API is told of the cut"
    assert "settled_note" not in _get(c, settled=1), "nothing left out, nothing to say"


def test_an_answer_still_in_flight_is_always_sent(client):
    """Answered but not yet taken by a run: it is not history, so no window cuts it."""
    c, tmp = client
    _pending(tmp, "alpha", "q-flight")
    answerfile.write(tmp / "routines" / "alpha", "q-flight", {"qid": "q-flight", "text": "yes"})
    row = next(q for q in _get(c)["items"] if q["qid"] == "q-flight")
    assert row["answered"] is True and row["answer"] == "yes"


def test_the_window_is_the_newest_across_the_instance_as_instants(client):
    """Newest first by when a run took the answer, across routines — compared as INSTANTS:
    the second stamp is the later moment although it sorts lower as text (F771's DST trap)."""
    c, tmp = client
    _archived(tmp, "alpha", "q-1", "2026-10-25T02:40:00+02:00")
    _archived(tmp, "beta", "q-2", "2026-10-25T02:07:00+01:00")   # 27 min LATER
    _archived(tmp, "alpha", "q-3", "2026-09-01T08:00:00+02:00")
    page = _get(c, settled=2)
    assert [q["qid"] for q in page["items"]] == ["q-2", "q-1"]
    assert page["settled_total"] == 3


def test_a_settled_row_carries_only_what_its_receipt_reads(client):
    c, tmp = client
    _archived(tmp, "alpha", "q-s", "2026-10-01T08:00:00+02:00")
    row = _get(c, settled=5)["items"][0]
    assert set(row) <= decisions_window.SETTLED_FIELDS
    for gone in ("options", "default", "batch", "class", "request", "answered_ts"):
        assert gone not in row, gone
    assert row["question"] == "Settled q-s?" and row["answer"] == "a"
    assert row["decision"] == "declined" and row["reason"] == "not now"
    assert row["settled"] is True and row["answered"] is True


def test_routine_narrows_every_row_to_its_own(client):
    """An API caller reading its OWN history (self-audit at orient) gets it whole, not the
    fleet's newest few."""
    c, tmp = client
    _pending(tmp, "alpha", "q-a")
    _pending(tmp, "beta", "q-b")
    _archived(tmp, "beta", "q-bs", "2026-10-01T08:00:00+02:00")
    _archived(tmp, "alpha", "q-as", "2026-10-02T08:00:00+02:00")
    page = _get(c, routine="beta", settled=25)
    assert [q["qid"] for q in page["items"]] == ["q-b", "q-bs"]
    assert page["settled_total"] == 1


def test_the_window_is_bounded(client):
    c, _tmp = client
    over = decisions_window.SETTLED_MAX + 1
    assert c.get("/api/questions", params={"settled": over}).status_code == 422
    assert c.get("/api/questions", params={"settled": -1}).status_code == 422
