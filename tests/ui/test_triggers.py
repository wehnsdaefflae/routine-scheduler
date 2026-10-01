"""Routine-page Triggers: rows render (URL, fire ledger); the list is a SETTING — added,
edited and removed in the draft, saved by the page's one accept. DOM and disk both asserted.

What the server mints is identity — a trigger's id and a webhook's token — so a trigger new in
the draft has no URL until it is accepted. Its row says so.
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
    expect(row.locator('input[type="text"]')).to_have_value(f"{ui.url}/api/hooks/uir/{SEED_TOKEN}")
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


def test_a_report_triggers_bounds_edit_in_place_and_one_is_the_limit(ui, ui_page):
    """The cooldown on a listed trigger IS an editor. Each add-button makes a trigger with its
    type's own defaults; a second report trigger stays refused."""
    panel = open_section(ui, ui_page, "sec-triggers")
    panel.get_by_role("button", name="+ add report trigger").click()
    row = panel.locator(".trigger-row").first
    cooldown = row.locator("input.cooldown-in")
    expect(cooldown).to_have_value("900")            # the type's own default
    expect(row.locator("input.cap-in")).to_have_value("24")
    cooldown.fill("120")
    cooldown.press("Tab")
    expect(panel.locator(".trigger-row").first.locator("input.cooldown-in")).to_have_value("120")
    expect(panel.get_by_role("button", name="+ add report trigger")).to_be_disabled()

    accept(ui_page)
    until(lambda: (stored_config(ui).get("triggers") or [{}])[0].get("cooldown_s") == 120,
          what="the accepted report trigger")
    stored = stored_config(ui)["triggers"][0]
    assert stored["type"] == "report" and stored["max_fires_per_day"] == 24
    ui_page.reload()
    panel = open_section(ui, ui_page, "sec-triggers")
    expect(panel.locator(".trigger-row").first.locator("input.cooldown-in")).to_have_value("120")
    expect(panel.get_by_role("button", name="+ add report trigger")).to_be_disabled()
