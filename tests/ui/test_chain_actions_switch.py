"""The ACTION CHAINING switch on the routine page (D203).

The operator asked for chaining "on for every routine but can be toggled off in the routine
settings" — the settings FORM, not only a YAML key. So this loads the page a person actually
sees: the control is there, it reads ON for a routine whose config never mentions it, and
turning it off writes `chain_actions: false` where the engine reads it.
"""
from __future__ import annotations

from playwright.sync_api import expect

from .conftest import until
from .helpers import accept, stored_config, unfold


def _group(page, title):
    return page.locator(".rgroup", has=page.locator(".rgroup-title", has_text=title))


def test_the_switch_is_on_the_page_and_reads_on_by_default(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)                        # it lives beside `improve`, behind the group's "more"
    box = ui_page.locator("input[data-chain-actions]")
    expect(box).to_be_visible()
    expect(box).to_be_checked()            # a config that never mentions it chains
    expect(ui_page.locator("#sec-chain-actions")).to_have_text("Action chaining")
    # …and the section says what it decides, for a reader who has never seen a chain
    expect(_group(ui_page, "Schedule & gate")).to_contain_text(
        "like commands typed in a terminal")
    assert "chain_actions" not in stored_config(ui)      # and nothing was written to say so


def test_turning_it_off_writes_the_key_the_engine_reads(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    ui_page.locator("input[data-chain-actions]").uncheck()
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/settings")) as sent:
        accept(ui_page)
    assert sent.value.post_data_json["changes"] == {"chain_actions": False}
    until(lambda: stored_config(ui).get("chain_actions") is False,
          what="the accepted chaining switch")
    ui_page.reload()
    unfold(ui_page)
    expect(ui_page.locator("input[data-chain-actions]")).not_to_be_checked()
    # and the group's own digest says it in words, not as a bare boolean
    expect(_group(ui_page, "Schedule & gate").locator(".rmore-digest")
           ).to_contain_text("one action per reply")
