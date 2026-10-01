"""An answer is sent once, however it is started (components/answerform.js).

The form disabled only its send button while the answer was in flight. A double-click on an
option button — or Enter pressed twice — posted the answer twice: two toasts, two `onSuccess`
calls, and on the Decisions page the second one spliced ANOTHER question's input out of the
arrow-key order (`indexOf` of the already-removed input is -1, and `splice(-1, 1)` drops the
last). A slow daemon is exactly when a person clicks again.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import until
from .helpers import hold_requests

ANSWER = re.compile(r"/api/questions/q-color/answer$")


def test_a_double_clicked_option_sends_one_answer(ui, ui_page):
    ui.seed_question("uir", "q-color", "Which color should the report use?",
                     options=["red", "blue"])
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()
    held = hold_requests(ui_page, ANSWER)

    card.get_by_role("button", name="1 · red").dblclick()
    until(lambda: held, what="the answer POST", page=ui_page)
    ui_page.wait_for_timeout(400)
    assert len(held) == 1, f"{len(held)} answer POSTs for one decision"
    expect(card.get_by_role("button", name="2 · blue")).to_be_disabled()   # all of it waits

    ui_page.unroute(ANSWER)                 # releases the held request with the handler
    expect(card.locator(".chip.ok")).to_contain_text("answered")


def test_enter_pressed_twice_sends_one_answer(ui, ui_page):
    ui.seed_question("uir", "q-color", "Which color should the report use?")
    ui_page.goto(f"{ui.url}/#/questions")
    box = ui_page.locator('textarea[data-persist="answer-q-color"]')
    expect(box).to_be_visible()
    held = hold_requests(ui_page, ANSWER)

    box.fill("teal")
    box.press("Enter")
    box.press("Enter")
    until(lambda: held, what="the answer POST", page=ui_page)
    ui_page.wait_for_timeout(400)
    assert len(held) == 1, f"{len(held)} answer POSTs for one answer"
    ui_page.unroute(ANSWER)
