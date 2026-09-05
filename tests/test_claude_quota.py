"""The Claude subscription quota read — the operator's "% remaining", from the real source.

What these pin is mostly the FAILURE side, because that is what the feature is made of: an
undocumented third-party endpoint and a credential that expires with nothing to refresh it. Every
one of those paths has to produce a sentence naming the fix, and none of them may raise — a panel
that can 500 the Settings page over someone else's API is worse than one that says it does not
know.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from rsched.endpoints import claude_quota

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)


def _creds(tmp_path, *, token="tok-abc", expires: datetime | None = None):  # noqa: S107
    p = tmp_path / ".credentials.json"
    oauth = {"accessToken": token, "scopes": ["user:profile", "user:inference"]}
    if expires is not None:
        oauth["expiresAt"] = int(expires.timestamp() * 1000)
    p.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
    return str(p)


RAW = {
    "five_hour": {"utilization": 39.0, "resets_at": "2026-09-05T11:10:00Z"},
    "seven_day": {"utilization": 68.4, "resets_at": "2026-09-09T00:00:00Z"},
    "seven_day_sonnet": {"utilization": 12.0},
    "unknown_window": {"utilization": 99.0},
}


def test_utilization_becomes_the_remaining_percentage_the_operator_asked_for():
    got = claude_quota.normalize(RAW, now=NOW)
    assert got["five_hour"]["remaining"] == 61.0        # 100 - utilization, computed once
    assert got["five_hour"]["utilization"] == 39.0
    assert got["five_hour"]["seconds_until_reset"] == 2 * 3600 + 10 * 60
    assert got["seven_day"]["remaining"] == 31.6
    # a window with no reset stamp still reports its percentage
    assert got["seven_day_sonnet"]["seconds_until_reset"] is None
    # …and a window we do not render is not invented into the payload
    assert "unknown_window" not in got


def test_a_missing_login_says_exactly_what_to_run(tmp_path):
    got = claude_quota.read_quota(path=str(tmp_path / "nope.json"))
    assert got["supported"] is True and got["ok"] is False
    assert "no interactive login" in got["error"]
    assert "claude /login" in got["error"]


def test_an_expired_token_is_reported_from_the_stamp_not_from_a_401(tmp_path):
    """The credentials file carries `expiresAt` and nothing refreshes it headlessly — the live
    instance's had been dead four days and said nothing. Reading the stamp names the cause; a
    bare 401 does not."""
    path = _creds(tmp_path, expires=datetime.now(UTC) - timedelta(days=4))
    got = claude_quota.read_quota(path=path)
    assert got["ok"] is False
    assert "expired" in got["error"] and "4 days ago" in got["error"]
    assert got["expires_at"]                       # and the card can warn BEFORE it lapses


def test_a_live_token_yields_the_windows(tmp_path, monkeypatch):
    path = _creds(tmp_path, expires=datetime.now(UTC) + timedelta(days=20))
    monkeypatch.setattr(claude_quota.httpx, "get",
                        lambda *a, **k: httpx.Response(200, json=RAW))
    got = claude_quota.read_quota(path=path)
    assert got["ok"] is True
    assert got["windows"]["five_hour"]["remaining"] == 61.0
    assert got["manage_url"].startswith("https://claude.ai/")


@pytest.mark.parametrize(("status", "needle"), [
    (401, "no longer accepted"),
    (403, "user:profile scope"),
    (500, "HTTP 500"),
])
def test_every_provider_failure_is_soft_and_names_the_fix(tmp_path, monkeypatch, status, needle):
    path = _creds(tmp_path, expires=datetime.now(UTC) + timedelta(days=20))
    monkeypatch.setattr(claude_quota.httpx, "get",
                        lambda *a, **k: httpx.Response(status, text="nope"))
    got = claude_quota.read_quota(path=path)
    assert got["ok"] is False and needle in got["error"]


def test_a_network_error_and_a_garbled_body_both_degrade(tmp_path, monkeypatch):
    path = _creds(tmp_path, expires=datetime.now(UTC) + timedelta(days=20))

    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")
    monkeypatch.setattr(claude_quota.httpx, "get", boom)
    assert "could not reach" in claude_quota.read_quota(path=path)["error"]

    monkeypatch.setattr(claude_quota.httpx, "get",
                        lambda *a, **k: httpx.Response(200, text="<html>"))
    assert "not JSON" in claude_quota.read_quota(path=path)["error"]


def test_a_shape_change_is_reported_as_one(tmp_path, monkeypatch):
    """It is undocumented. A 200 carrying nothing we recognise must say the shape may have
    changed, not silently render an empty strip."""
    path = _creds(tmp_path, expires=datetime.now(UTC) + timedelta(days=20))
    monkeypatch.setattr(claude_quota.httpx, "get",
                        lambda *a, **k: httpx.Response(200, json={"whatever": 1}))
    got = claude_quota.read_quota(path=path)
    assert got["ok"] is False and "undocumented" in got["error"]


def test_the_route_is_claude_cli_only(api_client):
    """The fixture endpoint is an `openai` one, so the route must say the question does not
    apply rather than reaching for a credential that could not answer it."""
    c, _tmp = api_client
    r = c.get("/api/settings/endpoints/dummy/quota")
    assert r.status_code == 200 and r.json() == {"supported": False}
    assert c.get("/api/settings/endpoints/nope/quota").status_code == 404
