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


# ---- the REVISABLE half of the brief (D212): "answered AND revisable in the final summary"


def test_what_you_answered_stays_on_the_banner_with_a_way_to_change_it(ui, ui_page):
    """Answering here used to be final the moment it posted: the form locked and nothing came
    after it. The Decisions page has had a revise path since F525, and the operator's brief asks
    for the answer to be *revisable in the run's final summary* — so the receipt keeps what you
    said and offers the same affordance where you are already reading.
    """
    # On the harness's ONE real routine (`uir`): the record behind the banner payload has to be
    # FINDABLE, and `decisions_read.find_question` walks only dirs carrying a routine.yaml —
    # the other tests here need no record, so they can use a bare seeded slug.
    ui.seed_question("uir", "q-20261101-154000-3", "which shape?", options=["left"])
    _finished_run_with(ui, "uir", "20261101-154000", {"closing_asks": [
        {"qid": "q-20261101-154000-3", "question": "which shape?", "options": ["left"]}]})
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-154000")
    ask = ui_page.locator('.closing-ask[data-qid="q-20261101-154000-3"]')
    expect(ask.get_by_role("button", name="left")).to_have_count(1)

    ask.get_by_role("button", name="left").click()

    expect(ask).to_contain_text("left")                      # your own words, kept
    expect(ask).to_contain_text("it reaches the next run")
    revise = ask.get_by_role("button", name="revise", exact=True)
    expect(revise).to_have_count(1)
    # …and the answer box is gone: the receipt REPLACES the form rather than sitting beside it,
    # so one ask never shows an open box and a settled answer at the same time.
    expect(ask.locator(".answer-input")).to_have_count(0)


def test_revising_on_the_banner_posts_the_new_text_to_the_revise_route(ui, ui_page):
    """The whole point of the affordance: answering one of three closing asks and then wanting
    to extend it is ordinary. It goes to the SAME `/revise` route the Decisions page uses, which
    rewrites a queued answer and re-queues one a run already read — not to a second route built
    for this surface.
    """
    # The durable record BEHIND the banner payload, on the harness's one real routine: a live
    # closing ask always has one (`inbox_questions.file_question` wrote it), `/revise` 404s
    # without it, and `find_question` only walks dirs carrying a routine.yaml — so a test that
    # seeded the finish event alone would prove the button posts and nothing more.
    ui.seed_question("uir", "q-20261101-155000-6", "which shape?", options=["left"])
    _finished_run_with(ui, "uir", "20261101-155000", {"closing_asks": [
        {"qid": "q-20261101-155000-6", "question": "which shape?", "options": ["left"]}]})
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-155000")
    ask = ui_page.locator('.closing-ask[data-qid="q-20261101-155000-6"]')
    ask.get_by_role("button", name="left").click()
    ask.get_by_role("button", name="revise", exact=True).click()

    box = ask.locator(".answer-input")
    expect(box).to_have_count(1)
    expect(box).to_have_value("left")                  # it opens on what you said, not empty
    box.fill("left, and keep the old one for a week")
    with ui_page.expect_request(
            lambda r: r.method == "POST"
            and r.url.endswith("/api/questions/q-20261101-155000-6/revise")) as sent:
        ask.get_by_role("button", name="save revision", exact=True).click()
    assert sent.value.post_data_json == {"text": "left, and keep the old one for a week"}, \
        sent.value.post_data_json
    expect(ask).to_contain_text("keep the old one for a week")   # the receipt shows the new text
