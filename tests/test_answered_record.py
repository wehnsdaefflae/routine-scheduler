"""F525 — an answered decision must survive the run that consumes it.

The operator answered one of four sub-questions on a decision card and then could neither
SEE his answer ("why can't i see this answer in the routine's inbox?") nor REVISE it to add
the other three. Both symptoms have one cause: consumption destroyed the record.

Before this change `collect_deferred_answers` consumed `inbox/answer-<qid>.json` into the
run's `consumed/` dir AND unlinked `questions/pending/<qid>.json`, writing nothing in their
place. `decisions_read._mark_answered` derives the answer text by reading that inbox file
and `_all_questions_fresh` iterates the PENDING records — so the moment a run booted, the
"Settled — answered, queued for pickup" card vanished, carrying the user's own words with
it. What he saw was not a rendering bug: the system genuinely no longer knew what he said.

These tests pin the property that consumption must ARCHIVE what it removes, and that an
answer stays amendable afterwards.
"""

from __future__ import annotations

import json

from rsched.engine import inbox


def _ask(routine_dir, qid, question="Pick one", options=None):
    inbox.file_question(routine_dir, qid, question, options or ["a", "b"], "20260921-113706")


def _answer(routine_dir, qid, text):
    (routine_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (routine_dir / "inbox" / f"answer-{qid}.json").write_text(
        json.dumps({"qid": qid, "text": text, "source": "web", "ts": "2026-09-21T15:11:19+02:00"}),
        encoding="utf-8")


def test_consuming_an_answer_keeps_the_record_the_user_can_read(tmp_path):
    """The exact loss the operator hit: answer a question, let a run boot, and the answer
    is still there to be read — not only inside that run's consumed/ audit trail.
    """
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", "Four things only you can do")
    _answer(rd, "q-1", "1. i think it works now. i opened the tailgate port")

    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")
    assert [p["qid"] for p in pairs] == ["q-1"]          # the run still receives it

    settled = inbox.answered_questions(rd)
    assert len(settled) == 1
    rec = settled[0]
    assert rec["qid"] == "q-1"
    assert rec["answer"] == "1. i think it works now. i opened the tailgate port"
    assert rec["question"] == "Four things only you can do"
    assert rec["answer_source"] == "web"
    assert rec["answered"] is True
    assert rec["consumed"]                                # WHEN the run took it


def test_the_archived_answer_is_not_re_delivered_to_the_next_run(tmp_path):
    """Keeping the record must not resurrect the work: a second boot delivers nothing."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1")
    _answer(rd, "q-1", "yes")
    inbox.collect_deferred_answers(rd, tmp_path / "run1" / "consumed")

    assert inbox.collect_deferred_answers(rd, tmp_path / "run2" / "consumed") == []
    assert inbox.open_questions(rd) == []                 # and it is no longer OPEN
    assert len(inbox.answered_questions(rd)) == 1         # but it is still readable


def test_revising_a_consumed_answer_re_queues_it_for_the_next_run(tmp_path):
    """His actual request: 'i want to revise it to add the other answers.' An amended
    answer must reach a run again — a correction nobody reads is not a correction.
    """
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", "Four things only you can do")
    _answer(rd, "q-1", "1. it works now")
    inbox.collect_deferred_answers(rd, tmp_path / "run1" / "consumed")

    inbox.revise_answer(rd, "q-1", "1. it works now\n2. copy the gmail creds\n3. it can ask")

    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run2" / "consumed")
    assert len(pairs) == 1
    assert "3. it can ask" in pairs[0]["answer"]
    assert pairs[0]["question"] == "Four things only you can do"   # context came back too
    assert pairs[0].get("revised") is True                          # marked as an amendment

    settled = inbox.answered_questions(rd)
    assert len(settled) == 1                       # ONE row, amended — never a duplicate
    assert "3. it can ask" in settled[0]["answer"]


def test_revising_an_answer_that_was_never_filed_is_refused(tmp_path):
    """Fail loudly rather than inventing a record for a question nobody answered."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    try:
        inbox.revise_answer(rd, "q-nope", "text")
    except LookupError:
        return
    raise AssertionError("revise_answer must refuse an unknown qid")


def test_an_answer_still_waiting_is_revised_in_place(tmp_path):
    """Revising BEFORE any run consumed it rewrites the queued answer — one answer, not two."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1")
    _answer(rd, "q-1", "first")

    inbox.revise_answer(rd, "q-1", "second")

    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")
    assert [p["answer"] for p in pairs] == ["second"]
