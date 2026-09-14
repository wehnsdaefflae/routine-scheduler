"""Signing a proxy account back in from the console: the consent link comes from the proxy,
the code goes back by hand (the callback lands on localhost on the OPERATOR'S device), the
exchange is confirmed by polling, and nothing the management key sees reaches the console."""

import json

import httpx
import pytest

from rsched.config import EndpointConfig
from rsched.endpoints import cliproxy_login, cliproxy_mgmt
from rsched.endpoints.base import EndpointError


@pytest.fixture
def proxy(monkeypatch):
    # the management client (key + URL) is cliproxy_mgmt's, shared with the quota read
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "management-secret")
    return EndpointConfig(kind="anthropic", base_url="http://proxy:8317/v1",
                          quota_source="cliproxy")


def mock_proxy(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(cliproxy_mgmt.httpx, "Client", lambda **kw: factory(
        transport=httpx.MockTransport(handler), **kw))


# ---- the account list ---------------------------------------------------------------------


def test_accounts_is_an_allowlist_projection_that_never_carries_a_token(proxy, monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer management-secret"
        assert request.url.path == "/v0/management/auth-files"     # /v1 stripped, root kept
        return httpx.Response(200, json={"files": [
            {"name": "claude-me.json", "provider": "claude", "email": "me@example.org",
             "status": "error", "status_message": "token expired\nsecond line",
             "unavailable": True, "disabled": False,
             "access_token": "sk-ant-secret", "refresh_token": "rt-secret"},
            {"name": "codex-me.json", "type": "codex", "label": "me+openai@example.org",
             "status": "error", "unavailable": True,
             "status_message": f'{{"error":{{"type":"usage_limit_reached"}}}} sk-ant-{"x" * 60} eyJ{"a" * 60}',
             "next_retry_after": "2026-09-20T06:00:16.003746254+08:00"},
            "not-a-dict"]})
    mock_proxy(monkeypatch, handler)
    out = cliproxy_login.accounts(proxy)          # no providers known: everything, both controls
    assert out["ok"] and out["supported"]
    assert out["providers"] == [{"id": "anthropic", "label": "Claude"},
                                {"id": "codex", "label": "Codex"}]
    claude, codex = out["accounts"]
    assert claude == {"name": "claude-me.json", "provider": "claude", "label": "me@example.org",
                      "status": "error", "status_message": "token expired",
                      "unavailable": True, "disabled": False, "next_retry_after": ""}
    assert codex["provider"] == "codex" and codex["label"] == "me+openai@example.org"
    # the JSON fragment (41 chars, not token alphabet) stays; the two token-shaped words go
    assert codex["status_message"] == '{"error":{"type":"usage_limit_reached"}}'
    assert codex["next_retry_after"] == "2026-09-20T06:00:16.00374"
    dumped = json.dumps(out)
    assert "secret" not in dumped and "sk-ant" not in dumped
    # an endpoint whose models are Codex's sees the Codex account and the Codex control only —
    # and a provider with no sign-in route contributes nothing
    only = cliproxy_login.accounts(proxy, {"codex", "gemini"})
    assert [a["provider"] for a in only["accounts"]] == ["codex"]
    assert only["providers"] == [{"id": "codex", "label": "Codex"}]


@pytest.mark.parametrize("reply", [httpx.Response(403, text="management-secret"),
                                   httpx.Response(200, text="not json management-secret"),
                                   httpx.Response(200, json={"files": "management-secret"})])
def test_accounts_errors_are_soft_and_redacted(proxy, monkeypatch, reply):
    mock_proxy(monkeypatch, lambda request: reply)
    out = cliproxy_login.accounts(proxy)
    assert out["supported"] and not out["ok"] and "management-secret" not in json.dumps(out)


def test_a_bad_base_url_is_a_soft_error_before_any_request(monkeypatch):
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "k")
    ep = EndpointConfig(kind="anthropic", base_url="ftp://proxy", quota_source="cliproxy")
    assert "base URL" in cliproxy_login.accounts(ep)["error"]


# ---- starting a sign-in -------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "port"), [("anthropic", 54545), ("codex", 1455)])
def test_start_asks_the_proxy_for_a_webui_consent_link(proxy, monkeypatch, provider, port):
    def handler(request):
        assert request.url.path == f"/v0/management/{provider}-auth-url"
        assert request.url.params["is_webui"] == "true"
        return httpx.Response(200, json={"status": "ok", "state": "st-1",
                                         "url": "https://claude.ai/oauth/authorize?x=1"})
    mock_proxy(monkeypatch, handler)
    out = cliproxy_login.start(proxy, provider)
    assert out == {"ok": True, "provider": provider, "url": "https://claude.ai/oauth/authorize?x=1",
                   "state": "st-1", "callback_port": port}


def test_start_refuses_an_unknown_provider_without_a_request(proxy, monkeypatch):
    mock_proxy(monkeypatch, lambda request: pytest.fail("no request expected"))
    out = cliproxy_login.start(proxy, "gemini")
    assert not out["ok"] and "anthropic, codex" in out["error"]


@pytest.mark.parametrize("reply", [
    httpx.Response(401, text="management-secret"),
    httpx.Response(200, text="not json management-secret"),
    httpx.Response(200, json={"status": "ok", "url": ""}),
])
def test_start_errors_are_soft_and_never_echo_a_body(proxy, monkeypatch, reply):
    mock_proxy(monkeypatch, lambda request: reply)
    out = cliproxy_login.start(proxy, "anthropic")
    assert not out["ok"] and "management-secret" not in json.dumps(out)


def test_start_surfaces_the_proxys_own_refusal_minus_any_token_shaped_word(proxy, monkeypatch):
    leaked = "sk-ant-" + "x" * 60
    mock_proxy(monkeypatch, lambda request: httpx.Response(
        200, json={"status": "error", "error": f"login disabled {leaked} by config"}))
    out = cliproxy_login.start(proxy, "anthropic")
    assert out["error"] == "The proxy did not return a sign-in link: login disabled by config"


# ---- what the operator pastes -------------------------------------------------------------


@pytest.mark.parametrize("pasted", [
    "http://localhost:54545/callback?code=abc123&state=st-1",
    "  http://localhost:54545/callback?state=st-1&code=abc123  ",
    "abc123#st-1",
    "abc123",
])
def test_parse_response_accepts_the_address_the_code_hash_state_and_the_bare_code(pasted):
    assert cliproxy_login.parse_response(pasted, "st-1") == ("abc123", "st-1")


@pytest.mark.parametrize(("pasted", "why"), [
    ("", "Paste the address"),
    ("http://localhost:54545/callback?state=st-1", "carries no code"),
    ("http://localhost:54545/callback?error=access_denied&error_description=nope", "access_denied"),
    ("http://localhost:54545/callback?code=abc&state=OTHER", "different sign-in attempt"),
    ("abc#OTHER", "different sign-in attempt"),
])
def test_parse_response_names_what_is_wrong(pasted, why):
    with pytest.raises(EndpointError, match=why):
        cliproxy_login.parse_response(pasted, "st-1")


# ---- finishing a sign-in ------------------------------------------------------------------


def test_complete_hands_the_code_over_and_polls_until_the_exchange_settles(proxy, monkeypatch):
    seen: list = []
    polls = iter(["wait", "wait", "ok"])

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/oauth-callback"):
            assert json.loads(request.content) == {"provider": "anthropic", "state": "st-1",
                                                   "code": "abc123"}
            return httpx.Response(200, json={"status": "ok"})
        assert request.url.params["state"] == "st-1"
        return httpx.Response(200, json={"status": next(polls)})
    mock_proxy(monkeypatch, handler)
    slept: list = []
    out = cliproxy_login.complete(proxy, "anthropic", "st-1",
                                  "http://localhost:54545/callback?code=abc123&state=st-1",
                                  sleep=slept.append)
    assert out == {"ok": True, "provider": "anthropic"}
    assert seen[0] == ("POST", "/v0/management/oauth-callback")
    assert [p for m, p in seen[1:]] == ["/v0/management/get-auth-status"] * 3
    assert slept == [1, 1]


def test_complete_reports_the_proxys_refusal_in_its_own_words(proxy, monkeypatch):
    mock_proxy(monkeypatch, lambda request: httpx.Response(
        200, json={"status": "error", "error": "unknown or expired state"}))
    out = cliproxy_login.complete(proxy, "anthropic", "st-1", "abc")
    assert not out["ok"] and "refused the code" in out["error"]
    assert "unknown or expired state" in out["error"] and "start the sign-in again" in out["error"]


def test_complete_never_echoes_a_non_json_body(proxy, monkeypatch):
    mock_proxy(monkeypatch, lambda request: httpx.Response(502, text="gateway management-secret"))
    out = cliproxy_login.complete(proxy, "anthropic", "st-1", "abc")
    assert not out["ok"] and "management-secret" not in json.dumps(out)


def test_complete_surfaces_a_failed_exchange(proxy, monkeypatch):
    def failing(request):
        if request.url.path.endswith("/oauth-callback"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "error", "error": "invalid_grant"})
    mock_proxy(monkeypatch, failing)
    out = cliproxy_login.complete(proxy, "codex", "st-1", "abc", sleep=lambda s: None)
    assert not out["ok"] and "could not finish" in out["error"] and "invalid_grant" in out["error"]


def test_complete_surfaces_a_stalled_exchange(proxy, monkeypatch):
    # one mock per test: mock_proxy wraps whatever httpx.Client currently is, so a second
    # call in the same test would wrap the first mock and pass two transports
    def stalled(request):
        if request.url.path.endswith("/oauth-callback"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "wait"})
    mock_proxy(monkeypatch, stalled)
    out = cliproxy_login.complete(proxy, "codex", "st-1", "abc", poll_s=0, sleep=lambda s: None)
    assert not out["ok"] and "not confirmed" in out["error"]


def test_complete_refuses_a_bad_paste_before_any_request(proxy, monkeypatch):
    mock_proxy(monkeypatch, lambda request: pytest.fail("no request expected"))
    out = cliproxy_login.complete(proxy, "anthropic", "st-1", "abc#OTHER")
    assert not out["ok"] and "different sign-in attempt" in out["error"]
    out = cliproxy_login.complete(proxy, "bogus", "st-1", "abc")
    assert not out["ok"] and "unknown provider" in out["error"]
