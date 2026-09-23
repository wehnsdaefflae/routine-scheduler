"""What you answered stays on the page, and you can change it (F525).

The operator's report, 2026-09-21: *"i just answered a question asked by this routine... i
accidentally answered just one of the four questions. why can't i see this answer in the
routine's inbox? i want to revise it to add the other answers."*

Both halves had one cause. `collect_deferred_answers` consumed the inbox answer file AND
unlinked the pending record, writing nothing in their place — and the Decisions page derives
"answered" by reading exactly those two. So the settled card, with his own words in it,
disappeared the moment the next run booted, and no surface could offer a revision of an
answer the system no longer had.

These flows drive the REAL console across that boundary: answer, let a run consume it,
reload, and the answer is still readable and still amendable. The file on disk is the proof,
because that file is what the next run actually reads.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from rsched.engine import inbox
from rsched.paths import read_json

from .conftest import until


def _seed(ui, qid):
    ui.seed_question("uir", qid, "Four things only you can do",
                     mode="deferred", options=[], default="keep them blocked")


def _consume(ui, run="20260921-132502"):
    """What a run's boot does: take the answer and drop the pending record."""
    return inbox.collect_deferred_answers(
        ui.routines / "uir", ui.routines / "uir" / "runs" / run / "consumed")


def test_an_answer_a_run_has_read_is_still_on_the_page(ui, ui_page):
    qid = "q-20260921-113706-212"
    _seed(ui, qid)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    card.locator("textarea.answer-input").fill("1. i opened the tailgate port")
    card.get_by_role("button", name="answer", exact=True).click()
    expect(card).to_contain_text("answered")
    until((ui.routines / "uir" / "inbox" / f"answer-{qid}.json").exists,
          what="the answer file")

    pairs = _consume(ui)
    assert [p["answer"] for p in pairs] == ["1. i opened the tailgate port"]

    # the boundary the operator fell off: reload AFTER the run took the answer
    ui_page.reload()
    settled = ui_page.locator(".question-item.answered").first
    expect(settled).to_be_visible()
    expect(settled).to_contain_text("i opened the tailgate port")
    expect(settled).to_contain_text("acted on")
    expect(settled.get_by_role("button", name="revise")).to_be_visible()


def test_revising_a_read_answer_re_queues_it_for_the_next_run(ui, ui_page):
    qid = "q-20260921-113706-213"
    _seed(ui, qid)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    card.locator("textarea.answer-input").fill("1. it works now")
    card.get_by_role("button", name="answer", exact=True).click()
    expect(card).to_contain_text("answered")
    until((ui.routines / "uir" / "inbox" / f"answer-{qid}.json").exists,
          what="the answer file")
    _consume(ui)

    ui_page.reload()
    settled = ui_page.locator(".question-item.answered").first
    expect(settled).to_be_visible()
    settled.get_by_role("button", name="revise").click()
    box = settled.locator("textarea.answer-input")
    expect(box).to_be_visible()
    box.fill("1. it works now\n2. copy the gmail creds over\n3. it can request the grant itself")
    settled.get_by_role("button", name="save revision").click()
    # The card says the amendment went BACK to the inbox for a run to read — the whole claim of
    # revising something already acted on. It says it twice over: the save writes the line in
    # place, and the shared questions store repaints the card from the server a moment later
    # ("→ inbox → consumed by the next run", against "→ read by the run that asked" before the
    # revision). The two moments are one statement, so the assertion is on the statement.
    expect(settled).to_contain_text(re.compile(r"→ inbox.*next run"))
    revised = ui.routines / "uir" / "inbox" / f"answer-{qid}.json"
    until(lambda: revised.exists() and read_json(revised).get("revised"),
          what="the revision")

    # the amendment is queued where a run will read it — the whole point of revising
    queued = read_json(revised)
    assert "3. it can request the grant itself" in queued["text"]
    assert queued["revised"] is True
    pairs = _consume(ui, run="20260921-140000")
    assert len(pairs) == 1
    assert "copy the gmail creds over" in pairs[0]["answer"]
    assert pairs[0]["question"] == "Four things only you can do"
