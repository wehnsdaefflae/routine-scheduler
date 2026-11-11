"""What the escalation dispatch may CLAIM about its own evidence (R3320/R3325, `fau`).

The ladder's value is one comparison: the worker's account against the engine's counts.
`engine/oversight.py`'s header states the premise — *"the party under suspicion must not be
the only witness"* — and the supervisor prompt is where that premise can be broken by a
sentence, because a rung reads nothing but the dispatch it is handed.

MEASURED (`fau` filed it twice, R3320 then R3325): the prompt told the supervisor the dispatch
carried "its own answers to 3 questions". Nothing had asked the worker anything and no turn
was ever spent doing so — `ladder._worker_answers` returns `turn_records[-3:]`'s `say` lines,
and `oversight.build_dispatch` already puts EVERY say line of the interval in `says`. So the
tail is a SUBSET of what the rung was already reading, and the cross-check was one witness
quoted twice. The failure is invisible by construction: a rung comparing one source against
itself reports, truthfully as far as it knows, that it compared two.

These live in their own module because `tests/test_ladder_trigger.py` is at its size-ratchet
ceiling (506) and an over-bar file may only shrink; the helpers come from it by the flat-import
pattern ten sibling test modules already use.
"""

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
