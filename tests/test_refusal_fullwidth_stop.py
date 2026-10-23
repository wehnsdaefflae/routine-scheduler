"""R2844: the opening-sentence split must end at a FULLWIDTH or CJK stop too.

`_OPENING_STOP` split on ASCII `.!?;:` followed by whitespace. A reply whose first sentence
ends in `。` / `！` / `？` / `．` / `；` / `：` was therefore never split, so the whole
`_HEAD_CAP` (200) character head was taken as "the opening sentence" — and the zero-cost
marker shortcut, which exists to decide on ONE sentence, then matched a refusal marker
occurring anywhere in those 200 characters.

Reported by scheduler-builder (R2844, restated in R2863) and handed back to self-audit as a
one-run live-tree fix. The failure mode is over-eagerness, not laxness: a reply that genuinely
answers can be classified as a refusal because its third sentence quotes one.

The stops split with NO whitespace lookahead, because the writing systems that use them put
no space after them — which is the whole reason the ASCII rule missed them.
"""

from __future__ import annotations

import pytest

from rsched.engine.refusal import _HEAD_CAP, _opening_sentence, looks_like_refusal

#: One per codepoint the fix adds, with the character's name — a regression that drops any
#: single one of them fails here by name rather than as "the fullwidth test".
STOPS = [
    ("\u3002", "ideographic full stop"),
    ("\uff01", "fullwidth exclamation mark"),
    ("\uff1f", "fullwidth question mark"),
    ("\uff0e", "fullwidth full stop"),
    ("\uff1b", "fullwidth semicolon"),
    ("\uff1a", "fullwidth colon"),
]


@pytest.mark.parametrize(("stop", "name"), STOPS, ids=[n for _, n in STOPS])
def test_a_fullwidth_stop_ends_the_opening_sentence(stop: str, name: str) -> None:
    """The opening sentence stops AT the character, so the rest of the head is not in it."""
    reply = f"\u3053\u308c\u306f\u7b54\u3048\u3067\u3059{stop}The second sentence is separate."
    opening = _opening_sentence(reply)
    assert "second sentence" not in opening, f"{name} did not end the opening sentence"
    assert opening == "\u3053\u308c\u306f\u7b54\u3048\u3067\u3059"


@pytest.mark.parametrize(("stop", "name"), STOPS, ids=[n for _, n in STOPS])
def test_a_marker_past_a_fullwidth_stop_is_not_an_opening_refusal(stop: str, name: str) -> None:
    """The shortcut decides on the opening sentence alone — a marker in a LATER sentence of a
    reply that answers must not make it a refusal. This is the defect's observable form.
    """
    reply = (f"\u3053\u308c\u304c\u7d50\u679c\u3067\u3059{stop}"
             "For the record I cannot help with the 2019 figures, which are not published.")
    assert len(reply) < _HEAD_CAP, "the case must fit inside the head the shortcut reads"
    assert looks_like_refusal(reply) is False, f"{name}: a later-sentence marker decided it"


def test_an_opening_refusal_before_a_fullwidth_stop_still_matches() -> None:
    """The fix must not cost the shortcut its real hits: a decline that OPENS the reply and
    ends in a fullwidth stop is still read as one.
    """
    assert looks_like_refusal("I can't help with that\u3002\u5225\u306e\u8cea\u554f\u3092") is True


def test_the_ascii_rule_is_unchanged() -> None:
    """The ASCII stops keep their whitespace lookahead: `3.5 million` is not two sentences."""
    assert _opening_sentence("We counted 3.5 million rows. The rest follows.") \
        == "we counted 3.5 million rows"
