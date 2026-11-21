"""F731 — a numbered answer to a question's own numbered picks grounds a goal."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import run_context
from rsched.engine import goalops

TS = "20261024-130004"

# The measured question, trimmed to its shape: prose, a fenced snippet whose code starts with a
# digit (so the choice parser cannot take it for a pick), then the numbered choices.
QUESTION = """**No, your netplan change was right — and it worked. It just wasn't the last gate.**

The refusal comes from a different line, in `build/modules/tool.js`:

```js
1 < r.length && (o.allowed = !1, o.interfaces.count = r.length)
```

**Which do you want?**

1. **Sudoers line for one reviewed wrapper** — I write `/usr/local/sbin/bbm-netns`, you read it
   and add one line. After that I never need you for the campaign again.
2. **Narrow sudoers for `ip` + `nsenter`** — I build the namespace myself each time.
3. **Stop pursuing the certified measurement** — the case rests on the outage log.
"""

OPTION_QUESTION = "Dark Matter S02E02 has been stalled for 11 days. Which do you want?"
OPTIONS = ["keep waiting and nudging", "try a 720p WEB-DL fallback", "drop the episode"]


def _loop(make_routine, slug: str, *, answer: str, options: list[str] | None = None,
          question: str = QUESTION, deferred: bool = True) -> SimpleNamespace:
    """A run whose transcript holds one answered question, in either shape the engine writes:"""
    d = make_routine(slug=slug)
    ctx = run_context(d, TS)
    if deferred:
        ctx.transcript.event("answer", {"qid": "q-0", "text": answer, "source": "deferred",
                                        "question": question})
    else:
        ctx.transcript.event("question", {"qid": "q-1", "mode": "blocking", "type": "question",
                                          "question": question, "options": options or []})
        ctx.transcript.event("answer", {"qid": "q-1", "text": answer, "source": "web"})
    return SimpleNamespace(ctx=ctx, instruction="")


def _set(loop: SimpleNamespace, quote: str, text: str = "the wrapper runs under one sudoers line") -> dict:
    return goalops.handle_goal(loop, {"say": "Recording what he approved.", "kind": "goal",
                                      "verb": "set",
                                      "goals": [{"text": text, "quote": quote}]})


def test_a_numbered_answer_to_the_questions_own_picks_grounds_a_goal(make_routine):
    """The reported defect, in the shape it was measured: `"answer": "1."`, options empty, the
    choices numbered in the question's prose."""
    loop = _loop(make_routine, "gp-prose", answer="1.")
    obs = _set(loop, "1.")
    assert not obs.get("rejected"), obs.get("reason")
    (b1,) = obs["ledger"]
    assert "Sudoers line for one reviewed wrapper" in b1["quote"], (
        f"the goal must carry the WORDS of the choice he picked, not the keystroke: {b1}")
    assert b1["status"] == "open" and b1["source"] == "person"


def test_a_number_resolves_against_the_options_the_question_carried(make_routine):
    """The finding's own fix direction: when the answered question carried `options`, the
    resolved option text is what grounds the goal."""
    loop = _loop(make_routine, "gp-options", answer="2", options=OPTIONS,
                 question=OPTION_QUESTION, deferred=False)
    obs = _set(loop, "2", text="a 720p WEB-DL of S02E02 is downloaded")
    assert not obs.get("rejected"), obs.get("reason")
    assert obs["ledger"][0]["quote"] == "try a 720p WEB-DL fallback", obs["ledger"]


def test_the_choice_he_picked_is_quotable_in_its_own_words(make_routine):
    """The engine rendered the picks, so the words behind the pick are his — quoting them
    directly must work too, which is what a run should do by preference."""
    loop = _loop(make_routine, "gp-words", answer="1.")
    obs = _set(loop, "Sudoers line for one reviewed wrapper")
    assert not obs.get("rejected"), obs.get("reason")


def test_the_latest_pick_of_a_number_is_the_one_a_goal_is_grounded_in(make_routine):
    """Two options questions in one run, both answered "1." — the approval being recorded is the
    one that just arrived, so the later choice's words are what ground the goal."""
    d = make_routine(slug="gp-twice")
    ctx = run_context(d, TS)
    ctx.transcript.event("question", {"qid": "q-1", "mode": "blocking", "type": "question",
                                      "question": "First?", "options": ["the earlier choice"]})
    ctx.transcript.event("answer", {"qid": "q-1", "text": "1.", "source": "web"})
    ctx.transcript.event("question", {"qid": "q-2", "mode": "blocking", "type": "question",
                                      "question": "Second?", "options": ["the later choice"]})
    ctx.transcript.event("answer", {"qid": "q-2", "text": "1.", "source": "web"})
    obs = _set(SimpleNamespace(ctx=ctx, instruction=""), "1.")
    assert not obs.get("rejected"), obs.get("reason")
    assert obs["ledger"][0]["quote"] == "the later choice", obs["ledger"]


def test_a_number_the_prose_uses_for_two_different_things_resolves_to_nothing(make_routine):
    """Two numbered lists in one question: which "1." he meant is unknowable, and grounding a
    goal in the wrong sentence is what the quote rule exists to prevent — so it is refused."""
    loop = _loop(make_routine, "gp-ambig", answer="1.", question=(
        "What I found:\n\n1. the bridges are up\n2. the cable is fine\n\n"
        "What do you want?\n\n1. patch the app\n2. build the namespace\n"))
    obs = _set(loop, "1.")
    assert obs.get("rejected"), obs
    assert "are not in anything the person wrote to this run" in obs["reason"]


@pytest.mark.parametrize(("slug", "quote"), [
    ("gp-never", "deploy to production"),      # words nobody wrote
    ("gp-nochoice", "9."),                     # a pick of a choice the question never offered
    ("gp-unpicked", "Stop pursuing the certified measurement"),   # a choice he did NOT pick
])
def test_a_quote_that_matches_no_words_and_no_choice_he_picked_is_still_refused(make_routine,
                                                                               slug, quote):
    """The grounding rule does not weaken: this is the refusal that must survive the fix."""
    loop = _loop(make_routine, slug, answer="1.")
    obs = _set(loop, quote)
    assert obs.get("rejected"), obs
    assert "are not in anything the person wrote to this run" in obs["reason"]
    assert repr(quote) in obs["reason"], (
        f"the refusal names the words the model wrote, never a resolved choice: {obs['reason']}")
