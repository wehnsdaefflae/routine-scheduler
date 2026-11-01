"""The CLOSING asks on a finish banner, in the browser — D212 increment (b).

The engine selects them (`engine/closingasks.py`, `tests/test_closing_asks.py`); these pin what
the PERSON sees: the questions his run left him, their options as buttons, under the summary he
is already reading — and that answering one there goes through the ONE answer route, not a
second poster built for this surface.

Every assertion about a control first asserts the control EXISTS (`to_have_count(1)`). A browser
assertion about an element's style or content that does not is a test that passes over a console
which lost the behaviour — measured 2026-10-31, when a sibling check in another file had been
passing vacuously for exactly that reason.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect


def _finished_run_with(ui, slug: str, ts: str, payload_extra: dict):
    run_dir = ui.seed_run(slug, ts, "finished", summary="done")
    finish = {"ts": "2026-11-01T15:05:00+00:00", "type": "finish", "turns": 3,
              "usage_total": {"in": 10, "out": 5},
              "payload": {"status": "ok", "summary": "**Work:** the thing is done.",
                          "authored": True, **payload_extra}}
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(finish) + "\n")
    return run_dir


def test_a_closing_ask_is_answerable_on_the_finish_banner(ui, ui_page):
    """One question, its options as buttons, inside the finish banner. The point of the
    increment: the answer is one click away where the result is read, instead of a card on a
    page he visits separately.
    """
    _finished_run_with(ui, "uica", "20261101-150000", {"closing_asks": [
        {"qid": "q-20261101-150000-7", "question": "keep polling hourly?",
         "options": ["hourly", "daily"], "default": "I keep polling hourly"}]})
    ui_page.goto(f"{ui.url}/#/run/uica:20261101-150000")
    banner = ui_page.locator(".finish-banner")
    expect(banner).to_have_count(1)
    block = banner.locator("[data-closing-asks]")
    expect(block).to_have_count(1)                      # present before anything about it
    expect(block).to_have_attribute("data-closing-asks", "1")
    ask = block.locator('.closing-ask[data-qid="q-20261101-150000-7"]')
    expect(ask).to_have_count(1)
    expect(ask).to_contain_text("keep polling hourly?")
    # it says the run did NOT wait on this — a block of questions under a finished run would
    # otherwise read as something having gone wrong
    expect(block).to_contain_text("finished without the answer")
    # the options are real buttons of the ONE answer form, and its default line is there
    for label in ("hourly", "daily"):
        expect(ask.get_by_role("button", name=label)).to_have_count(1)
    expect(ask).to_contain_text("I keep polling hourly")


def test_several_closing_asks_each_get_their_own_form(ui, ui_page):
    """They were asked together only in the sense that one run asked them; each is answered on
    its own, so each renders its own form rather than one form for the batch.
    """
    _finished_run_with(ui, "uicb", "20261101-151000", {"closing_asks": [
        {"qid": "q-20261101-151000-4", "question": "first question?", "options": ["a"]},
        {"qid": "q-20261101-151000-5", "question": "second question?", "options": ["b"]}]})
    ui_page.goto(f"{ui.url}/#/run/uicb:20261101-151000")
    block = ui_page.locator(".finish-banner [data-closing-asks]")
    expect(block).to_have_count(1)
    expect(block).to_have_attribute("data-closing-asks", "2")
    expect(block.locator(".closing-ask")).to_have_count(2)
    expect(block).to_contain_text("finished without the answers")   # plural wording
    expect(block.locator(".answer-input")).to_have_count(2)


def test_a_finish_with_no_closing_asks_shows_no_block_at_all(ui, ui_page):
    """The overwhelming majority of runs. The engine omits the key rather than sending an empty
    list, and the banner must look exactly as it did before this increment.
    """
    _finished_run_with(ui, "uicc", "20261101-152000", {})
    ui_page.goto(f"{ui.url}/#/run/uicc:20261101-152000")
    banner = ui_page.locator(".finish-banner")
    expect(banner).to_have_count(1)
    expect(banner).to_contain_text("the thing is done")
    expect(banner.locator("[data-closing-asks]")).to_have_count(0)


def test_answering_on_the_banner_posts_to_the_one_answer_route(ui, ui_page):
    """No second poster: this surface uses the same `POST /api/questions/<qid>/answer` every
    other one does, which is what keeps the answer shape from forking (`answerform.js`'s own
    header says so).
    """
    _finished_run_with(ui, "uicd", "20261101-153000", {"closing_asks": [
        {"qid": "q-20261101-153000-2", "question": "which shape?", "options": ["left"]}]})
    ui_page.goto(f"{ui.url}/#/run/uicd:20261101-153000")
    button = ui_page.locator('.closing-ask[data-qid="q-20261101-153000-2"]').get_by_role(
        "button", name="left")
    expect(button).to_have_count(1)
    with ui_page.expect_request(
            lambda r: r.method == "POST"
            and r.url.endswith("/api/questions/q-20261101-153000-2/answer")) as sent:
        button.click()
    assert sent.value.post_data_json.get("text") == "left", sent.value.post_data_json
