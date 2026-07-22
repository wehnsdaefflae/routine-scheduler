"""OAuthRefreshManager: refreshes an expiring connection near expiry (persisting a ROTATED
refresh_token), skips non-expiring and not-yet-due ones, and flags needs_reauth + notifies on a
provider rejection. httpx is mocked — no network."""

from __future__ import annotations

import time

import pytest

from rsched import secrets
from rsched.config import ServerConfig
from rsched.daemon import oauth_refresh
from rsched.daemon.oauth_refresh import OAuthRefreshManager
from rsched.oauth import store
from rsched.oauth.store import Connection


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")
    monkeypatch.setattr(oauth_refresh.notify, "send", lambda *a, **k: True)
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "gid")     # google is an expiring provider
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_SECRET", "gsec")


def _mgr():
    return OAuthRefreshManager(ServerConfig())


def test_refreshes_near_expiry_and_rotates(monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="OLD",
                                    refresh_token="RT1", expires_at=time.time() + 60))
    seen = {}

    def fake_post(url, **kw):
        seen["data"] = kw.get("data")
        return _Resp(200, {"access_token": "NEW", "refresh_token": "RT2", "expires_in": 3600})

    monkeypatch.setattr(oauth_refresh.httpx, "post", fake_post)
    _mgr()._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None
    assert conn.access_token == "NEW"
    assert conn.refresh_token == "RT2"                 # rotation persisted
    assert conn.expires_at > time.time() + 3000
    assert conn.needs_reauth is False
    assert seen["data"]["grant_type"] == "refresh_token"


def test_skips_non_expiring(monkeypatch):
    store.set_connection(Connection(provider="notion", account="a", access_token="AT"))
    calls = []
    monkeypatch.setattr(oauth_refresh.httpx, "post", lambda *a, **k: calls.append(1))
    _mgr()._refresh_due(time.time())
    assert calls == []                                 # Notion: no refresh work at all


def test_skips_not_yet_due(monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 4000))
    calls = []
    monkeypatch.setattr(oauth_refresh.httpx, "post", lambda *a, **k: calls.append(1))
    _mgr()._refresh_due(time.time())
    assert calls == []


def test_rejection_marks_reauth_and_notifies(monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 60))
    monkeypatch.setattr(oauth_refresh.httpx, "post",
                        lambda *a, **k: _Resp(400, {"error": "invalid_grant"}))
    notes = []
    monkeypatch.setattr(oauth_refresh.notify, "send",
                        lambda server, text, **k: notes.append(text) is None or True)
    # Discord is opt-in: the ping fires only when a routine BINDS this connection and
    # holds the communication permission
    mgr = _mgr()
    monkeypatch.setattr(type(mgr), "_discord_opted_in", lambda self, conn: True)
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None and conn.needs_reauth is True
    assert notes and "google:me" in notes[0]


def test_rejection_without_optin_flags_but_does_not_ping(monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 60))
    monkeypatch.setattr(oauth_refresh.httpx, "post",
                        lambda *a, **k: _Resp(400, {"error": "invalid_grant"}))
    notes = []
    monkeypatch.setattr(oauth_refresh.notify, "send",
                        lambda server, text, **k: notes.append(text) is None or True)
    mgr = _mgr()
    monkeypatch.setattr(type(mgr), "_discord_opted_in", lambda self, conn: False)
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None and conn.needs_reauth is True     # the flag always lands
    assert notes == []                                        # the ping is opt-in
