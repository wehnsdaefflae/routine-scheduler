"""An answered AUDIT decision ACKNOWLEDGES the answer — it does not just disappear.

F529's second half, in the operator's own words: *"he must get an acknowledgement that an
answer landed — the absence of one is precisely why he answered twice."* The evidence is
concrete: he spent three messages and two clicks re-answering settled questions, and wrote
*"i answered your questions. for the second time btw. did my answers get lost the first
time?!"*

What he saw. Answering an audit decision writes the durable marker and queues his words
(`web/api_audit.write_feedback`), and `decisions_read._audit_decisions_fresh` then `continue`s
past that decision — so the card VANISHED. No receipt, nothing saying his words had been
recorded, nothing to re-read. Every OTHER decision shape on that page keeps a settled card
with the answer in it (F525, `questions-taxonomy.GROUPS`'s "Settled" band, rendered by
`views/questions.js`'s `q.answered` branch); the audit decisions — the ones he answers most —
were the only shape that silently emptied.

So an answered audit decision stays on the page as an ANSWERED row: `answered: True` plus the
words he chose, read from the two records his click actually wrote (the queued message while it
is queued, the durable marker once a run has consumed it). It is not a new ask: every counter
on that page and in the push sender filters `answered` out (`views/questions.js`'s `open`,
`web/push.pending`), so the acknowledgement costs no badge and asks nothing of him.
"""
from __future__ import annotations

from rsched.paths import atomic_write_json, read_json
from rsched.readmodels import memo

REPORT = {
    "generated": "2026-10-24T00:15:00+02:00",
    "findings": [],
    "decisions": [
        {"id": "D157", "title": "Drain audit feedback mid-run?", "detail": "context",
         "status": "open", "options": ["A — drain it", "B — name it"], "resolution": ""},
        {"id": "D158", "title": "Acknowledge an answer?", "detail": "context",
         "status": "open", "options": ["yes", "no"], "resolution": ""},
    ],
}


def _audit(tmp, report=None):
    rdir = tmp / "routines" / "self-audit"
    (rdir / "audit").mkdir(parents=True, exist_ok=True)
    (rdir / "inbox").mkdir(parents=True, exist_ok=True)
    atomic_write_json(rdir / "audit" / "report.json", report or REPORT)
    memo.reset()
    return rdir


def _metas(c):
    return {q["qid"]: q for q in c.get("/api/questions").json() if q.get("meta")}


def test_an_answered_audit_decision_keeps_a_receipt_carrying_his_words(api_client):
    """The acknowledgement. He answers D157; the row stays, says it is answered, and quotes
    what he chose — so he can see his answer landed instead of guessing it was lost."""
    c, _tmp = _audit_client(api_client)
    assert sorted(_metas(c)) == ["audit:D157", "audit:D158"]

    assert c.post("/api/questions/audit:D157/answer",
                  json={"text": "B — name it"}).status_code == 200
    memo.reset()

    rows = _metas(c)
    row = rows.get("audit:D157")
    assert row is not None, "the answered decision vanished — no acknowledgement at all"
    assert row["answered"] is True
    assert row["answer"] == "B — name it", row
    # it is not a new ask: every surface that COUNTS what waits filters `answered` out
    assert [q["qid"] for q in c.get("/api/questions").json()
            if q.get("meta") and not q.get("answered")] == ["audit:D158"]


def test_the_receipt_survives_the_run_that_consumes_his_answer(api_client):
    """A mid-run delivery consumes the queued message instantly, and then only the durable
    marker is left. The receipt must survive that — this is the exact moment the operator's
    own words used to disappear from the only surface that had shown them (F525's lesson,
    which the audit decisions never got)."""
    c, tmp = _audit_client(api_client)
    rdir = tmp / "routines" / "self-audit"
    assert c.post("/api/questions/audit:D157/answer",
                  json={"text": "B — name it"}).status_code == 200

    for p in (rdir / "inbox").glob("msg-*.json"):     # a run drains it
        p.unlink()
    memo.reset()

    row = _metas(c).get("audit:D157")
    assert row is not None and row["answered"] is True, \
        "the receipt vanished with the message a run consumed"
    # no words survive consumption, so it says WHEN he answered rather than inventing what
    marker = read_json(rdir / "audit" / "decisions-answered.json")
    assert row["answer"], row
    assert str(marker["D157"])[:10] in row["answer"], row["answer"]


def test_a_newer_report_re_asking_the_decision_is_open_again(api_client):
    """The re-ask must still work: self-audit deliberately lists a decision open again in a
    NEWER report, and that outranks the receipt — otherwise the acknowledgement would silence
    a question the routine means to ask a second time."""
    c, tmp = _audit_client(api_client)
    assert c.post("/api/questions/audit:D157/answer",
                  json={"text": "B — name it"}).status_code == 200
    _audit(tmp, {**REPORT, "generated": "2099-01-01T00:00:00+00:00"})

    row = _metas(c)["audit:D157"]
    assert not row.get("answered"), "a newer report re-asking it must read as OPEN again"
    assert row["options"] == ["A — drain it", "B — name it"]


def _audit_client(api_client):
    c, tmp = api_client
    _audit(tmp)
    return c, tmp
