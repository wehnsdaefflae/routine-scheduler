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


def test_proxy_reauth_from_the_endpoint_card(ui, ui_page, monkeypatch):
    """A dead proxy session is signed back in from the card, without a terminal: the card
    names the unavailable account in the proxy's words, the re-authenticate control hands
    out the consent link, and the address the operator lands on (localhost on THEIR device,
    which never loads) is pasted back to finish. The 2026-09-14 outage was 13 runs dead at
    turn 0 on an expired refresh token, with the only way back a tunnel and an exec."""
    from rsched.endpoints import cliproxy_login

    ui.server_cfg.endpoints["proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1", quota_source="cliproxy")
    quota = {"supported": True, "ok": False, "error": "token expired"}
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: quota)
    accounts = [{"name": "claude-me.json", "provider": "claude", "label": "me@example.org",
                 "status": "error", "status_message": "token expired", "unavailable": True,
                 "disabled": False, "next_retry_after": ""}]
    monkeypatch.setattr(cliproxy_login, "accounts",
                        lambda cfg: {"supported": True, "ok": True, "accounts": accounts})
    monkeypatch.setattr(cliproxy_login, "start", lambda cfg, provider: {
        "ok": True, "provider": provider, "url": "https://consent.invalid/authorize?x=1",
        "state": "st-1", "callback_port": 54545})
    completed: list = []

    def complete(cfg, provider, state, pasted):
        completed.append((provider, state, pasted))
        accounts[0].update(status="ok", status_message="", unavailable=False)
        quota.update(ok=True, windows={"five_hour": {"remaining": 90}})
        return {"ok": True, "provider": provider}
    monkeypatch.setattr(cliproxy_login, "complete", complete)

    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator(".panel", has=ui_page.get_by_text("proxy", exact=True)).first
    expect(card).to_contain_text("✗ Claude me@example.org: token expired")
    card.get_by_role("button", name="re-authenticate Claude", exact=True).click()
    link = card.get_by_role("link", name="1. open the sign-in page ↗")
    expect(link).to_have_attribute("href", "https://consent.invalid/authorize?x=1")
    expect(card).to_contain_text("localhost:54545")
    card.get_by_label("Claude sign-in response").fill(
        "http://localhost:54545/callback?code=abc&state=st-1")
    card.get_by_role("button", name="finish sign-in", exact=True).click()
    expect(card).to_contain_text("✓ Claude me@example.org: ok")
    expect(card).to_contain_text("5h 90% left")          # the quota row reloaded too
    assert completed == [("anthropic", "st-1", "http://localhost:54545/callback?code=abc&state=st-1")]
