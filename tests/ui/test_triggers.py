"""Routine-page Triggers: rows render (URL, fire ledger); the list is a SETTING — added,
edited and removed in the draft, saved by the page's one accept. DOM and disk both asserted.
"""

from playwright.sync_api import expect

from .conftest import until
from .helpers import accept, configure, open_section, stored_config, visible_toast

SEED_TOKEN = "tok-ui-" + "b" * 24


def _seed_trigger(ui, slug="uir"):
    configure(ui, slug, triggers=[{"id": "t-uiseed01", "type": "webhook", "token": SEED_TOKEN,
                                   "cooldown_s": 60}])


def test_triggers_render_and_a_new_one_is_created_on_accept(ui, ui_page):
    _seed_trigger(ui)
    panel = open_section(ui, ui_page, "sec-triggers")
    row = panel.locator(".trigger-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("webhook")
    expect(row).to_contain_text("t-uiseed01")
    expect(row).to_contain_text("last fired · never")
    expect(row.locator("input.cooldown-in")).to_have_value("60")   # editable in place
    expect(row.locator('input[type="text"]')).to_have_value(f"{ui.url}/api/webhooks/uir/{SEED_TOKEN}")
    expect(row.get_by_role("button", name="copy")).to_be_visible()

    panel.get_by_role("button", name="+ add webhook trigger").click()
    fresh = panel.locator('.trigger-row[data-trigger="new"]')
    expect(fresh).to_contain_text("its hook URL is minted when you accept")
    assert len(stored_config(ui)["triggers"]) == 1              # a draft until accepted

    accept(ui_page)
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: len(stored_config(ui)["triggers"]) == 2, what="the accepted trigger")
    triggers = stored_config(ui)["triggers"]
    assert triggers[0]["token"] == SEED_TOKEN                   # the existing hook is untouched
    created = triggers[1]
    assert created["type"] == "webhook" and len(created["token"]) >= 24
    # …and once accepted, the new trigger has its own URL on the page
    expect(panel.locator(".trigger-row")).to_have_count(2)
    expect(panel.locator('.trigger-row[data-trigger="new"]')).to_have_count(0)


def test_removing_a_trigger_takes_effect_on_accept(ui, ui_page):
    _seed_trigger(ui)
    panel = open_section(ui, ui_page, "sec-triggers")
    panel.locator(".trigger-row").get_by_role("button", name="remove").click()
    expect(panel.locator(".trigger-row")).to_have_count(0)
    expect(panel.locator(".triggers-body")).to_contain_text("no triggers")
    assert len(stored_config(ui)["triggers"]) == 1              # still there until accepted
    accept(ui_page)
    until(lambda: stored_config(ui).get("triggers") == [], what="the removed trigger")


def test_a_new_triggers_cooldown_edits_in_place(ui, ui_page):
    """The cooldown on a listed trigger IS an editor, and the add-button makes a trigger with
    the default cooldown."""
    panel = open_section(ui, ui_page, "sec-triggers")
    panel.get_by_role("button", name="+ add webhook trigger").click()
    row = panel.locator(".trigger-row").first
    cooldown = row.locator("input.cooldown-in")
    expect(cooldown).to_have_value("60")             # the default
    cooldown.fill("120")
    cooldown.press("Tab")
    expect(panel.locator(".trigger-row").first.locator("input.cooldown-in")).to_have_value("120")

    accept(ui_page)
    until(lambda: (stored_config(ui).get("triggers") or [{}])[0].get("cooldown_s") == 120,
          what="the accepted webhook trigger")
    stored = stored_config(ui)["triggers"][0]
    assert stored["type"] == "webhook" and "max_fires_per_day" not in stored
    ui_page.reload()
    panel = open_section(ui, ui_page, "sec-triggers")
    expect(panel.locator(".trigger-row").first.locator("input.cooldown-in")).to_have_value("120")
