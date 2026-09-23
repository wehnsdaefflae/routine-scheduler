"""The Decisions page's "answer & run now": answering a deferred question files the answer for
the NEXT scheduled run and fires nothing; the second button files it AND fires one manual run —
the operator's click, never a side effect of answering (0.333.0, reversing the answer wake).
The active-run and blocking cases are proven server-side (tests/test_api.py): a blocking record
with no live run behind it is presented as deferred by the read model, so it is not a UI case."""

from __future__ import annotations

from playwright.sync_api import expect

from rsched.paths import read_json

from .conftest import until


def _seed(ui, qid):
    ui.seed_question("uir", qid, "Shall I draft the reply?", mode="deferred",
                     options=["Draft it", "Leave it"], default="Wait for the next run")


def test_plain_answer_files_and_fires_nothing(ui, ui_page):
    _seed(ui, "q-20260912-120000-1")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    expect(card.locator("[data-answer-run-now]")).to_have_text("answer & run now")
    card.locator("textarea.answer-input").fill("later is fine")
    card.get_by_role("button", name="answer", exact=True).click()
    expect(card).to_contain_text("answered · queued")
    filed = ui.routines / "uir" / "inbox" / "answer-q-20260912-120000-1.json"
    until(filed.exists, what="the answer file")
    answer = read_json(filed)
    assert answer["text"] == "later is fine"
    assert ui.runner.fired == []


def test_answer_and_run_now_fires_one_manual_run(ui, ui_page):
    _seed(ui, "q-20260912-120000-2")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    card.locator("textarea.answer-input").fill("Do it")
    card.locator("[data-answer-run-now]").click()
    expect(card).to_contain_text("answered · run started")
    filed = ui.routines / "uir" / "inbox" / "answer-q-20260912-120000-2.json"
    until(filed.exists, what="the answer file")
    answer = read_json(filed)
    assert answer["text"] == "Do it"
    assert ui.runner.fired == [("uir", "manual")]
    assert answer["ran_now"]
    ui_page.reload()
    expect(ui_page.locator(".question-item").first).to_contain_text(
        "answered · run started")


def test_answer_run_now_does_not_claim_a_refused_start(ui, ui_page, monkeypatch):
    _seed(ui, "q-refused-start")

    async def refuse_fire(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ui.runner, "fire", refuse_fire)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    card.locator("textarea.answer-input").fill("Save this answer")
    card.locator("[data-answer-run-now]").click()
    expect(card).to_contain_text("answered · queued")
    expect(card).not_to_contain_text("run started")
    answer = read_json(ui.routines / "uir" / "inbox" / "answer-q-refused-start.json")
    assert answer["text"] == "Save this answer"
    assert "ran_now" not in answer
    assert ui.runner.fired == []
    ui_page.reload()
    expect(ui_page.locator(".question-item").first).to_contain_text("answered · queued")

