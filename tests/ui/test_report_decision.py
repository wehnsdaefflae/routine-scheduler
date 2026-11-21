"""F621 — a routine blocked on the OPERATOR is visible to him, on the pages he reads."""

from __future__ import annotations

from playwright.sync_api import expect

QUESTION = """**Enumerating `own_dir`'s writable children**

It changes what every `shell`-holding routine can write, so it needs your call before I build it.

*(R2471 — **self-audit** owns the work and will be told what you decide.)*"""


def _seed(ui, qid="q-20261011-120001-4"):
    return ui.seed_question("uir", qid, QUESTION,
                            options=["enumerate them", "leave it as one root"],
                            extra={"type": "report", "report": "R2471",
                                   "report_target": "self-audit"})


def test_a_report_born_decision_is_an_answerable_card_naming_its_report(ui, ui_page):
    _seed(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()

    # the chip says what kind of decision this is, which report it came from, and who acts
    chip = card.locator(".chip", has_text="report R2471")
    expect(chip).to_be_visible()
    expect(chip).to_contain_text("self-audit")

    # it is an ordinary open decision: deferred, with his two picks as buttons
    expect(card.locator(".chip", has_text="deferred")).to_be_visible()
    expect(card.get_by_role("button", name="1 · enumerate them")).to_be_visible()
    expect(card.get_by_role("button", name="2 · leave it as one root")).to_be_visible()

    # …and the report's own evidence reached him as prose, not as literal markdown
    expect(card.locator("strong").first).to_contain_text("Enumerating")
    assert "**Enumerating" not in card.inner_text()


def test_the_routine_card_counts_it_so_nothing_is_silently_blocked(ui, ui_page):
    """The other half of the hole: the dashboard said `0 open questions` for a routine that
    was waiting on him. The count is the one the Decisions badge uses, so the two can never
    disagree."""
    _seed(ui)
    ui_page.goto(f"{ui.url}/#/routines")
    # the row's own chip — "1 open ?" in list view, "1 open question" in card view, and in
    # both a LINK to where he answers it, so the count is never a dead badge
    chip = ui_page.locator("a.chip.blocking").first
    expect(chip).to_be_visible()
    expect(chip).to_contain_text("1 open")
    expect(chip).to_have_attribute("href", "#/questions")
    # …and the nav badge agrees with it: one decision owed, instance-wide
    expect(ui_page.locator('nav a[href="#/questions"]').first).to_contain_text("1")


def test_answering_it_from_the_page_settles_the_card(ui, ui_page):
    """He answers it where he reads it — the same control every other decision has. The
    receipt keeps the report chip, so the settled card still says what it disposed of."""
    _seed(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    card.get_by_role("button", name="1 · enumerate them").click()
    expect(card.locator(".chip.ok")).to_contain_text("answered")
    expect(card.locator(".chip", has_text="report R2471")).to_be_visible()
