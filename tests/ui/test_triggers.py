"""Routine-page Triggers card: rows render (URL, ledger line), create writes routine.yaml
through the authed CRUD, delete removes the entry — DOM and disk both asserted."""

import yaml
from playwright.sync_api import expect

SEED_TOKEN = "tok-ui-" + "b" * 24


def _toast(page):
    return page.locator("#toast:not([hidden])")


def _seed_trigger(ui, slug="uir"):
    path = ui.routine_dir(slug) / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["triggers"] = [{"id": "t-uiseed01", "type": "webhook", "token": SEED_TOKEN,
                        "cooldown_s": 60}]
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_triggers_card_renders_and_creates(ui, ui_page):
    _seed_trigger(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    row = ui_page.locator(".trigger-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("webhook")
    expect(row).to_contain_text("t-uiseed01")
    expect(row).to_contain_text("last fired · never")
    expect(row.locator("input.cooldown-in")).to_have_value("60")   # editable in place
    expect(row.locator('input[type="text"]')).to_have_value(f"{ui.url}/api/hooks/uir/{SEED_TOKEN}")
    expect(row.get_by_role("button", name="copy")).to_be_visible()

    ui_page.get_by_role("button", name="+ add webhook trigger").click()
    expect(_toast(ui_page)).to_contain_text("webhook trigger created")
    expect(ui_page.locator(".trigger-row")).to_have_count(2)
    raw = yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert len(raw["triggers"]) == 2
    created = raw["triggers"][1]
    assert created["type"] == "webhook" and len(created["token"]) >= 24


def test_trigger_delete_flow(ui, ui_page):
    _seed_trigger(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.locator(".trigger-row").get_by_role("button", name="delete").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="delete").click()
    # the row vanishing + the yaml entry going are the durable assertions (a toast expires)
    expect(ui_page.locator(".trigger-row")).to_have_count(0)
    expect(ui_page.locator(".triggers-body")).to_contain_text("no triggers yet")
    raw = yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["triggers"] == []


def test_routine_page_trigger_cooldown_edits_in_place(ui, ui_page):
    """The cooldown on a listed trigger IS an editor (it used to be static text next to a
    create-form input, which read as an editor and had no save path), and each add-button
    carries its own cooldown box."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = ui_page.locator(".panel", has=ui_page.get_by_role("button", name="+ add report trigger"))
    panel.get_by_role("button", name="+ add report trigger").click()
    expect(_toast(ui_page)).to_contain_text("report trigger created")

    row = panel.locator(".trigger-row").first
    cooldown = row.locator("input.cooldown-in")
    expect(cooldown).to_have_value("900")            # the type's own default
    cooldown.fill("120")
    cooldown.press("Enter")                          # change fires on commit — no save button
    expect(_toast(ui_page)).to_contain_text("cooldown saved — 120s")
    ui_page.reload()
    expect(panel.locator(".trigger-row").first.locator("input.cooldown-in")).to_have_value("120")

    # a second report trigger stays refused, and its add-box is disabled with the reason
    expect(panel.get_by_role("button", name="+ add report trigger")).to_be_disabled()
