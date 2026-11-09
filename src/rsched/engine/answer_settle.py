"""Settle an answered decision WITHOUT a run reading it (2026-11-09).

The operator's "settle" on an UNDELIVERED decision (`web/decision_pickup`): an answer no run
will ever read — its conversation finished during the turn it was filed in, its routine is off
— and that he no longer wants acted on. Delivering it would start a run that acts on a stale
word, so this does exactly what a run's consumption does (`inbox_questions`: archive the ask
and the answer as one record, consume the answer file by rename, drop the pending record) and
nothing else. The archive marks it `settled_by: operator`, so the receipt card says nobody
acted on it.
"""

from __future__ import annotations

from pathlib import Path

from .. import answerfile
from ..paths import atomic_write_json, read_json
from .inbox import _consume
from .inbox_questions import _archive_answer


def settle(routine_dir: Path, qid: str) -> bool:
    """Archive `qid`'s ask with its waiting answer and consume both. False when either is gone."""
    pending = routine_dir / "questions" / "pending" / f"{qid}.json"
    answer_path = answerfile.path(routine_dir, qid)
    question, answer = read_json(pending), read_json(answer_path)
    if not isinstance(question, dict) or not isinstance(answer, dict) or "text" not in answer:
        return False
    archived = _archive_answer(routine_dir, question, {**answer, "qid": qid})
    record = read_json(archived)
    if isinstance(record, dict):
        atomic_write_json(archived, {**record, "settled_by": "operator"})
    run_dir = routine_dir / "runs" / str(question.get("asked") or "")
    _consume(answer_path, (run_dir if run_dir.is_dir() else routine_dir / "questions") / "consumed")
    pending.unlink(missing_ok=True)
    return True
