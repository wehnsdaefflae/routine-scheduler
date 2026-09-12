"""The Decisions page's "answer & run now": answering a deferred question files the answer for
the NEXT scheduled run and fires nothing; the second button files it AND fires one manual run —
the operator's click, never a side effect of answering (0.333.0, reversing the answer wake).
The active-run and blocking cases are proven server-side (tests/test_api.py): a blocking record
with no live run behind it is presented as deferred by the read model, so it is not a UI case."""

from __future__ import annotations

from playwright.sync_api import expect

from rsched.paths import read_json


def _seed(ui, qid):
    ui.seed_question("uir", qid, "Shall I draft the reply?", mode="deferred",
                     options=["Draft it", "Leave it"], default="Wait for the next run")


def test_plain_answer_files_and_fires_nothing(ui, ui_page):
    _seed(ui, "q-20260912-120000-1")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible(timeout=10_000)
    expect(card.locator("[data-answer-run-now]")).to_have_text("answer & run now")
    card.locator("textarea.answer-input").fill("later is fine")
    card.get_by_role("button", name="answer", exact=True).click()
    expect(card).to_contain_text("answered · queued", timeout=10_000)
    ui_page.wait_for_timeout(300)
    answer = read_json(ui.routines / "uir" / "inbox" / "answer-q-20260912-120000-1.json")
    assert answer["text"] == "later is fine"
    assert ui.runner.fired == []


def test_answer_and_run_now_fires_one_manual_run(ui, ui_page):
    _seed(ui, "q-20260912-120000-2")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible(timeout=10_000)
    card.locator("textarea.answer-input").fill("Do it")
    card.locator("[data-answer-run-now]").click()
    expect(card).to_contain_text("answered · run started", timeout=10_000)
    ui_page.wait_for_timeout(300)
    answer = read_json(ui.routines / "uir" / "inbox" / "answer-q-20260912-120000-2.json")
    assert answer["text"] == "Do it"
    assert ui.runner.fired == [("uir", "manual")]
