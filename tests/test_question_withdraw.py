"""The asking routine's own two dispositions — `POST /questions/{qid}/retract` and
`POST /questions/{qid}/supersede` (F943)."""

import pytest

from conftest import authed_client, make_test_server, mk_run
from rsched import withdraw
from rsched.engine import inbox
from rsched.paths import atomic_write_json, read_json
from rsched.web import decisions_window


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _pending(tmp_path, qid, question="Build the thing?", **extra):
    """One open DEFERRED record, the shape `ask_user` files."""
    path = tmp_path / "routines" / "apir" / "questions" / "pending" / f"{qid}.json"
    atomic_write_json(path, {"qid": qid, "question": question, "options": [],
                             "asked": "20260707", "mode": "deferred", **extra})
    return path


def _archived(tmp_path, qid):
    return read_json(tmp_path / "routines" / "apir" / "questions" / "answered" / f"{qid}.json")


def _row(client, qid, settled=20):
    items = client.get(f"/api/questions?settled={settled}").json()["items"]
    return next((q for q in items if q["qid"] == qid), None)


def test_a_retraction_takes_the_card_back_and_states_the_reason(client):
    """The defect: a routine disproved its own premise an hour after asking and could only
    file a SECOND card correcting the first. One call withdraws the card, and the reason
    stands where the card was."""
    c, tmp = client
    pending = _pending(tmp, "q-w1")

    r = c.post("/api/questions/q-w1/retract",
               json={"reason": "the log line it rested on was my own test fixture"})
    assert r.status_code == 200
    body = r.json()
    assert body["withdrawn"] is True
    assert body["retracted"]["reason"] == "the log line it rested on was my own test fixture"
    assert body["retracted"]["ts"]

    assert not pending.exists()                      # off the page
    rec = _archived(tmp, "q-w1")                     # and kept as history
    assert rec["withdrawn"] is True
    assert rec["question"] == "Build the thing?"
    assert rec["retracted"]["reason"].startswith("the log line")
    # NO answer: nobody spoke. A run told its question was answered would act on words that
    # do not exist, and the receipt card must not offer to revise them.
    assert "answered" not in rec and "answer" not in rec


def test_a_withdrawal_writes_no_answer_and_reaches_no_run(client):
    """The whole distinction from a decline, which DOES write the ordinary answer payload."""
    c, tmp = client
    _pending(tmp, "q-w2")
    assert c.post("/api/questions/q-w2/retract", json={}).status_code == 200

    routine_dir = tmp / "routines" / "apir"
    assert not (routine_dir / "inbox" / "answer-q-w2.json").exists()
    assert inbox.collect_deferred_answers(routine_dir, tmp / "run" / "consumed") == []
    assert inbox.open_questions(routine_dir) == []


def test_a_supersession_names_the_card_that_is_live_now(client):
    """One live card per matter: the old one keeps its place in history stamped with the
    carrier, exactly as a folded report row names the report that took it over."""
    c, tmp = client
    _pending(tmp, "q-old", question="Raise the budget to 600?")
    _pending(tmp, "q-new", question="Raise the budget to 450?")

    r = c.post("/api/questions/q-old/supersede",
               json={"by": "q-new", "reason": "450 is what the measurement supports"})
    assert r.status_code == 200
    fold = r.json()["superseded_by"]
    assert fold["by"] == "q-new"
    assert fold["reason"] == "450 is what the measurement supports"

    rec = _archived(tmp, "q-old")
    assert rec["withdrawn"] is True and rec["superseded_by"]["by"] == "q-new"
    assert withdraw.carrier_of(rec) == "q-new"
    # the carrier is untouched and still open — it is the live card
    assert (tmp / "routines" / "apir" / "questions" / "pending" / "q-new.json").exists()


def test_a_supersession_refuses_a_carrier_that_is_not_an_open_card(client):
    """A retraction may leave the matter with no card; a supersession must not. A carrier
    that is settled, withdrawn or imaginary would leave the operator with neither."""
    c, tmp = client
    _pending(tmp, "q-s1")
    r = c.post("/api/questions/q-s1/supersede", json={"by": "q-ghost"})
    assert r.status_code == 400
    assert "q-ghost" in r.json()["detail"]
    # refused means refused: the card is still open
    assert (tmp / "routines" / "apir" / "questions" / "pending" / "q-s1.json").exists()
    assert _archived(tmp, "q-s1") is None

    assert c.post("/api/questions/q-s1/supersede", json={"by": "q-s1"}).status_code == 400
    assert c.post("/api/questions/q-s1/supersede", json={}).status_code == 422


def test_an_unknown_or_audit_record_cannot_be_withdrawn(client):
    """The floor the snooze, defer and decline routes stand on: an audit decision lives in
    the self-audit report, not as a record, so there is nothing to withdraw."""
    c, _ = client
    assert c.post("/api/questions/q-nope/retract", json={}).status_code == 404
    assert c.post("/api/questions/audit:D1/retract", json={}).status_code == 404
    assert c.post("/api/questions/audit:D1/supersede", json={"by": "q-x"}).status_code == 404


def test_a_queued_answer_wins_over_a_late_withdrawal(client):
    """The operator has spoken and no run has read him yet. Withdrawing would delete the one
    copy of his words, so the route refuses and the asking run reads the answer instead."""
    c, tmp = client
    _pending(tmp, "q-w3")
    assert c.post("/api/questions/q-w3/answer", json={"text": "yes, do it"}).status_code == 200

    assert c.post("/api/questions/q-w3/retract", json={}).status_code == 409
    assert c.post("/api/questions/q-w3/supersede", json={"by": "q-w3b"}).status_code == 409
    ans = read_json(tmp / "routines" / "apir" / "inbox" / "answer-q-w3.json")
    assert ans["text"] == "yes, do it"               # his own answer is intact
    assert _archived(tmp, "q-w3") is None            # and no withdrawal row was written


def test_a_blocking_card_is_deferred_not_withdrawn(client):
    """It parks a live run, and that run is the asker: it sits in `interact._await_reply`, so
    withdrawing the card would leave it waiting for one that no longer exists."""
    c, tmp = client
    routines = tmp / "routines"
    mk_run(routines / "apir", "20260708-110000", "waiting_user", turn=2,
           question={"qid": "q-blk", "question": "Ship it?", "options": []})
    _pending(tmp, "q-blk", question="Ship it?", mode="blocking", asked="20260708-110000")

    r = c.post("/api/questions/q-blk/retract", json={})
    assert r.status_code == 400
    assert "defer" in r.json()["detail"]
    assert (routines / "apir" / "questions" / "pending" / "q-blk.json").exists()


def test_a_withdrawn_card_reads_as_history_on_the_page(client):
    """What the operator sees: the row leaves the open set and arrives in the settled
    archive carrying WHY — the three fields the page renders in place of an answer."""
    c, tmp = client
    _pending(tmp, "q-w4", question="Rebuild the exporter?")
    c.post("/api/questions/q-w4/retract", json={"reason": "the exporter was already fixed"})

    assert _row(c, "q-w4", settled=0) is None        # not among the open rows
    row = _row(c, "q-w4")
    assert row is not None and row["settled"] is True
    assert row["withdrawn"] is True
    assert row["retracted"]["reason"] == "the exporter was already fixed"
    # the wire cuts a settled row to SETTLED_FIELDS, so the three keys have to be declared
    # there or the history card is a decision with no account of what became of it
    assert set(row) <= decisions_window.SETTLED_FIELDS
    for key in (withdraw.WITHDRAWN, withdraw.RETRACTED, withdraw.SUPERSEDED_BY):
        assert key in decisions_window.SETTLED_FIELDS


def test_a_withdrawn_card_is_never_re_queued_by_a_revision(client):
    """`revise_answer` re-queues a consumed decision from its archive so an amendment reaches
    the next run. A withdrawal row is not an answer, and re-queueing it would put the
    withdrawn card back on the page as open."""
    c, tmp = client
    _pending(tmp, "q-w5")
    assert c.post("/api/questions/q-w5/retract", json={}).status_code == 200

    assert c.post("/api/questions/q-w5/revise", json={"text": "actually yes"}).status_code == 404
    routine_dir = tmp / "routines" / "apir"
    assert inbox.open_questions(routine_dir) == []
    with pytest.raises(LookupError):
        inbox.revise_answer(routine_dir, "q-w5", "actually yes")


def test_a_late_answer_file_cannot_resurrect_a_withdrawn_question(client):
    """The other half of the same hole: `_reanswered` recovers an ask from the archive, so an
    answer file landing after the withdrawal would be DELIVERED and would overwrite the
    withdrawal row with `answered: True` — losing the record of what became of the card."""
    c, tmp = client
    _pending(tmp, "q-w6")
    assert c.post("/api/questions/q-w6/retract", json={"reason": "premise gone"}).status_code == 200

    routine_dir = tmp / "routines" / "apir"
    atomic_write_json(routine_dir / "inbox" / "answer-q-w6.json",
                      {"qid": "q-w6", "text": "go ahead", "source": "web"})
    assert inbox.collect_deferred_answers(routine_dir, tmp / "run" / "consumed") == []
    rec = _archived(tmp, "q-w6")
    assert rec["withdrawn"] is True and "answered" not in rec
    assert rec["retracted"]["reason"] == "premise gone"
