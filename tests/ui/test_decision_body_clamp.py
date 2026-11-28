"""The OPEN decision card's question body is height-bounded, driven in a real browser (R3811)."""

import re

from playwright.sync_api import expect

#: Long enough to overflow twelve lines of the body's own prose at any plausible viewport, and
#: shaped like what actually overflows on this page: a proposal's title then its claims.
LONG = "A merge queue for main\n\n" + "\n\n".join(
    f"Claim {n}: the same sentence repeated so the rendered body is unmistakably taller than "
    f"twelve lines of prose, which is what the clamp is measured against." for n in range(1, 9))

CLAMPED = ".question-item .q-text.q-clamped"
READALL = "[data-question-readall]"


def test_a_long_open_question_is_clamped_and_offers_to_read_all(ui, ui_page):
    """The defect: an open card rendered its whole body with no height bound, so the group that
    nothing waits on took the most vertical weight and pushed its own answer form off-screen."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-long", LONG, asked="20260714-070000")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="A merge queue for main")
    expect(card).to_have_count(1)
    body = card.locator(".q-text")
    expect(body).to_have_count(1)
    expect(body).to_have_class(re.compile(r"\bq-clamped\b"))

    toggle = card.locator(READALL)
    expect(toggle).to_have_count(1)
    expect(toggle).to_have_text("read all")
    # the bound is real: the rendered body is shorter than the content it holds
    assert body.evaluate("n => n.scrollHeight > n.clientHeight + 1")

    toggle.click()
    expect(toggle).to_have_text("collapse")
    expect(card.locator(".q-text.q-clamped")).to_have_count(0)
    assert body.evaluate("n => n.scrollHeight <= n.clientHeight + 1")


def test_a_blocking_question_is_never_clamped(ui, ui_page):
    """A run is parked on it, so reading all of it IS the page's business — the one group where
    unbounded height is correct, and the reason the clamp is not simply global."""
    ui.seed_run("uir", "20260715-110000", "waiting_user",
                question={"qid": "q-blk", "question": LONG, "options": [],
                          "default": "keep both", "asked": "20260715-110000"})
    ui.seed_question("uir", "q-blk", LONG, mode="blocking", default="keep both",
                     asked="20260715-110000")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="A merge queue for main")
    expect(card).to_have_count(1)
    expect(card.locator(".q-text")).to_have_count(1)
    expect(card.locator(".q-text.q-clamped")).to_have_count(0)
    expect(card.locator(READALL)).to_have_count(0)


def test_a_short_question_gets_no_clamp_and_no_control(ui, ui_page):
    """A control on a two-line question is noise: the toggle is built only where the body
    really overflows, measured after layout rather than guessed from the text."""
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", "q-short", "Rename fau's two ladder keys?", asked="20260714-070000")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="ladder keys")
    expect(card).to_have_count(1)
    expect(card.locator(".q-text")).to_have_count(1)
    expect(card.locator(CLAMPED)).to_have_count(0)
    expect(card.locator(READALL)).to_have_count(0)
