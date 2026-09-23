"""The browser-view relay's two outward-facing edges: what it will fetch, and how its pass
travels.

The URL arithmetic (`test_browser_proxy.py`) and the routes (`test_browser_view_routes.py`)
were both already pinned; what was not is the hop the relay does NOT choose. It ran with
`follow_redirects=True`, so the FIRST hop was safely derived from the configured URL and every
one after it belonged to the upstream — and the upstream here is a raw websockify on a port
published to the whole tailnet with no credential. A 302 from it turned the console into an
SSRF proxy reachable with nothing but the browser-view pass, from a daemon that can talk to
cliproxy, the chrome CDP port, the LAN and the internet.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from rsched.web import api_browser_view
from rsched.web.app import create_app


@pytest.fixture
def app_client(tmp_path):
    from rsched.config import ServerConfig

    cfg = ServerConfig(token="tok", routines_home=tmp_path / "routines",
                       conversations_home=tmp_path / "conversations",
                       background_home=tmp_path / "background",
                       libraries_home=tmp_path / "library")
    for d in (cfg.routines_home, cfg.conversations_home, cfg.background_home,
              cfg.libraries_home):
        d.mkdir(parents=True, exist_ok=True)
    cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"
    return TestClient(create_app(cfg, with_scheduler=False)), cfg


def _stub_upstream(monkeypatch, response: httpx.Response) -> dict:
    """Replace the relay's one outbound client with a stub that answers `response`, and
    return the kwargs it was built with — how the client is CONFIGURED is the finding."""
    seen: dict = {}

    class _Client:
        def __init__(self, *_a, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, headers=None):
            seen["request_headers"] = dict(headers or {})
            response.request = httpx.Request("GET", url)
            return response

    monkeypatch.setattr(api_browser_view.httpx, "AsyncClient", _Client)
    return seen


def test_a_redirecting_upstream_does_not_become_an_ssrf_proxy(app_client, monkeypatch):
    client, _cfg = app_client
    built = _stub_upstream(monkeypatch, httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"},
        content=b"never-fetched"))
    r = client.get("/browser-view/app/ui.js", headers={"Authorization": "Bearer tok"})
    assert built["follow_redirects"] is False       # the hop is never the upstream's to choose
    assert r.status_code == 502
    assert "169.254.169.254" in r.text      # named, not silently swallowed
    assert "does not follow redirects" in r.text
    assert "never-fetched" not in r.text


def test_an_ordinary_asset_is_still_relayed(app_client, monkeypatch):
    client, _cfg = app_client
    _stub_upstream(monkeypatch, httpx.Response(
        200, headers={"content-type": "application/javascript"}, content=b"export const x=1;"))
    r = client.get("/browser-view/app/ui.js", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.text == "export const x=1;"


def test_the_pass_cookie_is_secure_behind_a_terminating_proxy(app_client):
    """`request.url.scheme` is `http` behind tailscale serve --https or any reverse proxy, so
    keying Secure on it alone shipped the pass unprotected exactly where the user's own
    connection was the encrypted one."""
    client, _cfg = app_client
    plain = client.post("/api/browser-view/pass", headers={"Authorization": "Bearer tok"})
    assert "Secure" not in plain.headers["set-cookie"]
    fronted = client.post("/api/browser-view/pass",
                          headers={"Authorization": "Bearer tok",
                                   "x-forwarded-proto": "https, http"})
    assert "Secure" in fronted.headers["set-cookie"]
    assert "HttpOnly" in fronted.headers["set-cookie"]


def test_the_relay_authenticates_to_the_sidecar(app_client, monkeypatch):
    """The sidecar's ports sit behind a bearer proxy now (deploy/browser-auth-proxy.py), so the
    console is one of its CALLERS: without the header the screen is a 401 the user cannot act on.

    Asserted on how the request is BUILT, like the redirect test beside it — the alternative is
    a live sidecar, which a unit test must not need.
    """
    from rsched.web import browser_proxy

    monkeypatch.setattr(browser_proxy, "auth_headers", lambda: {"Authorization": "Bearer t0k"})
    client, _cfg = app_client
    built = _stub_upstream(monkeypatch, httpx.Response(200, content=b"ok",
                                                       headers={"content-type": "text/plain"}))
    r = client.get("/browser-view/app/ui.js", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert built["request_headers"].get("Authorization") == "Bearer t0k"
