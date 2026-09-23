"""The three proxy sign-in routes beside the quota read: bound to the PROXY (an endpoint
without its own binding resolves a sibling's on the same origin), filtered to the provider
of each endpoint's models, thin over `endpoints.cliproxy_login`, and refused for the
read-only routine token like every other config-mutating POST."""

from __future__ import annotations

from rsched.endpoints import cliproxy_login
from rsched.web.settings import endpoint_probe


def _add_proxy_pair(c) -> None:
    """One proxy, two endpoints: claude-proxy carries the binding and Claude models,
    codex-proxy shares the origin, carries no binding and Codex models — the 2026-09-14
    layout, where the Codex card offered nothing and the Claude card offered both."""
    for name, quota in (("claude-proxy", "cliproxy"), ("codex-proxy", "")):
        r = c.post("/api/settings/endpoints", json={
            "name": name, "kind": "anthropic", "base_url": "http://cliproxy:8317",
            "quota_source": quota})
        assert r.status_code == 200, r.text
    for name, endpoint, model in (("Opus", "claude-proxy", "claude-opus-5"),
                                  ("Astra", "codex-proxy", "gpt-6-astra")):
        r = c.post("/api/settings/models", json={"name": name, "endpoint": endpoint,
                                                 "model": model})
        assert r.status_code == 200, r.text


def test_the_view_says_which_cards_carry_the_binding_and_which_the_quota(api_client):
    c, _tmp = api_client
    _add_proxy_pair(c)
    view = {e["name"]: e for e in c.get("/api/settings/endpoints").json()["endpoints"]}
    assert view["dummy"]["proxy_management"] is False
    assert view["dummy"]["has_subscription_quota"] is False
    assert view["claude-proxy"]["proxy_management"] is True
    assert view["claude-proxy"]["has_subscription_quota"] is True
    assert view["codex-proxy"]["proxy_management"] is True      # the sibling's binding
    assert view["codex-proxy"]["has_subscription_quota"] is False  # Codex models: no quota


def test_accounts_route_resolves_the_proxys_binding_and_names_each_cards_providers(
        api_client, monkeypatch):
    c, _tmp = api_client
    assert c.get("/api/settings/endpoints/dummy/proxy-accounts").json() == {"supported": False}
    assert c.get("/api/settings/endpoints/nope/proxy-accounts").status_code == 404
    _add_proxy_pair(c)
    asked: list = []
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "accounts",
                        lambda bound, providers: asked.append((bound.quota_source,
                                                               sorted(providers)))
                        or {"supported": True, "ok": True, "accounts": [], "providers": []})
    assert c.get("/api/settings/endpoints/claude-proxy/proxy-accounts").json()["ok"]
    assert c.get("/api/settings/endpoints/codex-proxy/proxy-accounts").json()["ok"]
    # both calls went through the binding endpoint (quota_source set), each with its own
    # card's providers
    assert asked == [("cliproxy", ["claude"]), ("cliproxy", ["codex"])]


def test_quota_applies_to_the_claude_card_only(api_client, monkeypatch):
    c, _tmp = api_client
    _add_proxy_pair(c)
    monkeypatch.setattr(endpoint_probe.cliproxy_quota, "read_quota",
                        lambda bound: {"supported": True, "ok": True, "windows": {}})
    assert c.get("/api/settings/endpoints/claude-proxy/quota").json()["ok"]
    assert c.get("/api/settings/endpoints/codex-proxy/quota").json() == {"supported": False}
    assert c.get("/api/settings/endpoints/dummy/quota").json() == {"supported": False}


def test_start_and_complete_pass_the_operators_words_through_the_binding(api_client,
                                                                          monkeypatch):
    c, _tmp = api_client
    _add_proxy_pair(c)
    calls: list = []
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "start",
                        lambda bound, provider: calls.append(("start", bound.quota_source,
                                                              provider))
                        or {"ok": True, "provider": provider, "url": "https://x", "state": "s1",
                            "callback_port": 1455})
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "complete",
                        lambda bound, provider, state, pasted:
                        calls.append(("complete", provider, state, pasted)) or {"ok": True})
    # the Codex card has no binding of its own — the sibling's serves it
    r = c.post("/api/settings/endpoints/codex-proxy/proxy-login", json={"provider": "codex"})
    assert r.status_code == 200 and r.json()["state"] == "s1"
    r = c.post("/api/settings/endpoints/codex-proxy/proxy-login/complete",
               json={"provider": "codex", "state": "s1",
                     "response": "http://localhost:1455/callback?code=c&state=s1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls == [("start", "cliproxy", "codex"),
                     ("complete", "codex", "s1", "http://localhost:1455/callback?code=c&state=s1")]


def test_mutating_routes_need_a_binding_and_the_operator_token(api_client, monkeypatch):
    c, _tmp = api_client
    # no binding anywhere: a plain endpoint cannot start a sign-in (400 names the setting)
    r = c.post("/api/settings/endpoints/dummy/proxy-login", json={"provider": "anthropic"})
    assert r.status_code == 400 and "Proxy management" in r.json()["detail"]
    assert c.post("/api/settings/endpoints/nope/proxy-login",
                  json={"provider": "anthropic"}).status_code == 404
    # the routine token reaches none of this: /api/settings is a denied READ subtree for it
    # (web/app.ROUTINE_TOKEN_DENIED_READS), because the account list names the subscription
    # credentials a run has no business enumerating — and it may not sign anything in either.
    _add_proxy_pair(c)
    monkeypatch.setattr(cliproxy_login, "accounts",
                        lambda bound, providers: {"supported": True, "ok": True,
                                                  "accounts": [], "providers": []})
    server = c.app.state.server
    server.routine_token = "routine-only"
    headers = {"Authorization": "Bearer routine-only"}
    assert c.get("/api/settings/endpoints/claude-proxy/proxy-accounts",
                 headers=headers).status_code == 403
    r = c.post("/api/settings/endpoints/claude-proxy/proxy-login",
               json={"provider": "anthropic"}, headers=headers)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    r = c.post("/api/settings/endpoints/claude-proxy/proxy-login/complete",
               json={"provider": "anthropic", "state": "s", "response": "x"}, headers=headers)
    assert r.status_code == 403
