"""The Decisions page's `approve & apply`: the patch lands on the routine the engine resolved,
not on whoever asked.

D123/F458: a config_patch may be FOR another routine (config-optimizer's whole job). The engine
resolves and validates that slug at ask time and the record carries it as `config_target`; a
record without one is a proposal for the asking routine itself.
"""

from __future__ import annotations

from playwright.sync_api import expect

from rsched.paths import atomic_write_json, read_yaml

from .conftest import until


def test_a_patch_for_another_routine_applies_to_that_routine(ui, ui_page, make_routine):
    """The button PATCHes /api/routines/{target} and says whose config it changed — the old
    hardwiring to the asker silently rewrote the asker's own config and reported success."""
    make_routine(slug="target")
    ui.seed_question("uir", "q-20260914-070000-1", "Raise target's turn budget to 90?",
                     extra={"config_patch": {"budgets": {"max_turns": 90}},
                            "config_target": "target"})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    # the copy names the routine it would change — an apply button that does not say where it
    # lands is the dead-button failure this bridge exists to end
    expect(card).to_contain_text("proposed config change for target")
    card.get_by_role("button", name="approve & apply").click()
    expect(card).to_contain_text("applied")
    until(lambda: read_yaml(ui.routines / "target" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 90, what="the target's patch")
    asker = read_yaml(ui.routines / "uir" / "routine.yaml")
    assert asker.get("budgets", {}).get("max_turns") != 90


def test_a_patch_for_the_asker_applies_to_the_asker(ui, ui_page):
    """A record with no `config_target` reaches /api/routines/{asker}."""
    ui.seed_question("uir", "q-20260914-070000-2", "Raise the turn budget to 120?",
                     extra={"config_patch": {"budgets": {"max_turns": 120}}})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item").first
    expect(card).to_be_visible()
    card.get_by_role("button", name="approve & apply").click()
    expect(card).to_contain_text("applied")
    until(lambda: read_yaml(ui.routines / "uir" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 120, what="the routine patch")
    raw = read_yaml(ui.routines / "uir" / "routine.yaml")
    assert raw["budgets"]["max_turns"] == 120


def test_a_conversations_patch_for_a_routine_applies_to_that_routine(ui, ui_page):
    """A conversation names a routine as its proposal's target: the engine resolves it against
    the ROUTINES home and records it as `config_target`; the button PATCHes that routine.
    Forcing the asker as the target posted it to /api/routines/<the conversation> — a 404 —
    while the copy called the routine a conversation."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Tidy the uir routine's budget.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    conv = ui_page.url.rsplit("/", 1)[-1]
    pending = ui.conversations / conv / "questions" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    atomic_write_json(pending / "q-20260930-070000-1.json", {
        "qid": "q-20260930-070000-1", "question": "Raise uir's turn budget to 77?",
        "mode": "deferred", "type": "text", "options": [], "default": "",
        "asked": "20260930-070000", "config_patch": {"budgets": {"max_turns": 77}},
        "config_target": "uir"})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="Raise uir's turn budget").first
    expect(card).to_be_visible()
    expect(card).to_contain_text("proposed config change for uir")
    expect(card).to_contain_text("routine's behalf")
    card.get_by_role("button", name="approve & apply").click()
    expect(card).to_contain_text("applied")
    until(lambda: read_yaml(ui.routines / "uir" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 77, what="the routine patch")


def test_answering_keeps_the_card_and_its_apply_button(ui, ui_page):
    """R2189, reported twice. A patch-carrying card has TWO controls, and ANSWERING is not one
    of them: the answer lands the text and never applies the patch. So answering must not take
    the card away — the operator ruled it (2026-10-05, option 3: "only the button; answering
    warns and keeps the patch available"), because the second report carried an option reading
    literally "Ja — Patch übernehmen" and the proposed change evaporated with the card.

    Loads what the person sees, not the attribute that points at it: after answering, the
    proposal, the apply button and a warning that nothing was applied must all still be there,
    and the button must still WORK.
    """
    ui.seed_question("uir", "q-20261008-070000-1", "Raise the turn budget to 95?",
                     extra={"config_patch": {"budgets": {"max_turns": 95}}})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="Raise the turn budget to 95").first
    expect(card).to_be_visible()
    apply_btn = card.get_by_role("button", name="approve & apply")
    expect(apply_btn).to_be_visible()

    # The house locators (`textarea.answer-input`, and `name="answer"` with exact=True), not a
    # generic selector: a patch-carrying card ALSO carries an "approve & apply" button, so a
    # NON-exact accessible-name match for "answer" is ambiguous on exactly the card type this
    # test seeds — which is why the first version of this test failed all four attempts while
    # its patch-less control sibling passed in the same run.
    card.locator("textarea.answer-input").fill("yes, please raise it")
    card.get_by_role("button", name="answer", exact=True).click()

    expect(card).to_contain_text("answered")
    expect(card).to_contain_text("NOT applied")
    expect(card).to_contain_text("budgets")          # the proposal is still shown

    # Re-locate the button instead of reusing `apply_btn`: an answered card is RE-RENDERED
    # through the settled-receipt branch of `item`, a different element tree that mounts its
    # own copy of the proposal, so the handle taken before answering points at a node that is
    # no longer in the document. This is the whole substance of the fix — the proposal has to
    # exist in BOTH card shapes, and a test holding the old handle would have proved only that
    # Playwright caches an element.
    kept_btn = card.get_by_role("button", name="approve & apply")
    expect(kept_btn).to_be_visible()                 # …the one control that applies it

    # the button is not merely present: it still applies
    kept_btn.click()
    expect(card).to_contain_text("applied")
    until(lambda: read_yaml(ui.routines / "uir" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 95, what="the patch applied after answering")


def test_answering_an_ordinary_question_still_clears_it(ui, ui_page):
    """The control case, and the reason the keep-the-card change is scoped to patch-carrying
    cards: a question with NO proposal must still disappear on answering, exactly as before.
    A card that lingered after being answered would read as "it didn't work"."""
    ui.seed_question("uir", "q-20261008-070000-2", "Which colour?")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="Which colour").first
    expect(card).to_be_visible()
    card.locator("textarea.answer-input").fill("green")
    card.get_by_role("button", name="answer", exact=True).click()
    expect(card).to_contain_text("answered")
    expect(card.get_by_role("button", name="approve & apply")).to_have_count(0)
