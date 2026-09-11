"""Settings must persist dedicated archival selection and restore Automatic."""

from playwright.sync_api import expect

from rsched.config import load_server_config


def test_compaction_selector_persists_and_clears(ui, ui_page):
    headers = {"Authorization": "Bearer ui-test-token"}
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=headers,
        data={"name": "archive-ep", "kind": "openai"}).ok
    assert ui_page.request.post(f"{ui.url}/api/settings/models", headers=headers,
        data={"name": "archive", "endpoint": "archive-ep", "model": "archive-id"}).ok
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    selector = ui_page.get_by_label("Context compaction model", exact=True)
    expect(selector).to_have_value("")
    selector.select_option("archive")
    ui_page.get_by_role("button", name="save compaction model", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("compaction model → archive")
    ui_page.reload()
    expect(selector).to_have_value("archive")
    assert load_server_config(ui.server_cfg.source)[0].compaction_model == "archive"
    selector.select_option("")
    ui_page.get_by_role("button", name="save compaction model", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("compaction model → Automatic")
    assert load_server_config(ui.server_cfg.source)[0].compaction_model == ""
