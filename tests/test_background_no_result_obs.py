"""The two background ends that produce NO result must render as words, not as a raw dict.

WHY ITS OWN FILE. `tests/render_guard.py` renders every observation the two dispatch seams
RETURN, which is what makes a handler test prove its own wording. These two payloads are
written straight to the transcript by `background.settle` and `background.kill` — they pass
through no seam, so the guard never saw them and the hole stayed open: `kind`/`handle`/
`started_turn`/`note` and no `name`, no `exit`. A backgrounded `script` or `util` is in
`obs_floor.OWN_ERRORS`, so floor 1 declines them as well, and the per-kind chain's
`obs['name']` raised. Measured live: conversation c-20261026-095411 lost a 25-minute
background `script` to a daemon restart (R2970) and its RESUMED leg, which re-renders the
stored observation, read `KeyError: 'name' at engine/observations.py:305` and a JSON dump
instead of the sentence naming what it had lost.

`test_background_actions.py` is at its size-ratchet ceiling, so the pin lives here.
"""

from __future__ import annotations

import pytest

from rsched.engine import observations

#: Exactly the keys `background.settle` writes for a call still running at run end.
ABANDONED = {
    "kind": "script", "background": True, "handle": "bg1", "started_turn": 544,
    "abandoned": True, "llm_calls_abandoned": 0,
    "note": "the run ended before this background action finished; its result is lost "
            "and nothing in the run read it",
}

#: Exactly the keys `background.kill` writes for a cancelled call.
CANCELLED = {
    "kind": "util", "background": True, "handle": "bg2", "started_turn": 12,
    "cancelled": True, "had_landed": False, "stopped_work": True,
    "note": "the run cancelled this background call; its result was not delivered; "
            "its command's process group was terminated",
}


@pytest.mark.parametrize(("obs", "word"), [(ABANDONED, "ABANDONED"), (CANCELLED, "CANCELLED")],
                         ids=["abandoned", "cancelled"])
def test_a_background_end_with_no_result_renders_its_own_sentence(obs, word):
    """Red before the fix with `KeyError: 'name'`, for both payloads."""
    text, defect = observations.render_observation(obs)
    assert defect is None, f"the renderer raised on a {word.lower()} background call: {defect}"
    assert word in text, text
    assert obs["handle"] in text, text
    assert str(obs["note"]) in text, "the note is the whole point — it says what was lost"
    assert "{" not in text, f"a raw dict reached the run instead of a sentence: {text!r}"


def test_the_started_wording_is_unchanged():
    """D118 phase 1's own branch keeps its words: the fix widened the condition, not the text."""
    started = {"kind": "script", "background": True, "handle": "bg1", "started_turn": 3,
               "started": True, "note": "the real observation arrives at a later boundary"}
    text, defect = observations.render_observation(started)
    assert defect is None
    assert "STARTED IN THE BACKGROUND as `bg1`" in text, text


def test_a_landed_background_result_still_renders_through_its_kind():
    """The widened condition must not swallow the REAL observation: a landed call carries its
    kind's own fields and must still be worded by its kind's branch, exit code and all."""
    landed = {"kind": "script", "name": "gridworld", "exit": 0, "stdout": "ok",
              "background": True, "handle": "bg1", "started_turn": 544}
    text, defect = observations.render_observation(landed)
    assert defect is None
    assert "gridworld" in text and "exit 0" in text, text
