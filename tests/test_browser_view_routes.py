"""The relayed browser screen, end to end through the real app (F527).

`test_browser_proxy.py` pins the URL arithmetic; this pins the ROUTES: that the console
serves the noVNC page from its own origin, that an unconfigured instance says so instead of
proxying nowhere, that a wedged upstream reports loudly rather than showing a blank frame,
and — the case that actually breaks a page — that noVNC's own asset requests, which carry no
credential of any kind, are still served.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rsched.web.app import create_app


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from rsched.config import ServerConfig

    cfg = ServerConfig(token="tok", routines_home=tmp_path / "routines",
                       conversations_home=tmp_path / "conversations",
                       background_home=tmp_path / "background",
                       libraries_home=tmp_path / "library")
    for d in (cfg.routines_home, cfg.conversations_home, cfg.background_home,
              cfg.libraries_home):
        d.mkdir(parents=True, exist_ok=True)
    app = create_app(cfg, with_scheduler=False)
    return TestClient(app), cfg


def test_an_unconfigured_screen_reports_503_rather_than_proxying_nowhere(app_client):
    client, cfg = app_client
    cfg.browser_view_url = ""
    r = client.get("/browser-view/vnc.html", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 503
    assert "browser_view_url" in r.text


def test_a_traversal_path_is_refused_at_the_relay(app_client):
    client, cfg = app_client
    cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"
    r = client.get("/browser-view/..%2f..%2fetc%2fpasswd",
                   headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400


def test_an_unreachable_upstream_is_reported_loudly_not_blankly(app_client):
    """The whole defect F527 fixes was a silent blank frame — a dead upstream must never
    reproduce that shape."""
    client, cfg = app_client
    # port 1 on loopback: nothing listens, connection refused immediately
    cfg.browser_view_url = "http://127.0.0.1:1/vnc.html"
    r = client.get("/browser-view/vnc.html", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 502
    assert "did not answer" in r.text


def test_the_relay_is_reachable_with_a_ticket_and_refused_without_one(app_client):
    """noVNC's assets are fetched by the browser from inside the frame and carry NO bearer
    header. They must still be served — otherwise the page half-loads and the screen is
    blank for a second, unrelated reason."""
    client, cfg = app_client
    cfg.browser_view_url = "http://127.0.0.1:1/vnc.html"

    naked = client.get("/browser-view/app/ui.js")
    assert naked.status_code == 401          # no credential at all is still refused

    ticket = client.post("/api/sse-ticket",
                         headers={"Authorization": "Bearer tok"}).json()["ticket"]
    withticket = client.get(f"/browser-view/app/ui.js?ticket={ticket}")
    # 502 (not 401): auth passed and the relay tried the dead upstream
    assert withticket.status_code == 502
