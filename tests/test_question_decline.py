"""The Decisions page's first-class NO — `POST /questions/{qid}/decline` (R2197).

A decision could be answered, snoozed or deferred, and none of those is a refusal: the answer
route closes an item only by saying something, a snooze leaves it open in every run's digest,
and a defer releases a parked run onto its default with the record still open. So an operator
who had read a proposal and did not want it could only type prose — and the routine that asked
had to parse a sentence to find out.

What is asserted here is the three halves of "done": the click files a refusal (the route), the
item leaves the open set AS DECLINED rather than as prose (the read model), and the asking run
is handed `declined` as a VALUE (the engine's answer pair + the digest line). The last one is
the half that has no UI: without `decline.carry` the value is dropped for every question that
is not an access request, which is the whole class this verb is for.

Its own file because `tests/test_api.py` is at its size ceiling (tests/size_ratchet.json).
"""

import pytest

from conftest import authed_client, make_test_server, mk_run
from rsched import decline
from rsched.engine import inbox
from rsched.paths import atomic_write_json, read_json


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


def test_decline_files_a_refusal_and_closes_the_item(client):
    """One click: the answer payload lands with `decision: "declined"`, and the item leaves
    the open set as DECLINED — not as whatever prose an answer would have carried."""
    c, tmp = client
    _pending(tmp, "q-n1")

    r = c.post("/api/questions/q-n1/decline", json={"reason": "we do not want it"})
    assert r.status_code == 200
    body = r.json()
    assert body["declined"] is True and body["decision"] == "declined"

    # the SAME answer payload shape the answer route writes — so `collect_deferred_answers`
    # drains it unchanged and no new consumption path exists
    ans = read_json(tmp / "routines" / "apir" / "inbox" / "answer-q-n1.json")
    assert ans["decision"] == "declined"
    assert ans["qid"] == "q-n1" and ans["source"] == "web" and ans["ts"]
    assert ans["reason"] == "we do not want it"
    assert ans["intermediate"] is False
    assert decline.PHRASE in ans["text"] and "we do not want it" in ans["text"]

    # …and the open set says it as a VALUE, which is what makes the card a refusal rather
    # than an answer that happens to read like one
    row = next(q for q in c.get("/api/questions").json() if q["qid"] == "q-n1")
    assert row["answered"] is True
    assert row["decision"] == "declined"
    assert row["reason"] == "we do not want it"


def test_decline_without_a_reason_still_states_the_refusal(client):
    """The reason is optional: the phrase alone has to be unambiguous, because it is what
    every surface renders when the operator just clicks."""
    c, tmp = client
    _pending(tmp, "q-n2")
    assert c.post("/api/questions/q-n2/decline", json={}).status_code == 200
    ans = read_json(tmp / "routines" / "apir" / "inbox" / "answer-q-n2.json")
    assert ans["decision"] == "declined" and ans["reason"] == ""
    assert ans["text"] == f"{decline.PHRASE}."


def test_decline_refuses_an_access_request_and_names_the_typed_decision(client):
    """An access request's "no" is `deny_now`/`deny_forever` — the values the engine reads to
    deny the grant, and the only ones that settle a blocking request's wait
    (`engine/interact._held_not_settled`). A question-scoped word there would be held as
    chatter while the run kept waiting, so the route refuses and says which word to use."""
    c, tmp = client
    _pending(tmp, "q-req", question="May I write ~/project?", request=["fs-write:~/project"])
    r = c.post("/api/questions/q-req/decline", json={})
    assert r.status_code == 400
    assert "deny_now" in r.json()["detail"]
    assert not (tmp / "routines" / "apir" / "inbox" / "answer-q-req.json").exists()


def test_decline_404s_an_unknown_record_and_409s_a_queued_answer(client):
    c, tmp = client
    assert c.post("/api/questions/q-nope/decline", json={}).status_code == 404
    # an AUDIT decision lives in the report, not as a record — it can no more be declined
    # than it can be snoozed or deferred
    assert c.post("/api/questions/audit:D1/decline", json={}).status_code == 404
    _pending(tmp, "q-n3")
    assert c.post("/api/questions/q-n3/answer", json={"text": "yes, do it"}).status_code == 200
    assert c.post("/api/questions/q-n3/decline", json={}).status_code == 409
    # the operator's own answer is intact — a late decline click never overwrites it
    ans = read_json(tmp / "routines" / "apir" / "inbox" / "answer-q-n3.json")
    assert ans["text"] == "yes, do it" and "decision" not in ans


def test_declining_a_patch_carrying_decision_records_it_as_not_applied(client):
    """A declined proposal is the clearest case of a `config_patch` that was NOT applied, and
    the record has to say so in the words the asking run already reads (R2778)."""
    c, tmp = client
    _pending(tmp, "q-n4", config_patch={"routine": "apir", "budgets": {"max_turns": 600}})
    assert c.post("/api/questions/q-n4/decline", json={}).status_code == 200
    ans = read_json(tmp / "routines" / "apir" / "inbox" / "answer-q-n4.json")
    assert ans["config_patch_applied"] is False
    assert ans["config_patch_target"] == "apir"


def test_a_blocking_record_can_be_declined_too(client):
    """A blocking ask parks a live run; the run is owed an answer, and "no" is one. The
    refusal lands as an ordinary answer file, which is what the wait consumes."""
    c, tmp = client
    routines = tmp / "routines"
    mk_run(routines / "apir", "20260708-110000", "waiting_user", turn=2,
           question={"qid": "q-blk", "question": "Ship it?", "options": []})
    _pending(tmp, "q-blk", question="Ship it?", mode="blocking", asked="20260708-110000")
    r = c.post("/api/questions/q-blk/decline", json={"reason": "not this week"})
    assert r.status_code == 200 and r.json()["mode"] == "blocking"
    ans = read_json(routines / "apir" / "inbox" / "answer-q-blk.json")
    assert ans["decision"] == "declined"
    # the head word of the phrase is an explicit decline, so a blocking APPROVAL record is
    # SETTLED by it rather than held as chatter (engine/interact._DECLINE_WORDS)
    assert ans["text"].split()[0].strip(".,") == "declined"


def test_declining_a_report_born_decision_tells_the_routine_that_owes_the_work(client,
                                                                               make_routine):
    """A report carrying `options` routes WORK to one routine and asks the OPERATOR the
    question (F621). The refusal settles the asking record — but the target is who would have
    acted on it, and it has no way to learn an answer filed into someone else's inbox. A "no"
    is the one outcome it must not miss."""
    c, tmp = client
    make_routine(slug="owner")
    _pending(tmp, "q-rep", question="Rebuild the exporter?",
             report="R99", report_target="owner")
    assert c.post("/api/questions/q-rep/decline",
                  json={"reason": "not worth it"}).status_code == 200
    queued = list((tmp / "routines" / "owner" / "inbox").glob("msg-*.json"))
    assert len(queued) == 1
    text = read_json(queued[0])["text"]
    assert "R99" in text and "declined" in text and "not worth it" in text


def test_a_consumed_decline_is_still_a_decline_on_the_page(client, tmp_path):
    """The SECOND path, and the one that is easy to miss: once a run consumes the answer, the
    inbox file and the pending record are gone and the settled card is rebuilt from the
    durable archive (`questions/answered/`, F525) — which does NOT go through the row
    derivation that reads the inbox file. A verdict carried on only one of the two paths makes
    a declined decision read as an ordinary answer the moment the run that asked acts on it."""
    c, tmp = client
    _pending(tmp, "q-n6", question="Rebuild the exporter?")
    assert c.post("/api/questions/q-n6/decline",
                  json={"reason": "no budget"}).status_code == 200
    rd = tmp / "routines" / "apir"
    inbox.collect_deferred_answers(rd, tmp_path / "consumed")      # the asking run boots
    assert not (rd / "inbox" / "answer-q-n6.json").exists()

    archived = read_json(rd / "questions" / "answered" / "q-n6.json")
    assert archived["decision"] == "declined" and archived["reason"] == "no budget"
    row = next(q for q in c.get("/api/questions").json() if q["qid"] == "q-n6")
    assert row["settled"] is True and row["decision"] == "declined"
    assert row["reason"] == "no budget"


def test_the_asking_run_reads_declined_as_a_value(client, tmp_path):
    """The half with no UI: `collect_deferred_answers` carries the verdict onto the answer
    pair both delivery seams read, and the digest line says REFUSED in words a model cannot
    mistake for a go-ahead. Without the carry, `decision` is kept only for access requests —
    so a declined proposal reached the run as prose and nothing else."""
    c, tmp = client
    _pending(tmp, "q-n5", question="Build the thing?")
    assert c.post("/api/questions/q-n5/decline",
                  json={"reason": "out of scope"}).status_code == 200

    rd = tmp / "routines" / "apir"
    pairs = inbox.collect_deferred_answers(rd, tmp_path / "consumed")
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["question"] == "Build the thing?"
    assert pair["decision"] == "declined" and pair["reason"] == "out of scope"
    line = decline.digest_line(pair)
    assert "declined" in line and "REFUSED" in line and "out of scope" in line
    assert decline.digest_suffix(pair).startswith("\n  ")
    # an ordinary answer gains nothing — no reader learns to expect the field
    assert decline.digest_line({"decision": "allow_now"}) == ""
    assert decline.carry({}, {"text": "sure"}) == {}
