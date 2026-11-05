"""A private/LAN redirect_uri is named AS THE CAUSE before the user leaves the console.

The operator re-authorized a routine's Google connection and got Google's own refusal page
instead of a consent screen: "Access blocked: Authorization error — device_id and device_name
are required for private IP", naming our OAuth app and 192.168.0.128. Google refuses an
authorization request whose `redirect_uri` resolves to a private address BEFORE any consent
screen, so nothing in the console said what was wrong and no routine's Google connection could
be re-authorized at all.

Our own code knows that redirect_uri (`web/settings/oauth.py` `_redirect_uri`), so the flow says
so itself: authorize-start still returns the authorize URL — the operator may genuinely need to
try it, and the detection is an inference about what a provider will do — but it carries a
`caveat` naming the host, the cause and the remedy, which the card shows for confirmation before
it opens the provider's tab. A public host carries no caveat: a working flow must stay silent.
"""

from __future__ import annotations

import pytest

from rsched import secrets
from rsched.oauth import redirecturi, store
from rsched.web.settings import oauth as oauth_mod


@pytest.fixture
def conn_client(api_client, monkeypatch):
    client, tmp_path = api_client
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "gcid")
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_SECRET", "gsek")
    secrets.set_secret("GOOGLE_OAUTH_SCOPES", "openid")
    oauth_mod._flows.clear()
    oauth_mod._state_index.clear()
    return client


def _start(client, public_url: str) -> dict:
    client.app.state.server.public_url = public_url
    r = client.post("/api/settings/oauth/google/authorize-start", json={"account": "me"})
    assert r.status_code == 200, r.text
    return r.json()


def test_private_ip_redirect_is_warned_with_the_cause_named(conn_client):
    """The reported case: public_url is a LAN address, so the redirect_uri Google would receive
    is one it refuses. The console says that itself, naming the host."""
    body = _start(conn_client, "http://192.168.0.128:8820")
    caveat = body["caveat"]
    assert "192.168.0.128" in caveat                 # the host it would have sent
    assert "private" in caveat.lower()               # why the provider will refuse it
    assert "public_url" in caveat                    # the remedy, in the console's own terms


def test_a_warned_flow_is_still_startable(conn_client):
    """A WARNING, not a refusal: the authorize URL still comes back (with its flow registered),
    so an operator who knows better can proceed. A hard refusal would leave no way through."""
    body = _start(conn_client, "http://192.168.0.128:8820")
    assert body["authorize_url"].startswith("https://accounts.google.com/")
    assert body["flow_id"] in oauth_mod._flows


def test_loopback_redirect_is_warned_too(conn_client):
    body = _start(conn_client, "http://127.0.0.1:8820")
    assert "127.0.0.1" in body["caveat"]


def test_a_public_host_carries_no_caveat(conn_client):
    """The regression that matters: a working flow must not grow a warning."""
    body = _start(conn_client, "https://host.ts.net")
    assert body["caveat"] == ""


@pytest.mark.parametrize("url", [
    "http://10.0.0.5/oauth/callback",                # RFC1918 10/8
    "http://172.16.4.4:8820/oauth/callback",         # RFC1918 172.16/12
    "http://192.168.0.128:8820/oauth/callback",      # RFC1918 192.168/16
    "http://127.0.0.1:8820/oauth/callback",          # loopback 127/8
    "http://169.254.7.7/oauth/callback",             # link-local 169.254/16
    "http://[fd00::1]:8820/oauth/callback",          # IPv6 unique-local fc00::/7
    "http://[::1]:8820/oauth/callback",              # IPv6 loopback
])
def test_every_unroutable_range_is_caught(url):
    assert redirecturi.private_host_caveat(url) != ""


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/oauth/callback",                  # a public literal
    "http://[2606:4700::1111]/oauth/callback",        # a public IPv6 literal
    "",                                               # nothing to judge
    "not a url at all",
])
def test_a_routable_or_unparseable_target_is_left_alone(url):
    assert redirecturi.private_host_caveat(url) == ""


def test_a_name_resolving_to_a_private_address_is_caught(monkeypatch):
    """The host need not be a literal: a DNS name pointing into the LAN is refused by the
    provider just the same, so resolution is part of the check."""
    monkeypatch.setattr(redirecturi, "resolve_host", lambda host: ["192.168.0.128"])
    caveat = redirecturi.private_host_caveat("http://nas.local/oauth/callback")
    assert "nas.local" in caveat and "192.168.0.128" in caveat


def test_resolution_failure_is_silent(monkeypatch):
    """A name that cannot be resolved here says nothing: a guess is not a cause."""
    def boom(host):
        raise OSError("no such host")

    monkeypatch.setattr(redirecturi, "resolve_host", boom)
    assert redirecturi.private_host_caveat("http://whatever.example/oauth/callback") == ""
