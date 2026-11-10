"""Proxy quota must remain account-specific, fail soft, and never expose credentials."""

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from helpers import mock_proxy
from rsched.config import EndpointConfig
from rsched.endpoints import cliproxy_mgmt, cliproxy_quota


@pytest.fixture
def proxy(monkeypatch):
    # the management client (key + URL) is cliproxy_mgmt's, shared with the sign-in module
    monkeypatch.setattr(cliproxy_mgmt, "resolve_api_key", lambda **kw: "management-secret")
    return EndpointConfig(kind="anthropic", base_url="http://proxy:8317",
                          quota_source="cliproxy")


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


def _one_claude_account(reply):
    """A proxy handler with one enabled Claude account whose api-call answers `reply`."""
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"files": [{"provider": "claude",
                                                        "auth_index": "a"}]})
        return reply
    return handler


@pytest.mark.parametrize("reply", [
    # the OUTER response is rate-limited (the proxy itself refuses)
    httpx.Response(429, headers={"Retry-After": "90"}, text="management-secret"),
    # …and the envelope's own status, which is how a 429 from Claude arrives: the proxy
    # answers 200 and carries the upstream status and headers inside
    httpx.Response(200, json={"status_code": 429, "headers": {"retry-after": "90"},
                              "body": "management-secret"}),
])
def test_a_rate_limited_read_reports_when_it_may_read_again(proxy, monkeypatch, reply):
    """A 429 is not an error to re-ask on the next poll: the account is being hammered at the
    console's own cadence and the operator is told nothing about when a figure will exist.
    `Retry-After` is the upstream's own answer to "when", so it is carried out verbatim.
    """
    mock_proxy(monkeypatch, _one_claude_account(reply))
    result = cliproxy_quota.read_quota(proxy)

    assert result["supported"] and not result["ok"]
    assert result["retry_after_s"] == 90
    assert result["rate_limited"] is True
    # the person reading the card is told WHEN, not just that something failed
    assert "90" in result["error"] and "again" in result["error"].lower()
    assert "management-secret" not in json.dumps(result)


def test_an_http_date_retry_after_is_read_as_seconds(proxy, monkeypatch):
    """RFC 9110 allows either form, and Anthropic has sent both. A date the console cannot
    turn into a countdown is the same as no answer at all, so it is converted here — once,
    server-side — rather than in two renderers.
    """
    when = format_datetime(datetime.now(tz=UTC) + timedelta(seconds=120), usegmt=True)
    mock_proxy(monkeypatch, _one_claude_account(
        httpx.Response(429, headers={"Retry-After": when}, text="nope")))
    result = cliproxy_quota.read_quota(proxy)

    assert result["rate_limited"] is True
    # a clock that moves between the header and the read makes an exact equality flaky
    assert 60 <= result["retry_after_s"] <= 180, result["retry_after_s"]


def test_a_429_without_the_header_is_still_marked_rate_limited(proxy, monkeypatch):
    """The header is optional. Without it there is no countdown to show — but "we are being
    rate limited" is still the thing the operator needs, and a caller that backs off only when
    it gets a number would poll straight through the commonest 429.
    """
    mock_proxy(monkeypatch, _one_claude_account(httpx.Response(429, text="nope")))
    result = cliproxy_quota.read_quota(proxy)

    assert result["rate_limited"] is True
    assert result.get("retry_after_s") is None
    assert "rate" in result["error"].lower()


@pytest.mark.parametrize("header", ["", "soon", "-5", "0", "99999999999999999999"])
def test_an_unusable_retry_after_never_becomes_a_wrong_countdown(proxy, monkeypatch, header):
    """A header the server cannot parse must leave NO figure rather than a plausible wrong one:
    a card saying "reads again in 0s" invites exactly the re-ask this change exists to stop,
    and a negative or absurd value would render as a countdown that never ends.
    """
    mock_proxy(monkeypatch, _one_claude_account(
        httpx.Response(429, headers={"Retry-After": header} if header else {}, text="nope")))
    result = cliproxy_quota.read_quota(proxy)

    assert result["rate_limited"] is True
    assert result.get("retry_after_s") is None, header


def test_a_non_429_error_carries_no_retry_fields(proxy, monkeypatch):
    """The ordinary failure path is unchanged: a 403 is not a rate limit, and marking it one
    would make the console wait before re-reading something a reconnect would fix at once.
    """
    mock_proxy(monkeypatch, _one_claude_account(
        httpx.Response(200, json={"status_code": 403, "body": "nope"})))
    result = cliproxy_quota.read_quota(proxy)

    assert not result["ok"] and not result.get("rate_limited")
    assert "retry_after_s" not in result


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
