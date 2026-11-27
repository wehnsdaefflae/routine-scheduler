"""OAuthRefreshManager: refreshes an expiring connection near expiry (persisting a ROTATED
refresh_token), skips non-expiring and not-yet-due ones, flags needs_reauth + notifies on a
provider rejection, and contains a connection whose refresh RAISES per connection (tickguard)
instead of swallowing it into a log line. httpx is mocked — no network."""

from __future__ import annotations

import time

import pytest

from helpers import tmp_server
from rsched import secrets
from rsched.daemon.oauth_refresh import OAuthRefreshManager
from rsched.health_events import HEALTH_EVENTS_FILE
from rsched.oauth import exchange, store
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
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "gid")     # google is an expiring provider
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_SECRET", "gsec")


@pytest.fixture
def mgr(tmp_path):
    """The manager under a TMP routines home: its ItemGuard files health events under
    `<routines_home>/.control/`, so a bare ServerConfig() would write into the live instance."""
    return OAuthRefreshManager(tmp_server(tmp_path))


def _health(mgr) -> str:
    path = mgr.server.routines_home / ".control" / HEALTH_EVENTS_FILE
    return path.read_text("utf-8") if path.is_file() else ""


def test_refreshes_near_expiry_and_rotates(mgr, monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="OLD",
                                    refresh_token="RT1", expires_at=time.time() + 60))
    seen = {}

    def fake_post(url, **kw):
        seen["data"] = kw.get("data")
        return _Resp(200, {"access_token": "NEW", "refresh_token": "RT2", "expires_in": 3600})

    monkeypatch.setattr(exchange.httpx, "post", fake_post)
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None
    assert conn.access_token == "NEW"
    assert conn.refresh_token == "RT2"                 # rotation persisted
    assert conn.expires_at > time.time() + 3000
    assert conn.needs_reauth is False
    assert seen["data"]["grant_type"] == "refresh_token"


def test_skips_non_expiring(mgr, monkeypatch):
    store.set_connection(Connection(provider="notion", account="a", access_token="AT"))
    calls = []
    monkeypatch.setattr(exchange.httpx, "post", lambda *a, **k: calls.append(1))
    mgr._refresh_due(time.time())
    assert calls == []                                 # Notion: no refresh work at all


def test_skips_not_yet_due(mgr, monkeypatch):
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 4000))
    calls = []
    monkeypatch.setattr(exchange.httpx, "post", lambda *a, **k: calls.append(1))
    mgr._refresh_due(time.time())
    assert calls == []


def test_rejection_marks_reauth_and_the_record_is_the_notification(mgr, monkeypatch):
    """A refresh the provider rejects flags `needs_reauth` — and that is the whole
    notification: 0.230.0 deleted every engine/daemon-implicit outbound send, so the
    Settings → Connections badge is the only surface (docs/notifications.md)."""
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 60))
    monkeypatch.setattr(exchange.httpx, "post",
                        lambda *a, **k: _Resp(400, {"error": "invalid_grant"}))
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None and conn.needs_reauth is True


@pytest.mark.parametrize("status", [503, 502, 429, 408])
def test_a_provider_hiccup_is_retried_not_a_refusal(mgr, monkeypatch, status):
    """Only a REFUSAL (RFC 6749 §5.2: 400 invalid_grant/…, 401 invalid_client; some providers
    say 403) means the grant is dead. Every other non-200 flagged `needs_reauth` too, and a
    flagged connection is skipped by every later pass and withheld from every run — so one
    provider outage at refresh time cost a re-authorization by hand."""
    store.set_connection(Connection(provider="google", account="me", access_token="AT",
                                    refresh_token="RT", expires_at=time.time() + 60))
    monkeypatch.setattr(exchange.httpx, "post", lambda *a, **k: _Resp(status, {}))
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None and conn.needs_reauth is False
    monkeypatch.setattr(exchange.httpx, "post",                 # the provider is back
                        lambda *a, **k: _Resp(200, {"access_token": "NEW", "expires_in": 3600}))
    mgr._refresh_due(time.time())
    conn = store.get_connection("google", "me")
    assert conn is not None and conn.access_token == "NEW"


# -- per-connection isolation (tickguard) -----------------------------------------------------


def test_one_connection_that_raises_never_starves_the_rest_and_is_filed_once(mgr, monkeypatch):
    """A refresh that RAISES used to hit a bare `except Exception: log.exception(...)`: the rest
    of the pass still ran, but NOTHING reached any surface, so a connection failing every
    five-second tick was a log line nobody reads. `tickguard.ItemGuard` keeps the isolation and
    files `scheduler_tick_error` ONCE — not once per pass — naming the connection key."""
    for account in ("aaa", "zzz"):                     # iteration follows insertion order
        store.set_connection(Connection(provider="google", account=account, access_token="OLD",
                                        refresh_token="RT-" + account,
                                        expires_at=time.time() + 60))
    monkeypatch.setattr(exchange.httpx, "post",
                        lambda *a, **k: _Resp(200, {"access_token": "NEW", "expires_in": 3600}))
    real = mgr._refresh_one

    def refresh_one(conn, prov):
        if conn.account == "aaa":
            raise OSError("an unreadable connection record")
        real(conn, prov)

    monkeypatch.setattr(mgr, "_refresh_one", refresh_one)
    mgr._refresh_due(time.time())
    mgr._refresh_due(time.time())                      # the broken one is still due, still raises

    zzz = store.get_connection("google", "zzz")
    assert zzz is not None and zzz.access_token == "NEW", "the rest of the pass must get its turn"
    stream = _health(mgr)
    assert stream.count('"scheduler_tick_error"') == 1, "once until it next succeeds, not per pass"
    assert "google:aaa" in stream                      # the event names the failing connection


def test_a_successful_refresh_clears_the_filing(mgr, monkeypatch):
    """The once-only filing is keyed on the connection and DISCARDED when it next refreshes, so a
    later failure of the same connection is a new incident and is filed again — a latch that
    never cleared would hide every recurrence after the first."""
    store.set_connection(Connection(provider="google", account="me", access_token="OLD",
                                    refresh_token="RT", expires_at=time.time() + 60))
    monkeypatch.setattr(exchange.httpx, "post",
                        lambda *a, **k: _Resp(200, {"access_token": "NEW", "expires_in": 3600}))
    real = mgr._refresh_one
    boom = {"on": True}

    def refresh_one(conn, prov):
        if boom["on"]:
            raise OSError("the provider client blew up")
        real(conn, prov)

    monkeypatch.setattr(mgr, "_refresh_one", refresh_one)
    now = time.time()
    mgr._refresh_due(now)                              # raises — filed
    assert _health(mgr).count('"scheduler_tick_error"') == 1
    boom["on"] = False
    mgr._refresh_due(now)                              # succeeds — the guard forgets the key
    assert store.get_connection("google", "me").access_token == "NEW"
    boom["on"] = True
    mgr._refresh_due(now + 7200)                       # due again, raises again
    assert _health(mgr).count('"scheduler_tick_error"') == 2
