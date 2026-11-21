"""The seven classes of thing that might need the operator's intervention — D289."""

from __future__ import annotations

import json
from pathlib import Path

from rsched.readmodels.attention import CLASSES, DERIVED, MARKED, classify_run


def _run(tmp_path: Path, *, slug: str = "a-routine", ts: str = "20261114-120000",
         pending: list[dict] | None = None, result: str = "", **status) -> Path:
    """A routine dir with one run: its status.json, its result.md, its questions/pending."""
    routine = tmp_path / slug
    d = routine / "runs" / ts
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"run_id": f"{slug}:{ts}", **status}),
                                   encoding="utf-8")
    if result:
        (d / "result.md").write_text(result, encoding="utf-8")
    if pending:
        q = routine / "questions" / "pending"
        q.mkdir(parents=True)
        for rec in pending:
            (q / f"{rec['qid']}.json").write_text(json.dumps(rec), encoding="utf-8")
    return d


# ---- the vocabulary is HIS, letter for letter ---------------------------------------------

def test_the_seven_classes_are_his_seven_in_his_order():
    """A class he did not name, or one of his missing, makes the labelled block a different
    thing from the one he answered for."""
    assert [c["id"] for c in CLASSES] == list("abcdefg")
    for cid, word in (("a", "decide"), ("b", "unexpected"), ("c", "communication"),
                      ("d", "goal"), ("e", "block"), ("f", "pivot"), ("g", "dead")):
        label = next(c["label"] for c in CLASSES if c["id"] == cid)
        assert word in label.lower(), f"class {cid} should speak of {word!r}: {label!r}"


def test_every_class_declares_whether_it_is_derived_or_marked():
    """The sub-question's answer, held as DATA so either surface can show which half a row came
    from — and so a future class cannot be added without deciding it."""
    assert set(DERIVED) | set(MARKED) == {c["id"] for c in CLASSES}
    assert not set(DERIVED) & set(MARKED), "a class is derived or marked, never both"
    assert set(DERIVED) == set("abde"), "the four the engine already records"
    assert set(MARKED) == set("cfg"), "the three it cannot derive"


def test_each_class_names_where_its_rows_come_from():
    """A class whose source is unnamed is a label nobody can check — and these sources ARE the
    measurement the decision rests on."""
    for c in CLASSES:
        assert c.get("source"), f"class {c['id']} names no source"


# ---- (d) reached goals: derived from the run's own record ---------------------------------

def test_a_met_goal_lands_in_class_d_with_its_evidence_inline(tmp_path):
    d = _run(tmp_path, goals=[
        {"id": "b1", "text": "the digest is published", "status": "met",
         "evidence": "artifacts/digest-20261114.html, 14 links checked"},
        {"id": "b2", "text": "the funder replied", "status": "open", "remains": "no answer"},
    ])
    met = [r for r in classify_run(d) if r["class"] == "d"]
    assert len(met) == 1 and met[0]["id"] == "b1"
    assert "digest" in met[0]["text"]
    assert "14 links" in met[0]["detail"], "requirement 1: the content inline, not an id"


def test_an_open_or_dropped_goal_is_not_a_reached_one(tmp_path):
    d = _run(tmp_path, goals=[{"id": "b1", "text": "x", "status": "open"},
                              {"id": "b2", "text": "y", "status": "dropped",
                               "dropped_by": "he withdrew it"}])
    assert [r for r in classify_run(d) if r["class"] == "d"] == []


# ---- (e) a block the system cannot clear: the `partial` contract, not a heuristic ---------

def test_a_partial_finish_is_class_e_because_that_is_what_partial_means(tmp_path):
    """`partial` is defined as "something you cannot unblock yourself stopped work you could
    otherwise have done" — the contract says it, so the fold reads it instead of guessing."""
    d = _run(tmp_path, outcome="partial", state="done",
             result="**Process:** the portal refused the login.\n**Work:** nothing filed.")
    rows = [r for r in classify_run(d) if r["class"] == "e"]
    assert len(rows) == 1
    assert "portal refused" in rows[0]["detail"], "his content, inline"


def test_an_ok_or_failed_finish_is_not_a_partial_block(tmp_path):
    for outcome in ("ok", "failed"):
        d = _run(tmp_path, ts=f"20261114-1200{outcome[:2]}", outcome=outcome, state="done",
                 result="s")
        assert [r for r in classify_run(d) if r["class"] == "e"] == [], outcome


# ---- (a) a decision for him: the ask this run filed, linked by its qid --------------------

def test_a_pending_ask_from_this_run_is_class_a_with_its_options_inline(tmp_path):
    """A question's `qid` embeds the asking run's timestamp (`q-<run ts>-<turn>`), which is how
    an ask is attributed to a run without a new field."""
    d = _run(tmp_path, pending=[
        {"qid": "q-20261114-120000-14", "question": "Which mailbox should I answer from?",
         "options": ["work", "personal"], "class": "closing", "mode": "deferred"},
        {"qid": "q-20261101-090000-3", "question": "an older run's ask",
         "options": ["x"], "class": "closing"},
    ])
    asks = [r for r in classify_run(d) if r["class"] == "a"]
    assert len(asks) == 1, "only the ask this run filed"
    assert "Which mailbox" in asks[0]["text"]
    assert "work" in asks[0]["detail"] and "personal" in asks[0]["detail"], (
        "requirement 1: the choices ARE the content he needs, so they are inline")


def test_a_blocking_ask_is_both_a_decision_and_a_block(tmp_path):
    """It is (a) because he must decide and (e) because the run cannot do its work without him.
    His list is not a partition, and forcing a row into one class would lose half its meaning."""
    d = _run(tmp_path, pending=[
        {"qid": "q-20261114-120000-9", "question": "May I send it?", "options": ["yes", "no"],
         "class": "blocking", "mode": "deferred"}])
    assert {"a", "e"} <= {r["class"] for r in classify_run(d)}


def test_an_answered_ask_is_not_still_a_decision_for_him(tmp_path):
    """Only `questions/pending` is read: an answered one is settled, and showing it would be
    the opposite of actionable."""
    routine = tmp_path / "a-routine"
    d = _run(tmp_path)
    ans = routine / "questions" / "answered"
    ans.mkdir(parents=True)
    (ans / "q-20261114-120000-2.json").write_text(
        json.dumps({"qid": "q-20261114-120000-2", "question": "done?", "options": ["a"],
                    "answer": "a"}), encoding="utf-8")
    assert [r for r in classify_run(d) if r["class"] == "a"] == []


# ---- (b) unexpected events: the engine's own record, joined on the run id -----------------

def _health(tmp_path: Path, *rows: dict) -> Path:
    control = tmp_path / ".control"
    control.mkdir(exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def test_a_health_event_this_run_filed_is_class_b_with_its_detail(tmp_path):
    d = _run(tmp_path)
    _health(tmp_path,
            {"ts": "2026-11-14T12:01:00+00:00", "event": "budget_exhausted",
             "routine": "a-routine", "run_id": "a-routine:20261114-120000",
             "detail": "turns exhausted at 60 of 60"},
            {"ts": "2026-11-14T12:02:00+00:00", "event": "run_failed",
             "routine": "other", "run_id": "other:20261114-120000", "detail": "not this run"})
    rows = [r for r in classify_run(d, home=tmp_path) if r["class"] == "b"]
    assert [r["text"] for r in rows] == ["budget_exhausted"]
    assert "60 of 60" in rows[0]["detail"]


def test_the_same_event_twice_in_one_run_is_one_row_with_a_count(tmp_path):
    """MEASURED on this instance 2026-11-14: 266 of 846 class-(b) rows were the same event
    filed again inside one run, and one run produced 30 rows. A labelled block leading with
    thirty lines, nine of them the same word, would be LONGER and less actionable than the
    narrative it leads — the opposite of what he asked for. The count keeps the magnitude.
    """
    d = _run(tmp_path)
    _health(tmp_path,
            *[{"event": "cost_trend_up", "routine": "a-routine",
               "run_id": "a-routine:20261114-120000", "detail": f"reading {i}"}
              for i in range(5)],
            {"event": "run_partial", "routine": "a-routine",
             "run_id": "a-routine:20261114-120000", "detail": "one of these"})
    rows = [r for r in classify_run(d, home=tmp_path) if r["class"] == "b"]
    assert sorted(r["text"] for r in rows) == ["cost_trend_up", "run_partial"]
    by = {r["text"]: r for r in rows}
    assert by["cost_trend_up"]["occurrences"] == 5
    assert by["run_partial"]["occurrences"] == 1
    assert "reading 0" in by["cost_trend_up"]["detail"], "the FIRST detail is kept, not the last"


def test_without_a_home_the_event_class_is_absent_never_guessed(tmp_path):
    """A caller that does not hand the stream over gets no (b) rows — rather than a heuristic
    read of the run's prose, which is exactly what this decision rejects."""
    d = _run(tmp_path)
    _health(tmp_path, {"event": "run_failed", "routine": "a-routine",
                       "run_id": "a-routine:20261114-120000", "detail": "x"})
    assert [r for r in classify_run(d) if r["class"] == "b"] == []


def test_a_malformed_stream_line_is_skipped_not_fatal(tmp_path):
    d = _run(tmp_path)
    control = tmp_path / ".control"
    control.mkdir(exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "{not json\n" + json.dumps({"event": "run_partial", "routine": "a-routine",
                                    "run_id": "a-routine:20261114-120000",
                                    "detail": "real"}) + "\n", encoding="utf-8")
    assert [r["text"] for r in classify_run(d, home=tmp_path) if r["class"] == "b"] == [
        "run_partial"]


# ---- (c)/(f)/(g): marked, because they exist nowhere else ---------------------------------

def test_a_run_may_mark_a_pivot_a_dead_end_and_a_communication(tmp_path):
    d = _run(tmp_path, attention=[
        {"class": "f", "text": "switched from the API to the browser session",
         "detail": "the API has no endpoint for the seat map (checked 2026-11-14)"},
        {"class": "g", "text": "the 2019 archive cannot be reached",
         "detail": "every mirror 404s; a copy would have to be requested"},
        {"class": "c", "text": "answered Sonu's mail about the invoice",
         "detail": "sent 2026-11-14 12:04 from the work mailbox"},
    ])
    assert {r["class"] for r in classify_run(d)} == {"c", "f", "g"}
    assert any("browser session" in r["text"] for r in classify_run(d))


def test_a_marked_row_in_a_derived_class_is_dropped(tmp_path):
    """The half of the answer that protects the surfaces: a run may not label its own work a
    reached goal or an unresolvable block. Those are derived from the record, and a run's
    opinion of them is exactly why these surfaces exist."""
    d = _run(tmp_path, attention=[{"class": "d", "text": "I met the goal, honestly"},
                                  {"class": "e", "text": "and I was blocked, honestly"}],
             goals=[{"id": "b1", "text": "x", "status": "open"}], outcome="ok")
    assert classify_run(d) == []


def test_a_marked_row_with_an_unknown_class_or_no_text_is_dropped(tmp_path):
    d = _run(tmp_path, attention=[{"class": "z", "text": "something"}, {"class": "f"},
                                  "not a dict", {"text": "no class"}])
    assert classify_run(d) == []


# ---- the fold may never break a surface --------------------------------------------------

def test_a_run_dir_with_no_status_reads_as_nothing_to_report(tmp_path):
    d = tmp_path / "r" / "runs" / "20261114-130000"
    d.mkdir(parents=True)
    assert classify_run(d) == []


def test_an_unreadable_status_reads_as_nothing_rather_than_raising(tmp_path):
    d = tmp_path / "r" / "runs" / "20261114-140000"
    d.mkdir(parents=True)
    (d / "status.json").write_text("{not json", encoding="utf-8")
    assert classify_run(d) == []


def test_an_unreadable_question_file_is_skipped_not_fatal(tmp_path):
    d = _run(tmp_path, pending=[{"qid": "q-20261114-120000-1", "question": "real",
                                 "options": ["a"], "class": "closing"}])
    (tmp_path / "a-routine" / "questions" / "pending" / "broken.json").write_text(
        "{nope", encoding="utf-8")
    assert [r["text"] for r in classify_run(d) if r["class"] == "a"] == ["real"]


def test_rows_come_back_in_his_own_order(tmp_path):
    """The labelled block leads with (a) and ends with (g) — the order he wrote them in, which
    is the order he will read them in."""
    d = _run(
        tmp_path, outcome="partial", state="done", result="the portal was down",
        goals=[{"id": "b1", "text": "done", "status": "met", "evidence": "e"}],
        pending=[{"qid": "q-20261114-120000-4", "question": "which?", "options": ["a", "b"],
                  "class": "closing"}],
        attention=[{"class": "g", "text": "dead end"}, {"class": "f", "text": "pivot"},
                   {"class": "c", "text": "a mail went out"}],
    )
    assert [r["class"] for r in classify_run(d)] == ["a", "c", "d", "e", "f", "g"]
