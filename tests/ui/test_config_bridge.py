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
