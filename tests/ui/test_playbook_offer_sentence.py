"""The playbook offer's SENTENCE, asserted where it is COMPOSED — in the browser (R3645).
`playbook_offer.offer` returns a sentence FRAGMENT (no subject, no terminal punctuation) and
static/components/playbook-row.js supplies the lead-in clause and the question mark, so the
sentence an operator reads exists in NEITHER file, and the server-side guard
(tests/test_playbook_offer.py) is a banned-substring check with no access to the console's
half: reword the lead-in and it goes stale while a new collision renders unchallenged. 0.443.0
shipped exactly that — "did real work — 23 turns of work —" — and was found by RENDERING the
row, which is what this module does.
"""

from __future__ import annotations

import json
import re

import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .helpers import start_conversation

#: The hook playbook-row.js mounts on the offer line (an attribute, not a class: nothing here
#: needs styling, and the class contract governs classes).
OFFER = "[data-pb-offer]"

#: Grammatical glue may legitimately recur inside one sentence. A CONTENT word repeating across
#: the lead-in/fragment join is the defect this module exists to catch.
GLUE = frozenset({"a", "an", "and", "for", "in", "into", "it", "its", "of", "on", "or", "so",
                  "that", "the", "to", "with"})

LEAD_IN = "This conversation did real work — "


def _did_real_work(ui, page, text: str, *, turns: int = 23, playbook: str = "") -> str:
    """A conversation the offer fires on: one finished run of `turns` turns (`seed_run` writes
    2, below the threshold), optionally bound to a source playbook — the `mode: "update"`
    wording, whose fragment is written separately from the `save` one."""
    slug, conv = start_conversation(ui, page, text)
    run_dir = ui.seed_run(slug, "20261105-090000", "finished", summary="did the thing",
                          home=ui.conversations)
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    status["turn"] = turns
    atomic_write_json(run_dir / "status.json", status)
    if playbook:
        path = conv / "routine.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg["playbook"] = {"slug": playbook, "commit": "0" * 8}
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    page.reload()
    return slug


def _composed_sentence(page) -> str:
    """The offer as a reader sees it, asserted PRESENT first (an empty locator passes every
    content assertion vacuously), then checked as ONE sentence: one terminal question mark, no
    sentence ended inside it, and no content word said twice across the join."""
    offer = page.locator(OFFER)
    expect(offer).to_have_count(1)
    text = offer.inner_text().strip()
    assert text.startswith(LEAD_IN), f"the console's lead-in changed; this case pins it: {text}"
    assert text.endswith("?"), f"the offer is not a question: {text}"
    assert text.count("?") == 1, f"the fragment brought its own punctuation: {text}"
    assert "." not in text, f"the fragment ended a sentence inside the lead-in's: {text}"
    fragment = text[len(LEAD_IN):]
    assert not fragment[:1].isupper(), f"the fragment starts a new sentence: {text}"
    words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in GLUE]
    said_twice = sorted({w for w in words if words.count(w) > 1})
    assert not said_twice, f"the composed sentence says {said_twice} twice: {text}"
    return text


def test_the_offer_reads_as_one_sentence(ui, ui_page):
    """The save offer: lead-in plus the server's fragment, rendered and read back."""
    _did_real_work(ui, ui_page, "Plan the migration.")
    text = _composed_sentence(ui_page)
    assert "23 turns" in text, text
    assert "playbook" in text, text


def test_a_seeded_conversations_offer_reads_as_one_sentence_too(ui, ui_page):
    """The UPDATE fragment is written separately from the save one, so it collides separately:
    a conversation bound to a playbook is offered the refinement, in the same single sentence."""
    _did_real_work(ui, ui_page, "Research the suppliers.", playbook="research-and-report")
    text = _composed_sentence(ui_page)
    assert "research-and-report" in text, text


def test_a_short_conversation_renders_no_offer_at_all(ui, ui_page):
    """The negative half of the hook: below the threshold nothing is composed, so the locator
    the two cases above assert on must be absent rather than empty — otherwise a render that
    stopped happening would pass them both."""
    _did_real_work(ui, ui_page, "Quick question.", turns=2)
    expect(ui_page.locator(".conv-composer")).to_be_visible()
    expect(ui_page.locator(OFFER)).to_have_count(0)
