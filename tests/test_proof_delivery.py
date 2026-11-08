"""D220 — a filed proof reaches the run that asked, through BOTH delivery doors.

An answer reaches a run at two moments: its boot digest (`engine/composer`) and a live turn
boundary (`engine/control.drain_injections`). Each used to compose the verdicts riding an
answer by naming every module inline, so a third verdict could reach a run at boot and not
mid-run — the exact asymmetry R2778 and R2197 were each filed about. `rsched/answerlines.py`
holds the one order; these tests pin that both doors use it and that the proof survives the
consumption that destroys the pending record.
"""

from __future__ import annotations

import json

from rsched import answerlines, proofs
from rsched.engine import inbox

_PROOF = {"a: do it": {"predicate": "release_names_item", "reads": "state/shipped.jsonl",
                       "category": "routine_act"},
          "b: leave it": {"predicate": "row_dropped", "reads": "audit/report.json",
                          "category": "routine_act"}}


def _ask(routine_dir, qid, **kw):
    inbox.file_question(routine_dir, qid, "Ship it?", list(_PROOF), "20261108-140001", **kw)


def _answer(routine_dir, qid, text):
    (routine_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (routine_dir / "inbox" / f"answer-{qid}.json").write_text(
        json.dumps({"qid": qid, "text": text, "source": "web", "ts": "2026-11-08T15:04:00+01:00"}),
        encoding="utf-8")


def test_a_question_filed_with_a_proof_stores_it_open_per_option(tmp_path):
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", proof=_PROOF)

    record = json.loads((rd / "questions" / "pending" / "q-1.json").read_text(encoding="utf-8"))
    assert set(record["proof"]) == set(_PROOF)
    assert {e["state"] for e in record["proof"].values()} == {proofs.OPEN}
    assert record["proof"]["a: do it"]["predicate"] == "release_names_item"


def test_a_question_filed_without_one_carries_no_proof_key_at_all(tmp_path):
    """Every ask the fleet files today. The key must be ABSENT, not an empty object: a
    surface asking `unproven` of it must get the pre-D220 answer."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1")

    record = json.loads((rd / "questions" / "pending" / "q-1.json").read_text(encoding="utf-8"))
    assert "proof" not in record
    assert proofs.unproven({**record, "answered": True}) is False


def test_the_answer_pair_a_run_consumes_carries_the_chosen_options_proof(tmp_path):
    """The pair is what both delivery seams read, and consumption destroys the pending
    record — so a proof that stopped at the record would reach no run at all."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", proof=_PROOF)
    _answer(rd, "q-1", "a: do it")

    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")
    assert len(pairs) == 1
    assert pairs[0]["proof"]["a: do it"]["state"] == proofs.IN_PROGRESS
    assert pairs[0]["proof"]["b: leave it"]["state"] == proofs.DECLINED
    assert proofs.unproven({"answered": True, "proof": pairs[0]["proof"]}) is True


def test_a_prose_answer_carries_the_proof_with_nothing_selected(tmp_path):
    """He answers in prose more often than he picks. The run must still learn that the
    decision has a proof and is not settled by what he wrote."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", proof=_PROOF)
    _answer(rd, "q-1", "whichever is cheaper, you decide")

    pairs = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")
    assert {e["state"] for e in pairs[0]["proof"].values()} == {proofs.OPEN}
    assert answerlines.suffixes(pairs[0]) == ""      # nothing is in progress, so nothing to say


def test_both_delivery_doors_render_the_same_verdict_lines(tmp_path):
    """The property the shared order exists for: the boot digest and the live turn boundary
    read one composition, so a verdict cannot reach one door and not the other."""
    import inspect

    from rsched.engine import composer, control

    boot = inspect.getsource(composer.state_digest)
    live = inspect.getsource(control.drain_injections)
    assert "answerlines.suffixes" in boot
    assert "answerlines.suffixes" in live
    # and neither composes the list itself any more
    assert "decline.digest_suffix" not in boot
    assert "decline.digest_suffix" not in live


def test_the_proof_line_names_the_predicate_and_what_it_reads(tmp_path):
    """What the run is actually told — the sentence, through the shared composition."""
    rd = tmp_path / "self-audit"
    (rd / "questions" / "pending").mkdir(parents=True)
    _ask(rd, "q-1", proof=_PROOF)
    _answer(rd, "q-1", "a: do it")
    pair = inbox.collect_deferred_answers(rd, tmp_path / "run" / "consumed")[0]

    line = answerlines.suffixes(pair)
    assert "release_names_item" in line
    assert "state/shipped.jsonl" in line
    assert "CONDITIONAL" in line
