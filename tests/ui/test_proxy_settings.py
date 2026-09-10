"""Proxy settings remain editable without presenting them as direct metered billing."""

from playwright.sync_api import expect

from rsched.config import EndpointConfig
from rsched.endpoints import cliproxy_quota


def test_proxy_quota_settings_roundtrip(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.evaluate("""async () => {
      await fetch('/api/settings/endpoints', {method: 'POST',
        headers: {'Content-Type': 'application/json',
          Authorization: 'Bearer ' + localStorage.getItem('rsched_token')},
        body: JSON.stringify({name: 'proxy-trial', kind: 'anthropic',
          base_url: 'http://127.0.0.1:1', quota_source: 'cliproxy'})});
    }""")
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator(".panel", has=ui_page.get_by_text("proxy-trial", exact=True)).first
    expect(card).to_contain_text("Anthropic-compatible Messages API")
    card.get_by_text("edit fields", exact=True).click()
    expect(card.get_by_label("Subscription quota source")).to_have_value("cliproxy")
    card.get_by_label("Quota account index").fill("account-a")
    card.get_by_label("Management key secret name").fill("PROXY_MGMT")
    card.get_by_role("button", name="save changes", exact=True).click()
    card.get_by_text("edit fields", exact=True).click()
    expect(card.get_by_label("Quota account index")).to_have_value("account-a")
    expect(card.get_by_label("Management key secret name")).to_have_value("PROXY_MGMT")


def test_dashboard_shows_proxy_quota(ui, ui_page, monkeypatch):
    ui.server_cfg.endpoints["legacy"] = EndpointConfig(kind="anthropic")
    ui.server_cfg.endpoints["proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1", quota_source="cliproxy")
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: {
        "supported": True, "ok": True,
        "windows": {"five_hour": {"remaining": 75}, "seven_day": {"remaining": 40}},
    })
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.get_by_text("5h 75% left · 7d 40% left", exact=True)).to_be_visible()
