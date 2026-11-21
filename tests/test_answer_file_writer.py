"""D143 — the `answer-<qid>.json` shape has ONE writer too."""

from __future__ import annotations

import json

import pytest

from rsched import answerfile
from rsched.engine import inbox


def _read(routine_dir, qid) -> dict:
    return json.loads((routine_dir / "inbox" / f"answer-{qid}.json").read_text(encoding="utf-8"))


def test_the_filename_and_the_qid_come_from_one_place(tmp_path):
    """The seam's point: the caller names the question, never the file. Both readers fall back
    to `path.stem.removeprefix("answer-")`, so a record whose `qid` disagreed with its filename
    would be consumed under one id and archived under the other."""
    rd = tmp_path / "r"
    path = answerfile.write(rd, "q-1", {"text": "yes", "source": "web"})

    assert path == rd / "inbox" / "answer-q-1.json"
    assert path == answerfile.path(rd, "q-1")
    assert _read(rd, "q-1")["qid"] == "q-1"


def test_an_answer_written_through_the_seam_is_consumed_by_a_run(tmp_path):
    """End to end against the real reader: what the writer produces is what
    `collect_deferred_answers` delivers — the property every one of the seven sites existed
    to achieve."""
    rd = tmp_path / "r"
    (rd / "questions" / "pending").mkdir(parents=True)
    inbox.file_question(rd, "q-1", "Which one?", ["a", "b"], "20261031-140002")

    answerfile.write(rd, "q-1", {"text": "a", "source": "web"})
    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")

    assert [(p["qid"], p["answer"]) for p in pairs] == [("q-1", "a")]


def test_a_record_no_reader_would_consume_is_refused(tmp_path):
    """The silent loss this writer is the one place to catch: a record carrying neither a
    `text` key nor `defer` is skipped un-consumed by `collect_deferred_answers` and read as
    absent by `take_answer`, so it waits in the inbox forever and nobody is told."""
    rd = tmp_path / "r"
    with pytest.raises(ValueError, match="text"):
        answerfile.write(rd, "q-1", {"source": "web", "decision": "allow_now"})
    assert not answerfile.path(rd, "q-1").exists()

    # the control: an EMPTY text is a real answer (an access request decided with no prose),
    # because both readers test for the KEY, not for its truth
    answerfile.write(rd, "q-2", {"text": "", "decision": "allow_now", "source": "web"})
    assert inbox.take_answer(rd, "q-2", tmp_path / "c")["decision"] == "allow_now"


def test_a_defer_marker_is_a_valid_record_without_text(tmp_path):
    """The Decisions page's defer releases a parked run without deciding — the one record
    shape that legitimately carries no text (`engine.inbox.take_answer` returns it like an
    answer, and the caller unblocks on its stated default)."""
    rd = tmp_path / "r"
    answerfile.write(rd, "q-1", {"defer": True, "source": "web"})

    taken = inbox.take_answer(rd, "q-1", tmp_path / "c")
    assert taken["defer"] is True
    assert taken["qid"] == "q-1"


def test_every_record_carries_a_timestamp_and_a_callers_own_is_kept(tmp_path):
    """`ts` is read by the surfaces that order and age answers, and seven sites each stamping
    their own is how the `msg-*` shape drifted. A caller that already has one (the API's answer
    payload, which is also returned to the person who clicked) keeps it."""
    rd = tmp_path / "r"
    answerfile.write(rd, "q-1", {"text": "yes"})
    assert _read(rd, "q-1")["ts"]

    answerfile.write(rd, "q-2", {"text": "yes", "ts": "2026-10-31T14:00:02+01:00"})
    assert _read(rd, "q-2")["ts"] == "2026-10-31T14:00:02+01:00"


def test_a_correction_updates_a_queued_answer_in_place(tmp_path):
    """`api_patch_applied`'s job: the answer is already filed and the apply button reports the
    patch really went live, so the record a run has yet to read must say so."""
    rd = tmp_path / "r"
    answerfile.write(rd, "q-1", {"text": "Ok", "source": "web", "config_patch_applied": False})

    updated = answerfile.update(rd, "q-1", {"config_patch_applied": True})

    assert updated["config_patch_applied"] is True
    assert updated["text"] == "Ok"                      # the operator's own words, untouched
    assert _read(rd, "q-1")["config_patch_applied"] is True


def test_a_correction_never_resurrects_an_answer_a_run_already_took(tmp_path):
    """The hazard that makes `update` a separate verb. The patch-applied route corrects the
    queued answer AND the durable archive; if the inbox half were a plain write, correcting an
    answer a run had already consumed would re-create it — and the next run would read a
    settled decision as fresh. `inbox.rewrite_message` exists for the same race on the message
    side, where the re-created file delivered the edit twice.
    """
    rd = tmp_path / "r"
    answerfile.write(rd, "q-1", {"text": "Ok", "source": "web"})
    inbox.take_answer(rd, "q-1", tmp_path / "run" / "consumed")       # a run drains it
    assert not answerfile.path(rd, "q-1").exists()

    assert answerfile.update(rd, "q-1", {"config_patch_applied": True}) is None
    assert not answerfile.path(rd, "q-1").exists()
    assert inbox.collect_deferred_answers(rd, tmp_path / "run2" / "consumed") == []
