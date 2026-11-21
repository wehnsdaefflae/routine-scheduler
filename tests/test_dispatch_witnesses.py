"""What the escalation dispatch may CLAIM about its own evidence (R3320/R3325, `fau`)."""

from __future__ import annotations

import json

from rsched.engine import ladder
from test_ladder_trigger import _Ctx, _directive, _Loop, _Sub, _Subs


def _prompt_for(tmp_path, says=("Reading the queue.", "Building item one.")):
    """Run one rung and hand back the brief its supervisor child was started with."""
    ctx = _Ctx(tmp_path, ladder={"enabled": True})
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    loop = _Loop(ctx, subs, list(says), "Ship the thing.")
    ladder.escalate(loop, "interval", ladder.ladder_settings(ctx) | {"enabled": True})
    return subs.calls[0]["action"]["prompt"]


def test_the_dispatch_never_claims_the_worker_answered_questions_it_was_never_asked(tmp_path):
    """The defect itself: no sentence in the brief may attribute answers to the worker."""
    prompt = _prompt_for(tmp_path)
    assert "worker_says" in prompt, "the key still travels — the supervisor pattern reads it"
    assert "own answers to" not in prompt, "the worker answered nothing — do not say it did"


def test_the_three_questions_are_named_as_the_rungs_own_work(tmp_path):
    """Keeping the questions is right; they are the rung's checklist. The brief has to say so,
    or the next reader restores the answered-by-the-worker reading from the key name alone."""
    prompt = _prompt_for(tmp_path)
    assert "worker_questions" in prompt
    assert "YOURS to answer" in prompt


def test_worker_says_is_the_newest_tail_of_the_same_lines_not_a_second_source(tmp_path):
    """The relation the honest wording rests on, pinned so the two-source story cannot be
    re-invented: every `worker_says` line is also a `says` line of the interval."""
    ctx = _Ctx(tmp_path, ladder={"enabled": True})
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    lines = ["one.", "two.", "three.", "four."]
    loop = _Loop(ctx, subs, lines, "Ship the thing.")
    assert ladder._worker_answers(loop) == lines[-3:]


def test_the_counts_stay_the_evidence_when_the_two_disagree(tmp_path):
    """The one instruction that makes the comparison useful at all survives the rewording."""
    prompt = _prompt_for(tmp_path)
    assert "the counts are the evidence" in prompt
