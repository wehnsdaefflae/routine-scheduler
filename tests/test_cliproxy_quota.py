"""Proxy quota must remain account-specific, fail soft, and never expose credentials."""

import json

import httpx
import pytest

from rsched.config import EndpointConfig
from rsched.endpoints import cliproxy_mgmt, cliproxy_quota


@pytest.fixture
def proxy(monkeypatch):
    # the management client (key + URL) is cliproxy_mgmt's, shared with the sign-in module
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "management-secret")
    return EndpointConfig(kind="anthropic", base_url="http://proxy:8317",
                          quota_source="cliproxy")


def mock_proxy(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(cliproxy_mgmt.httpx, "Client", lambda **kw: factory(
        transport=httpx.MockTransport(handler), **kw))


def test_quota_uses_management_key_and_fixed_upstream_without_exposing_tokens(proxy, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer management-secret"
        if request.url.path.endswith("auth-files"):
            return httpx.Response(200, json={"files": [{"provider": "claude", "auth_index": "a"}]})
        payload = json.loads(request.content)
        assert payload["auth_index"] == "a"
        assert payload["method"] == "GET"
        assert payload["url"] == "https://api.anthropic.com/api/oauth/usage"
        assert payload["header"]["Authorization"] == "Bearer $TOKEN$"
        return httpx.Response(200, json={"status_code": 200, "body": json.dumps({
            "five_hour": {"utilization": 25}, "seven_day": {"utilization": 60}})})

    mock_proxy(monkeypatch, handler)
    result = cliproxy_quota.read_quota(proxy)
    assert result["ok"] and result["windows"]["five_hour"]["remaining"] == 75
    assert result["windows"]["seven_day"]["remaining"] == 40
    assert len(calls) == 2
    assert "management-secret" not in json.dumps(result)


@pytest.mark.parametrize("files", [[], [{"provider": "codex", "auth_index": "a"}],
    [{"provider": "claude", "auth_index": "a", "disabled": True}],
    [{"provider": "claude", "auth_index": "a"}, {"provider": "claude", "auth_index": "b"}]])
def test_ambiguous_or_missing_accounts_never_query_an_arbitrary_account(proxy, monkeypatch, files):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"files": files})
    mock_proxy(monkeypatch, handler)
    result = cliproxy_quota.read_quota(proxy)
    assert not result["ok"] and "Select one" in result["error"]


@pytest.mark.parametrize("reply", [
    httpx.Response(403, text="management-secret"),
    httpx.Response(200, text="not-json management-secret"),
    httpx.Response(200, json={"status_code": 403, "body": "management-secret"}),
    httpx.Response(200, json={"status_code": 200, "body": "[]"}),
    httpx.Response(200, json={"status_code": 200, "body": {"five_hour": {"utilization": "NaN"}}}),
    httpx.Response(200, json={"status_code": 200, "body": {"five_hour": {"utilization": []}}}),
    httpx.Response(200, json={"status_code": 200, "body": '{"five_hour":{"utilization":null}}'}),
])
def test_provider_errors_are_soft_and_redacted(proxy, monkeypatch, reply):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"files": [{"provider": "claude", "auth_index": "a"}]})
        return reply
    mock_proxy(monkeypatch, handler)
    result = cliproxy_quota.read_quota(proxy)
    assert result["supported"] and not result["ok"]
    assert "management-secret" not in json.dumps(result)


def test_network_failure_is_soft(proxy, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("private upstream detail")
    mock_proxy(monkeypatch, handler)
    assert not cliproxy_quota.read_quota(proxy)["ok"]


def test_settings_preserve_and_clear_quota_configuration(api_client, monkeypatch):
    client, _ = api_client
    body = {"name": "proxy", "kind": "anthropic", "base_url": "http://proxy:8317",
            "quota_source": "cliproxy", "quota_key_var": "MGMT", "quota_auth_index": "a"}
    assert client.post("/api/settings/endpoints", json=body).status_code == 200
    assert client.put("/api/settings/endpoints/proxy", json={
        "name": "proxy", "kind": "anthropic", "api_key": "client-secret"}).status_code == 200
    rows = client.get("/api/settings/endpoints").json()["endpoints"]
    row = next(r for r in rows if r["name"] == "proxy")
    assert row["quota_auth_index"] == "a" and row["quota_key_var"] == "MGMT"
    assert row["base_url"] == "http://proxy:8317"
    assert row["has_subscription_quota"]
    assert "client-secret" not in json.dumps(rows)
    monkeypatch.setattr(cliproxy_quota, "read_quota", lambda cfg: {"supported": True, "ok": True})
    assert client.get("/api/settings/endpoints/proxy/quota").json()["ok"]
    assert client.put("/api/settings/endpoints/proxy", json={
        **body, "quota_source": "", "quota_auth_index": ""}).status_code == 200
    assert client.get("/api/settings/endpoints/proxy/quota").json() == {"supported": False}
