"""Proxy settings remain editable without presenting them as direct metered billing."""

import re

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
    expect(card.get_by_label("Proxy management (CLIProxyAPI)")).to_have_value("cliproxy")
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
    # The chip carries ONE window - the TIGHTEST, the only one that can summon anybody -
    # since 0.351.0. Printing every window made a ~120-character `white-space: nowrap` box
    # that broke the horizontal viewport on a phone (operator, 2026-09-18); the full
    # breakdown moved to the tooltip and to the endpoint's own Settings card, which is
    # where he asked for it.
    expect(ui_page.get_by_text("quota · 7d 40% left", exact=True)).to_be_visible()
    expect(ui_page.locator(".page-head .chip").first).to_have_attribute(
        "title", re.compile(r"5h 75% left"))


def test_proxy_reauth_from_each_endpoints_own_card(ui, ui_page, monkeypatch):
    """A dead proxy session is signed back in from the card, without a terminal — and from
    the RIGHT card: one proxy serves two endpoints here (claude-proxy carries the binding and
    Claude models, codex-proxy shares the origin with no binding and Codex models), and each
    card lists only its own provider's account and offers only its own sign-in. On
    2026-09-14 the Claude card offered both and the Codex card nothing. The address the
    operator lands on (localhost on THEIR device, which never loads) is pasted back to
    finish; the row and the quota line reload."""
    from rsched.config import ModelConfig
    from rsched.endpoints import cliproxy_login

    ui.server_cfg.endpoints["claude-proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1", quota_source="cliproxy")
    ui.server_cfg.endpoints["codex-proxy"] = EndpointConfig(
        kind="anthropic", base_url="http://127.0.0.1:1")
    ui.server_cfg.models["Opus"] = ModelConfig(name="Opus", endpoint="claude-proxy",
                                                model="claude-opus-5")
    ui.server_cfg.models["Astra"] = ModelConfig(name="Astra", endpoint="codex-proxy",
                                                 model="gpt-6-astra")
    quota = {"supported": True, "ok": False, "error": "token expired"}
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: quota)
    accounts = [{"name": "claude-me.json", "provider": "claude", "label": "me@example.org",
                 "status": "error", "status_message": "token expired", "unavailable": True,
                 "disabled": False, "next_retry_after": ""},
                {"name": "codex-me.json", "provider": "codex", "label": "me+openai@example.org",
                 "status": "error", "status_message": "usage limit reached", "unavailable": True,
                 "disabled": False, "next_retry_after": "2026-09-20T06:00:16"}]
    labels = {"claude": ("anthropic", "Claude"), "codex": ("codex", "Codex")}

    def fake_accounts(cfg, providers):          # the real one filters the same way
        rows = [a for a in accounts if a["provider"] in providers]
        return {"supported": True, "ok": True, "accounts": rows,
                "providers": [{"id": labels[p][0], "label": labels[p][1]}
                              for p in sorted(providers)]}
    monkeypatch.setattr(cliproxy_login, "accounts", fake_accounts)
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
    claude = ui_page.locator(".panel", has=ui_page.get_by_text("claude-proxy", exact=True)).first
    codex = ui_page.locator(".panel", has=ui_page.get_by_text("codex-proxy", exact=True)).first
    # each card: its own account row and its own control, nothing of the other's
    expect(claude).to_contain_text("✗ Claude me@example.org: token expired")
    expect(claude).not_to_contain_text("Codex")
    expect(codex).to_contain_text("✗ Codex me+openai@example.org: usage limit reached "
                                  "(retry after 2026-09-20 06:00)")
    expect(codex.get_by_role("button", name="re-authenticate Codex", exact=True)).to_be_visible()
    expect(codex.get_by_role("button", name="re-authenticate Claude", exact=True)).to_have_count(0)
    expect(codex).not_to_contain_text("subscription quota")      # Codex card carries no quota
    # the sign-in, from the Claude card
    claude.get_by_role("button", name="re-authenticate Claude", exact=True).click()
    link = claude.get_by_role("link", name="1. open the sign-in page ↗")
    expect(link).to_have_attribute("href", "https://consent.invalid/authorize?x=1")
    expect(claude).to_contain_text("localhost:54545")
    claude.get_by_label("Claude sign-in response").fill(
        "http://localhost:54545/callback?code=abc&state=st-1")
    claude.get_by_role("button", name="finish sign-in", exact=True).click()
    expect(claude).to_contain_text("✓ Claude me@example.org: ok")
    expect(claude).to_contain_text("5h 90% left")          # the quota row reloaded too
    assert completed == [("anthropic", "st-1", "http://localhost:54545/callback?code=abc&state=st-1")]
