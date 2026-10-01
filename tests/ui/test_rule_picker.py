"""The general-rule picker's one dear control: withdrawing an unbound rule's TEXT from a live
run's context (components/rulepicker.js).

Unbinding reaches a live run either way — the run is told the rule no longer binds. Erasing the
text as well rewrites the messages carrying it, which costs the provider's prompt cache from
that point on, so it is offered only beside a staged unbind, only where a save can reach a live
run, and never left armed from a choice the reader made for an earlier save.
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect

from .conftest import until

# The stub runner resumes a terminal conversation to this ts (tests/ui/conftest.py); a run seeded
# there in a working state is the reply in flight the conversation view follows.
LIVE_TS = "20260715-120001"


def _live_conversation(ui, ui_page) -> str:
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Help me restyle the landing page.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    ui.seed_run(slug, LIVE_TS, "running", home=ui.conversations)
    ui_page.reload()
    ui_page.locator(".conv-caps > summary").click()
    return slug


def test_the_erase_choice_stands_beside_a_staged_unbind_and_nothing_else(ui, ui_page):
    """It used to stay on screen after the unbind it belonged to was withdrawn, and after the
    save — still ticked, so the next unbind went out erasing text the reader never chose to."""
    _live_conversation(ui, ui_page)
    picker = ui_page.locator(".rulepicker")
    erase = picker.locator(".rule-erase")
    bound = picker.locator('.rule-bound[data-rule="ask-policy"] input[type="checkbox"]')
    expect(bound).to_be_visible()
    expect(erase).to_be_hidden()

    bound.uncheck()
    expect(erase).to_be_visible()
    erase.locator("input").check()
    bound.check()                                     # the unbind is withdrawn
    expect(erase).to_be_hidden()

    bound.uncheck()                                   # staged again: a fresh, unticked offer
    expect(erase.locator("input")).not_to_be_checked()
    erase.locator("input").check()
    bodies: list[dict] = []
    ui_page.on("request", lambda r: bodies.append(r.post_data_json)
               if r.method == "POST" and r.url.endswith("/rules") else None)
    picker.get_by_role("button", name="apply").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("rules updated")
    until(lambda: bodies, page=ui_page, what="the rules POST")
    assert bodies[0]["remove"] == ["ask-policy"] and bodies[0]["erase"] is True
    expect(erase).to_be_hidden()
    expect(erase.locator("input")).not_to_be_checked()


def test_a_draft_picker_offers_no_live_run_controls(ui, ui_page):
    """On the routine page the picker edits the settings DRAFT, which reaches a routine only
    through the page's accept — refused while a run is active, and carrying no erase at all. A
    live run there used to raise the erase offer and a status line promising the run in flight
    the change: one control that did nothing, one sentence that was not true."""
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["rules"] = ["ask-policy"]
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    ui_page.evaluate("() => { for (const d of document.querySelectorAll("
                     "'details.rgroup, details.rmore')) d.open = true; }")
    panel = ui_page.locator("#sec-general-rules + .panel")
    bound = panel.locator('.rule-bound[data-rule="ask-policy"] input[type="checkbox"]')
    expect(bound).to_be_visible()

    bound.uncheck()
    expect(panel.locator(".rule-bound.pending-drop")).to_have_count(1)   # staged, marked
    expect(panel.locator(".rule-erase")).to_be_hidden()
    expect(panel.locator(".rulepicker")).not_to_contain_text("run in flight")
