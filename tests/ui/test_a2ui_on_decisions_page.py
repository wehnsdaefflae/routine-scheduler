"""The A2UI rendering ON THE DECISIONS PAGE — D310 step 5, where a person can actually reach it.

Steps 1-4 made the protocol real and served a decision through it; until this step the surface was
reachable only by the route and by its own tests. Here it is a fold under each open card: closed by
default (the card above is what the operator answers real work on, and swapping it for a rendering
he has not seen would be the wrong order), mounted on FIRST OPEN rather than on every repaint (a
page of decisions must not fetch an envelope per card), and answering inside it posts to the one
answer route and takes the card out of the open list exactly as the form does.
"""

from __future__ import annotations

from playwright.sync_api import expect


def test_an_open_decision_offers_the_a2ui_rendering_folded_shut(ui, ui_page):
    """Folded SHUT, and that is the decision rather than an oversight: the card is what the
    operator relies on, so the protocol rendering is offered beside it and nothing is replaced.
    Nothing is fetched while it is closed — the surface does not exist until the fold opens."""
    ui.seed_question("uir", "q-a2p-1", "Should the renderer ship hand-written?",
                     options=["yes", "no"])
    ui_page.goto(f"{ui.url}/#/questions")
    fold = ui_page.locator(".q-a2ui")
    expect(fold).to_have_count(1)
    expect(fold.locator("summary")).to_contain_text("render through A2UI")
    # Closed: no surface mounted, so no chip and no A2UI node anywhere on the page.
    expect(ui_page.locator(".a2-chip")).to_have_count(0)
    expect(ui_page.locator(".a2-surface")).to_have_count(0)


def test_opening_the_fold_mounts_the_surface_with_the_richer_elements(ui, ui_page):
    """What the operator's answer asked for, visible on his own page: the options as chips rather
    than a row of buttons, a long-text field, and the without-an-answer sentence in a modal —
    three element types this console has no equivalent for."""
    ui.seed_question("uir", "q-a2p-2", "Which route for the renderer?",
                     options=["a — hand-written", "b — upstream SDK"],
                     default="a — hand-written")
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.locator(".q-a2ui > summary").click()
    expect(ui_page.locator(".a2-chip")).to_have_count(2)
    expect(ui_page.locator(".a2-chip").first).to_have_text("a — hand-written")
    expect(ui_page.locator("textarea.a2-input")).to_have_count(1)
    expect(ui_page.locator("dialog.a2-dialog")).to_have_count(1)
    expect(ui_page.locator(".a2-error")).to_have_count(0)
    # The form the card already had is UNTOUCHED beside it: this is an addition, not a swap.
    expect(ui_page.locator(".question-item .answer-input, .question-item input.answer-input")
           ).not_to_have_count(0)


def test_answering_inside_the_surface_posts_to_the_one_answer_route(ui, ui_page):
    """No second poster and no second answer shape: the surface's agent event is routed to the
    same `POST /api/questions/{qid}/answer` the card's own form uses, and the card leaves the
    open list afterwards exactly as it does when the form posts."""
    ui.seed_question("uir", "q-a2p-3", "Ship it?", options=["ship", "hold"])
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.locator(".q-a2ui > summary").click()
    expect(ui_page.locator(".a2-chip")).to_have_count(2)
    with ui_page.expect_request(
            lambda r: r.method == "POST"
            and r.url.endswith("/api/questions/q-a2p-3/answer")) as sent:
        ui_page.locator(".a2-chip").first.click()
        ui_page.locator("button.a2-button-primary").click()
    assert sent.value.post_data_json == {"text": "ship"}, sent.value.post_data_json
    # The decision stops being OPEN. It does not VANISH: the store's next snapshot marks it
    # answered and the page re-renders it as the settled RECEIPT (F525 — what you said stays on
    # the page), carrying the same question text, so the property is the card's CLASS and not its
    # absence. And measured red-first: this holds even with the fold's own `onAnswered` filtering
    # neutered, because that bus snapshot does it anyway — the callback buys latency, not the
    # outcome, and this assertion is about the outcome.
    expect(ui_page.locator('.question-item.answered:has-text("Ship it?")')).to_have_count(1)
    expect(ui_page.locator('.question-item:not(.answered):has-text("Ship it?")')
           ).to_have_count(0)
    expect(ui_page.locator(".question-item.answered")).to_contain_text("ship")


def test_closing_the_fold_tears_the_surface_down(ui, ui_page):
    """The renderer subscribes to its surface store, so a fold opened and closed on every card of
    a long page would leak one subscription each. Closing removes the node, and re-opening mounts
    a fresh one — which is also what makes the repaint-heavy Decisions page safe to fold on."""
    ui.seed_question("uir", "q-a2p-4", "Again?", options=["yes"])
    ui_page.goto(f"{ui.url}/#/questions")
    summary = ui_page.locator(".q-a2ui > summary")
    summary.click()
    expect(ui_page.locator(".a2-surface")).to_have_count(1)
    summary.click()
    expect(ui_page.locator(".a2-surface")).to_have_count(0)
    summary.click()
    expect(ui_page.locator(".a2-surface")).to_have_count(1)
    expect(ui_page.locator(".a2-chip")).to_have_count(1)


def test_the_settled_receipt_still_renders_after_its_extraction(ui, ui_page):
    """The fold's room came from extracting the settled RECEIPT into its own component, and an
    extraction answers for every test of the thing it split — so this pins the receipt itself:
    an answered decision keeps what was said, says what became of it, and still offers revise."""
    ui.seed_question("uir", "q-a2p-5", "Was it answered?")
    answered = ui.routine_dir("uir") / "inbox" / "answer-q-a2p-5.json"
    answered.parent.mkdir(parents=True, exist_ok=True)
    answered.write_text('{"qid": "q-a2p-5", "text": "it was"}', encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item.answered")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("it was")
    expect(card.get_by_role("button", name="revise")).to_have_count(1)
