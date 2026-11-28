"""A card the ASKING routine took back, rendered in a real browser (F943)."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.ui


def _withdraw(ui, ui_page, qid: str, verb: str, body: dict, slug: str = "uir"):
    """Post the withdrawal the way the asking routine's run does — through the API."""
    return ui_page.evaluate(
        "async ([qid, verb, body]) => {"
        " const m = await import('/static/api.js');"
        " return m.api(`/api/questions/${qid}/${verb}`, { method: 'POST', body });"
        " }", [qid, verb, body])


def test_a_retracted_card_shows_the_reason_in_place_of_an_answer_box(ui, ui_page):
    """The defect, through the page: the stale card is gone from the open set, and what the
    operator reads where it was is WHY it was withdrawn — not a second card for one matter."""
    ui.seed_question("uir", "q-wd1", "Should we rebuild the exporter?")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item")).to_contain_text("rebuild the exporter")

    _withdraw(ui, ui_page, "q-wd1", "retract",
              {"reason": "the log line it rested on was my own test fixture"})
    ui_page.reload()

    settled = ui_page.locator(".question-item.answered")
    expect(settled).to_be_visible()
    expect(settled.locator(".chip.partial")).to_contain_text("retracted by the routine")
    expect(settled.locator(".chip.partial")).to_contain_text("my own test fixture")
    # no answer was ever given, so nothing may offer to amend one — and the open card's
    # answer box must be gone, or the page invites an answer to a question that is not there
    expect(settled.get_by_role("button", name="revise")).to_have_count(0)
    expect(ui_page.locator('textarea[data-persist="answer-q-wd1"]')).to_have_count(0)
    expect(settled.locator(".chip.ok")).to_have_count(0)        # not "answered · queued"


def test_a_superseded_card_names_the_question_that_is_live_now(ui, ui_page):
    """One live card per matter: the folded one stays as history and says where the matter
    went, the way a folded report's card names the report that took it over."""
    ui.seed_question("uir", "q-old", "Raise the budget to 600?")
    ui.seed_question("uir", "q-new", "Raise the budget to 450?")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item")).to_have_count(2)

    _withdraw(ui, ui_page, "q-old", "supersede",
              {"by": "q-new", "reason": "450 is what the measurement supports"})
    ui_page.reload()

    # the carrier is still an OPEN card, answerable
    open_card = ui_page.locator(".question-item:not(.answered)")
    expect(open_card).to_have_count(1)
    expect(open_card).to_contain_text("Raise the budget to 450?")
    # and the folded one reads as history, naming its carrier
    folded = ui_page.locator(".question-item.answered")
    expect(folded.locator(".chip.partial")).to_contain_text("superseded by q-new")
    expect(folded).to_contain_text("q-new")


def test_a_withdrawn_card_is_not_counted_among_what_waits_on_the_operator(ui, ui_page):
    """The toolbar's counts and the page's own "does anything need me?" answer: a withdrawn
    card is history, so All reads 0 and the "nothing waits on you" note appears."""
    ui.seed_question("uir", "q-wd3", "An ordinary question?")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.get_by_role("button", name="All · 1")).to_be_visible()

    _withdraw(ui, ui_page, "q-wd3", "retract", {"reason": "premise gone"})
    ui_page.reload()

    expect(ui_page.get_by_role("button", name="All · 0")).to_be_visible()
    expect(ui_page.locator(".question-item.answered")).to_be_visible()
