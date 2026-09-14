"""The three proxy sign-in routes beside the quota read: gated on the endpoint's proxy
management binding, thin over `endpoints.cliproxy_login`, and refused for the read-only
routine token like every other config-mutating POST."""

from __future__ import annotations

from rsched.endpoints import cliproxy_login
from rsched.web.settings import endpoint_probe


def _add_proxy(c) -> None:
    r = c.post("/api/settings/endpoints", json={
        "name": "proxy", "kind": "anthropic", "base_url": "http://127.0.0.1:1",
        "quota_source": "cliproxy"})
    assert r.status_code == 200, r.text


def test_accounts_route_is_unsupported_without_the_binding_and_thin_with_it(api_client,
                                                                             monkeypatch):
    c, _tmp = api_client
    assert c.get("/api/settings/endpoints/dummy/proxy-accounts").json() == {"supported": False}
    assert c.get("/api/settings/endpoints/nope/proxy-accounts").status_code == 404
    _add_proxy(c)
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "accounts", lambda ep: {
        "supported": True, "ok": True, "accounts": [{"provider": "claude", "label": "me",
                                                     "status": "error", "unavailable": True}]})
    out = c.get("/api/settings/endpoints/proxy/proxy-accounts").json()
    assert out["ok"] and out["accounts"][0]["label"] == "me"


def test_start_and_complete_pass_the_operators_words_through(api_client, monkeypatch):
    c, _tmp = api_client
    _add_proxy(c)
    calls: list = []
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "start",
                        lambda ep, provider: calls.append(("start", ep.base_url, provider))
                        or {"ok": True, "provider": provider, "url": "https://x", "state": "s1",
                            "callback_port": 54545})
    monkeypatch.setattr(endpoint_probe.cliproxy_login, "complete",
                        lambda ep, provider, state, pasted:
                        calls.append(("complete", provider, state, pasted)) or {"ok": True})
    r = c.post("/api/settings/endpoints/proxy/proxy-login", json={"provider": "anthropic"})
    assert r.status_code == 200 and r.json()["state"] == "s1"
    r = c.post("/api/settings/endpoints/proxy/proxy-login/complete",
               json={"provider": "anthropic", "state": "s1",
                     "response": "http://localhost:54545/callback?code=c&state=s1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls == [("start", "http://127.0.0.1:1", "anthropic"),
                     ("complete", "anthropic", "s1",
                      "http://localhost:54545/callback?code=c&state=s1")]


def test_mutating_routes_need_the_binding_and_the_operator_token(api_client, monkeypatch):
    c, _tmp = api_client
    # no binding: a plain endpoint cannot start a sign-in (400 names the missing setting)
    r = c.post("/api/settings/endpoints/dummy/proxy-login", json={"provider": "anthropic"})
    assert r.status_code == 400 and "quota source" in r.json()["detail"]
    assert c.post("/api/settings/endpoints/nope/proxy-login",
                  json={"provider": "anthropic"}).status_code == 404
    # the routine token reads the account list but may not sign anything in
    _add_proxy(c)
    monkeypatch.setattr(cliproxy_login, "accounts", lambda ep: {"supported": True, "ok": True,
                                                                 "accounts": []})
    server = c.app.state.server
    server.routine_token = "routine-only"
    headers = {"Authorization": "Bearer routine-only"}
    assert c.get("/api/settings/endpoints/proxy/proxy-accounts", headers=headers).json()["ok"]
    r = c.post("/api/settings/endpoints/proxy/proxy-login", json={"provider": "anthropic"},
               headers=headers)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    r = c.post("/api/settings/endpoints/proxy/proxy-login/complete",
               json={"provider": "anthropic", "state": "s", "response": "x"}, headers=headers)
    assert r.status_code == 403
