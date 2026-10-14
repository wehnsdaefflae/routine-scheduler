"""The two instance-wide model choices carry a reasoning effort of their own — the levels come
from the server's payload, blank is the provider's default, and Automatic has none to set."""

from playwright.sync_api import expect

from rsched.config import load_server_config

HEADERS = {"Authorization": "Bearer ui-test-token"}
LEVELS = ["provider default", "low", "medium", "high", "xhigh", "max"]


def _catalog(ui, ui_page):
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=HEADERS,
        data={"name": "eff-ep", "kind": "openai"}).ok
    assert ui_page.request.post(f"{ui.url}/api/settings/models", headers=HEADERS,
        data={"name": "thinker", "endpoint": "eff-ep", "model": "thinker-id"}).ok


def test_the_system_model_saves_its_effort_and_clears_it(ui, ui_page):
    _catalog(ui, ui_page)
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    effort = ui_page.get_by_label("System model effort", exact=True)
    expect(effort.locator("option")).to_have_text(LEVELS)       # the server's vocabulary
    expect(effort).to_have_value("")
    effort.select_option("xhigh")
    row = ui_page.locator("div.row", has=ui_page.get_by_label("System model effort"))
    row.locator("button.primary").click()
    expect(ui_page.locator("#toast")).to_contain_text("effort xhigh")
    cfg = load_server_config(ui.server_cfg.source)[0]
    assert cfg.system_effort == "xhigh"
    ui_page.reload()
    expect(ui_page.get_by_label("System model effort", exact=True)).to_have_value("xhigh")
    ui_page.get_by_label("System model effort", exact=True).select_option("")
    ui_page.locator("div.row", has=ui_page.get_by_label("System model effort")) \
        .locator("button.primary").click()
    expect(ui_page.locator("#toast")).to_contain_text("system model →")
    assert load_server_config(ui.server_cfg.source)[0].system_effort == ""


def test_the_compaction_effort_belongs_to_a_dedicated_model_only(ui, ui_page):
    _catalog(ui, ui_page)
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    model = ui_page.get_by_label("Context compaction model", exact=True)
    effort = ui_page.get_by_label("Context compaction effort", exact=True)
    expect(effort.locator("option")).to_have_text(LEVELS)
    expect(effort).to_be_disabled()                    # Automatic: nothing for it to belong to
    model.select_option("thinker")
    expect(effort).to_be_enabled()
    effort.select_option("low")
    ui_page.get_by_role("button", name="save compaction model", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("compaction model → thinker")
    cfg = load_server_config(ui.server_cfg.source)[0]
    assert (cfg.compaction_model, cfg.compaction_effort) == ("thinker", "low")
    ui_page.reload()
    expect(ui_page.get_by_label("Context compaction effort", exact=True)).to_have_value("low")
    ui_page.get_by_label("Context compaction model", exact=True).select_option("")
    ui_page.get_by_role("button", name="save compaction model", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("Automatic")
    cfg = load_server_config(ui.server_cfg.source)[0]
    assert (cfg.compaction_model, cfg.compaction_effort) == ("", "")
