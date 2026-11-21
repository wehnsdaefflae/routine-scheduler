"""An answer this console just sent is never repainted as open (questions-store.js)."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import until
from .helpers import hold_requests, release

ANSWER = re.compile(r"/api/questions/q-color/answer$")
LIST = re.compile(r"/api/questions(\?settled=\d+)?$")   # the page asks for its window


def test_a_stale_list_does_not_reopen_an_answered_question(ui, ui_page):
    """The exact race: a list read is in flight when the answer lands, and its reply —
    which predates the answer — must not rebuild the open form."""
    ui.seed_question("uir", "q-color", "Which color should the report use?",
                     options=["red", "blue"])
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()

    # A list read that BEGINS now, i.e. before the answer exists: held open so it cannot
    # finish first. Releasing it after the answer is what the bus repaint used to do.
    held_lists = hold_requests(ui_page, LIST)
    ui_page.evaluate("() => window.dispatchEvent(new CustomEvent('rsched-bus', "
                     "{ detail: { event: 'run_state' } }))")
    until(lambda: held_lists, what="the in-flight list read", page=ui_page)

    card.get_by_role("button", name="1 · red").click()
    expect(card.locator(".chip.ok")).to_contain_text("answered")

    release(ui_page, LIST, held_lists)       # the stale reply arrives AFTER the answer
    ui_page.wait_for_timeout(600)

    # The settled state stands, and no fresh answer control is offered for it.
    expect(card.locator(".chip.ok")).to_contain_text("answered")
    assert card.get_by_role("button", name="1 · red").count() == 0, \
        "the stale list rebuilt an enabled answer button over the settled card"


def test_a_fresh_list_read_still_speaks_for_the_server(ui, ui_page):
    """The healing is not a lock: a read that BEGAN after the answer is authoritative, so
    the store stops substituting and an expiry, a revision or a deletion gets through."""
    ui.seed_question("uir", "q-color", "Which color should the report use?",
                     options=["red", "blue"])
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()
    card.get_by_role("button", name="1 · red").click()
    expect(card.locator(".chip.ok")).to_contain_text("answered")

    marked = ui_page.evaluate("""async () => {
      const m = await import('/static/questions-store.js');
      m.markAnswered('q-gone', 'x');
      const snap = await m.loadQuestions();          // a read that begins AFTER the mark
      return snap.items.some((q) => q.qid === 'q-gone');
    }""")
    assert marked is False, "a question the server no longer lists was kept alive by the mark"


def test_an_intermediate_reply_is_not_marked_answered(ui, ui_page):
    """An ask-back leaves the question OPEN by design, so it must never be recorded as
    answered — the store would otherwise hide a question that is still waiting.
    """
    ui.seed_question("uir", "q-color", "Which color should the report use?", mode="blocking")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item")).to_be_visible()

    mounted = ui_page.evaluate("""async () => {
      const af = await import('/static/components/answerform.js');
      const form = af.answerForm({ qid: 'q-color', question: 'x', options: [], mode: 'blocking' },
        { askBack: true, submitText: async () => ({ ok: true }) });
      document.body.appendChild(form.node);
      form.input.value = 'what do you mean by color?';
      window.__f780form = form;
      return true;
    }""")
    assert mounted

    held_lists = hold_requests(ui_page, LIST)          # a read that BEGINS before the reply
    ui_page.evaluate("() => window.dispatchEvent(new CustomEvent('rsched-bus', "
                     "{ detail: { event: 'run_state' } }))")
    until(lambda: held_lists, what="the in-flight list read", page=ui_page)

    ui_page.evaluate("() => window.__f780form.submit(true)")    # the INTERMEDIATE reply
    ui_page.wait_for_timeout(200)
    release(ui_page, LIST, held_lists)
    ui_page.wait_for_timeout(600)

    answered = ui_page.evaluate("""() => {
      const q = (window.__rschedLastQuestions || []).find((x) => x.qid === 'q-color');
      return q ? Boolean(q.answered) : null;
    }""")
    if answered is None:                 # no debug hook: read the page's own settled chip
        answered = ui_page.locator(".question-item .chip.ok").count() > 0
    assert answered is not True, "an intermediate reply marked a still-open question answered"
    # The card itself still asks (scoped to the page's card: this test mounted a second form
    # of its own, so an unscoped selector matches both).
    expect(ui_page.locator('.question-item textarea[data-persist="answer-q-color"]')
           ).to_be_visible()
