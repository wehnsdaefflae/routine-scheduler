"""The post-finish sweep re-opens a CONVERSATION for an answer filed during its last turn.

2026-09-21: two conversations asked a question in their closing turn and were answered seconds
before they finished. The answer route saw a LIVE run, so it did not resume it (F39 resumes a
finished one); the run then finished without draining the answer, and nothing read it for
seven weeks. A routine's answer waits for its next fire by design, so only a conversation is
re-opened here.
"""

from rsched.daemon.runner_state import stranded_input
from rsched.paths import atomic_write_json


def _dir(tmp_path, *, pending=True, answer=True):
    d = tmp_path / "c-1"
    (d / "inbox").mkdir(parents=True)
    if pending:
        atomic_write_json(d / "questions" / "pending" / "q-1.json", {"qid": "q-1"})
    if answer:
        atomic_write_json(d / "inbox" / "answer-q-1.json", {"qid": "q-1", "text": "weekly"})
    return d


def test_an_answered_question_of_a_finished_conversation_is_stranded(tmp_path):
    assert stranded_input(_dir(tmp_path), "conversations") is True


def test_a_routine_answer_waits_for_its_next_fire(tmp_path):
    assert stranded_input(_dir(tmp_path), "routines") is False


def test_an_answer_whose_question_is_gone_is_not_stranded(tmp_path):
    assert stranded_input(_dir(tmp_path, pending=False), "conversations") is False


def test_nothing_waiting_is_nothing_stranded(tmp_path):
    assert stranded_input(_dir(tmp_path, answer=False), "conversations") is False
