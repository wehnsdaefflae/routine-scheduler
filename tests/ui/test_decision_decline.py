"""The Decisions page's first-class NO, driven in a real browser (R2197).

A decision could be answered, snoozed or deferred — none of which is a refusal — so an
operator who had read a proposal and did not want it could only type prose. This asserts the
claim the UI actually makes: there is a button, ONE click declines, the card then says
DECLINED rather than "answered", and the refusal is on disk as a value.

Two traps this project has paid for, both exercised here:

* A control that must not exist for some users is asserted ABSENT (`to_have_count(0)`), never
  merely invisible — an access request's "no" is the typed decision `deny_now`/`deny_forever`,
  and a decline button there would post a 400.
* The settled receipt's question is COLLAPSED by default (`details.q-settled`), so it is
  clicked open before anything inside it is asserted.
"""

import json

from playwright.sync_api import expect

from .helpers import visible_toast

DECLINE = "[data-decline]"


def _answer_file(ui, qid: str, slug: str = "uir") -> dict:
    return json.loads((ui.routine_dir(slug) / "inbox" / f"answer-{qid}.json")
                      .read_text(encoding="utf-8"))


def test_one_click_declines_and_the_card_says_declined(ui, ui_page):
    """The whole of "done looks like", through the page: one click, the item leaves the open
    set AS DECLINED (not as prose), and the asking routine's inbox holds `decision:
    "declined"` as a value."""
    ui.seed_question("uir", "q-dec", "Should we rebuild the exporter?")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Should we rebuild the exporter?")

    card.locator(DECLINE).click()
    expect(visible_toast(ui_page)).to_contain_text("declined")

    # on disk: the ORDINARY answer payload shape, carrying the value
    answer = _answer_file(ui, "q-dec")
    assert answer["decision"] == "declined"
    assert answer["source"] == "web" and answer["reason"] == ""
    assert answer["text"].split()[0].strip(".,") == "declined"

    # and the page, reloaded from the API, renders a REFUSAL rather than an answer
    ui_page.reload()
    settled = ui_page.locator(".question-item.answered")
    expect(settled).to_be_visible()
    expect(settled.locator(".chip.failed")).to_contain_text("declined")
    expect(settled.locator(".chip.ok")).to_have_count(0)        # not "answered · queued"
    # The receipt's question is behind a fold — click it open before reading it. The direct
    # child div, because the SUMMARY carries `q-text` as well (two matches = strict-mode red).
    body = settled.locator("details.q-settled > div.q-text")
    expect(body).to_be_hidden()
    settled.locator("details.q-settled summary").click()
    expect(body).to_contain_text("rebuild the exporter")


def test_whatever_is_typed_in_the_answer_box_rides_along_as_the_reason(ui, ui_page):
    """Declining stays ONE click: the operator's words, if he wrote any, become the reason —
    and the card shows them on the receipt, so a refusal is never anonymous."""
    ui.seed_question("uir", "q-dec2", "Add a second exporter?")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    card.locator('textarea[data-persist="answer-q-dec2"]').fill("the first one is unused")
    card.locator(DECLINE).click()
    expect(visible_toast(ui_page)).to_contain_text("declined")

    answer = _answer_file(ui, "q-dec2")
    assert answer["reason"] == "the first one is unused"
    assert "the first one is unused" in answer["text"]
    ui_page.reload()
    expect(ui_page.locator(".question-item.answered .chip.failed")).to_contain_text(
        "the first one is unused")


def test_an_access_request_card_has_no_decline_button_at_all(ui, ui_page):
    """An access request's refusal is the typed decision `deny_now` / `deny_forever` — the
    values the engine reads to deny the grant. The decline endpoint refuses that record with a
    400, so the control must be ABSENT, not hidden: a visible button that cannot work is worse
    than no button."""
    ui.seed_question("uir", "q-req", "May I write ~/project?",
                     request=["fs-write:~/project"])
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card.locator(".chip.partial", has_text="access request")).to_be_visible()
    expect(card.locator(DECLINE)).to_have_count(0)
    # the typed decisions ARE the "no" here, and they are still offered
    expect(card.get_by_role("button", name="deny now")).to_be_visible()


def test_a_blocking_card_offers_both_decline_and_defer(ui, ui_page):
    """A blocking ask parks a live run. Defer releases it WITHOUT deciding; decline is the
    decision — both belong on that card, and they are different acts."""
    ui.seed_run("uir", "20260715-090000", "waiting_user",
                question={"qid": "q-blk", "question": "Overwrite the export?", "options": [],
                          "default": "keep both", "asked": "20260715-090000"})
    ui.seed_question("uir", "q-blk", "Overwrite the export?", mode="blocking",
                     default="keep both")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item.warn")
    expect(card.get_by_role("button", name="defer to next run")).to_be_visible()
    card.locator(DECLINE).click()
    expect(visible_toast(ui_page)).to_contain_text("declined")
    assert _answer_file(ui, "q-blk")["decision"] == "declined"


def test_a_meta_decision_has_no_decline_button(ui, ui_page):
    """A self-audit decision lives in the report, not as a record — it can no more be declined
    than snoozed or deferred, and the endpoint 404s it. Absent, not hidden."""
    ui.seed_question("uir", "q-plain", "An ordinary question?")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item").locator(DECLINE)).to_have_count(1)
    # the lifecycle component refuses to build the control for a meta item; the page has no
    # meta fixture of its own here, so the invariant is asserted on the component's own guard
    assert ui_page.evaluate(
        "async () => { const m = await import('/static/components/decision-lifecycle.js');"
        " return m.lifecycleControls({ qid: 'q-m', meta: true, mode: 'deferred' }, {}).length; }"
    ) == 0
