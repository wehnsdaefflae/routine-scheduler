"""The offer under a conversation's FINAL reply (D307), asserted where it is composed — in the
browser. The operator decided (code review) that saving a conversation as a playbook must be
reachable where a conversation ENDS, and the ending is the reply whose finish carries
`final: true`: a flag `loopend.finish_run` has always written and nothing in `static/` read.
Three things are checked here and nowhere else: a finished conversation renders the offer
between the chat and the composer with its save control, a conversation that is merely long
renders it in the OTHER place (the composer row, so the two never ask at once), and a
conversation still working renders no ending offer at all.
"""

from __future__ import annotations

import json

import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .helpers import start_conversation

#: The hook `finalOffer` mounts on its wrapper WHATEVER the verdict, so a test can wait for the
#: paint before asserting absence (the empty-library lesson of tests/ui/test_playbook_cards.py:
#: `to_have_count(0)` is satisfied the instant it is asked).
WRAP = "[data-final-offer]"
#: The composed sentence inside it.
ASK = "[data-pb-final]"
#: The composer row's offer — D175's, which must NOT render beside this one.
TURNS_OFFER = "[data-pb-offer]"

LEAD_IN = "This conversation is done — "


def _ended(ui, page, text: str, *, turns: int = 3, final: bool | None = True,
           state: str = "finished", playbook: str = "") -> str:
    """A conversation whose newest reply declared itself `final` on a terminal run — the shape
    the offer fires on. `turns` stays BELOW the eight-turn bar of D175's other reason unless a
    case raises it, so what is asserted here cannot be that reason firing."""
    slug, conv = start_conversation(ui, page, text)
    run_dir = ui.seed_run(slug, "20261105-090000", state, summary="did the thing",
                          home=ui.conversations)
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    status["turn"] = turns
    if final is not None:
        status["final"] = final
    atomic_write_json(run_dir / "status.json", status)
    if playbook:
        path = conv / "routine.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg["playbook"] = {"slug": playbook, "commit": "0" * 8}
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    page.reload()
    return slug


def test_a_finished_conversation_offers_to_save_itself_as_a_playbook(ui, ui_page):
    """The offer reads as one sentence about this conversation's ending, and carries the save
    control beside it — the same POST the composer's button makes."""
    _ended(ui, ui_page, "Plan the migration.")
    expect(ui_page.locator(WRAP)).to_have_attribute("data-final-offer", "final")
    ask = ui_page.locator(ASK)
    expect(ask).to_have_count(1)
    text = ask.inner_text().strip()
    assert text.startswith(LEAD_IN), f"the lead-in changed; this case pins it: {text}"
    assert text.endswith("?"), f"the offer is not a question: {text}"
    assert text.count("?") == 1, f"the server's fragment brought its own punctuation: {text}"
    assert "playbook" in text, text
    expect(ui_page.locator(".pb-final").get_by_role(
        "button", name="＋ save as playbook")).to_be_visible()


def test_a_finished_conversation_seeded_from_a_playbook_offers_the_update(ui, ui_page):
    """`mode: "update"` is the existing refinement path (PUT), and it is what a conversation
    started FROM a playbook must be offered instead of a near-duplicate."""
    _ended(ui, ui_page, "Research the suppliers.", playbook="research-and-report")
    expect(ui_page.locator(ASK)).to_contain_text("research-and-report")
    expect(ui_page.locator(".pb-final").get_by_role(
        "button", name="⟳ update playbook: research-and-report")).to_be_visible()


def test_the_ending_offer_replaces_the_composers_one_rather_than_doubling_it(ui, ui_page):
    """One predicate, two surfaces, exactly ONE ask. This conversation satisfies BOTH reasons —
    twenty-three turns AND a final reply — so it must be asked at its ending and nowhere else;
    without the `why` discriminator both lines render and the user is asked the same question
    twice on one screen."""
    _ended(ui, ui_page, "Plan the migration.", turns=23)
    expect(ui_page.locator(ASK)).to_have_count(1)
    expect(ui_page.locator(TURNS_OFFER)).to_have_count(0)


def test_a_long_conversation_that_has_not_ended_is_still_asked_at_the_composer(ui, ui_page):
    """The negative half that keeps D175 working: no `final` declaration means no ending, so
    the ending offer is absent and the composer's own offer is the one that renders. Without
    this case the change could silently move every offer to the new place."""
    _ended(ui, ui_page, "Plan the migration.", turns=23, final=None)
    expect(ui_page.locator(WRAP)).to_have_attribute("data-final-offer", "turns")
    expect(ui_page.locator(ASK)).to_have_count(0)
    expect(ui_page.locator(TURNS_OFFER)).to_have_count(1)


def test_a_conversation_still_working_is_not_called_done(ui, ui_page):
    """A reply that declared itself final and was then resumed is live again: the run state is
    the other half of the predicate. The offer must be absent — and the wrapper's stamp is what
    proves the paint ran at all, rather than the assertion passing before the render."""
    _ended(ui, ui_page, "Plan the migration.", final=True, state="running")
    expect(ui_page.locator(WRAP)).to_have_count(1)
    expect(ui_page.locator(ASK)).to_have_count(0)
