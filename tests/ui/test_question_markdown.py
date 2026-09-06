"""A question is BLOCK prose, on every surface that shows one.

A run lays out what it found before it asks — the ask-policy rule and the deliberation contract
both push it to — so an ordinary deferred question arrives carrying a GFM table of counts, a
numbered list and a fenced snippet. Rendered with the inline-only subset those reach the page as
literal pipes and asterisks, in the one place a person has to read carefully enough to decide.

Reported from the live console: a freelance-radar question whose whole evidence table
(`| sent | 17 |`, `| won | 0 |`) sat on the Decisions page as raw markdown while the **bold** and
the `code` around it rendered — the tell that the renderer was the inline one, not that markdown
was off.

md() is a superset of mdInline(), so the block renderer costs nothing anywhere it replaced it.
"""

from __future__ import annotations

from playwright.sync_api import expect

QUESTION = """Straight answer: yes, but here is the honest whole of it.

**17 applications have actually gone out**, and I owe you the uncomfortable half:

| fact | number |
|---|---|
| sent | 17 |
| won | 0 |

I built `job_reply.py` to post the reply once you approve the exact text.

What do you want me to do about the send gap?"""


def _seed(ui, qid="q-20260906-063000-1", mode="deferred"):
    ui.seed_question("uir", qid, QUESTION, mode=mode,
                     options=["Draft the reply now", "Drop that channel"],
                     default="Proceed with today's radar")
    return qid


def test_a_questions_table_renders_as_a_table_on_the_decisions_page(ui, ui_page):
    _seed(ui)
    ui_page.set_viewport_size({"width": 1400, "height": 1000})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible(timeout=10_000)

    # the table is a TABLE, not four lines of pipes
    table = card.locator("table")
    expect(table).to_be_visible()
    expect(table.locator("th").first).to_have_text("fact")
    expect(table.locator("td")).to_contain_text(["sent", "17", "won", "0"])

    body = card.inner_text()
    assert "|---|" not in body                      # the separator row is consumed
    assert "| sent | 17 |" not in body              # …and so is every data row
    assert "**17 applications" not in body          # bold rendered, not literal


def test_the_answer_card_renders_the_body_as_a_block(ui, ui_page):
    """The card carrying the answer box — the one the user reads before deciding. Its label
    sits on its own line precisely so the body can be a block."""
    _seed(ui)
    ui_page.set_viewport_size({"width": 1400, "height": 1000})
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item").first).to_be_visible(timeout=10_000)
    # the answer affordance and the rendered table live in the same card
    card = ui_page.locator(".question-item").first
    expect(card.locator("textarea")).to_be_visible()
    expect(card.locator("table")).to_be_visible()


def test_the_routine_page_strip_previews_one_line_and_does_not_break_the_row(ui, ui_page):
    """The routine page shows the question beside an `answer` button, so it is a PREVIEW: one
    line, the way a run summary previews on the dashboard. A block body cannot sit in a row —
    and the full text is one click away, where it is answered."""
    _seed(ui)
    ui_page.set_viewport_size({"width": 1400, "height": 1000})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    row = ui_page.locator(".panel.warn .row.spread").first
    expect(row).to_be_visible(timeout=10_000)
    text = row.inner_text()
    assert "Straight answer" in text                # the first line
    assert "| sent | 17 |" not in text              # never the raw table
    assert row.locator("table").count() == 0        # and no block inside the row
