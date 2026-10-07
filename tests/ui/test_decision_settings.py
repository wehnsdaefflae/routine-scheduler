"""Settings → Decision endpoints, in a browser: a provider preset fills the endpoint form, a
model added on it pre-fills its id, the defaults persist, and the section sits in the
Intelligence group beside the LLM endpoints."""

from playwright.sync_api import expect

from rsched.config import load_server_config


def _cfg(ui):
    return load_server_config(ui.server_cfg.source)[0]


def test_a_decision_catalog_is_built_from_the_settings_page(ui, ui_page):
    ui_page.set_viewport_size({"width": 1600, "height": 1000})
    ui_page.goto(f"{ui.url}/#/settings?section=decisions")
    expect(ui_page.locator("#sec-decisions")).to_have_text("Decision endpoints")
    expect(ui_page.locator(".settings-nav .tag", has_text="Decisions")).to_be_visible()
    expect(ui_page.get_by_text("no decision endpoints yet")).to_be_visible()

    add = ui_page.locator("details.panel", has_text="+ add decision endpoint")
    add.locator("summary").click()
    add.get_by_label("provider").select_option("openrouter")
    expect(add.get_by_label("protocol")).to_have_value("systemone")
    expect(add.get_by_label("base_url")).to_have_value("https://openrouter.ai/api/v1")
    add.get_by_role("button", name="add decision endpoint", exact=True).click()
    card = ui_page.locator('[data-decision-endpoint="openrouter"]')
    expect(card).to_contain_text("System One protocol (Jev)")
    ep = _cfg(ui).decision_endpoints["openrouter"]
    assert (ep.protocol, ep.base_url, ep.key_var) == (
        "systemone", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    expect(card).to_contain_text("✗ missing")     # hermetic secrets: no key anywhere yet

    addm = ui_page.locator("details.panel", has_text="+ add decision model")
    addm.locator("summary").click()
    expect(addm.get_by_label("model id")).to_have_value("typesafe/jev-1.13")
    addm.get_by_label("name").fill("jev")
    addm.get_by_role("button", name="add decision model", exact=True).click()
    row = ui_page.locator('[data-decision-model="jev"]')
    expect(row).to_contain_text("openrouter / typesafe/jev-1.13")
    expect(row.locator(".chip", has_text="text")).to_be_visible()

    ui_page.get_by_label("default decision model").select_option("jev")
    ui_page.get_by_role("button", name="save defaults").click()
    expect(row.locator(".chip", has_text="default")).to_be_visible()
    assert _cfg(ui).decision_model == "jev"
    # a text-only model is never offered as the image default
    expect(ui_page.get_by_label("default for images").locator("option")).to_have_count(1)


def test_a_failed_probe_reads_as_a_failure(ui, ui_page):
    headers = {"Authorization": "Bearer ui-test-token"}
    assert ui_page.request.post(f"{ui.url}/api/settings/decision-endpoints", headers=headers,
        data={"name": "jev-direct", "protocol": "systemone"}).ok
    assert ui_page.request.post(f"{ui.url}/api/settings/decision-models", headers=headers,
        data={"name": "jev", "endpoint": "jev-direct", "model": "jev-latest"}).ok
    ui_page.goto(f"{ui.url}/#/settings?section=decisions")
    row = ui_page.locator('[data-decision-model="jev"]')
    row.get_by_role("button", name="test").click()
    expect(row.locator(".test-result.bad")).to_contain_text("auth problem")
