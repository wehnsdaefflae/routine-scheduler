"""F530 — the relayed browser screen must authenticate the way an IFRAME actually asks.

The operator, one release after the relay shipped: *"all of the browser preview elements you
added to the webui show `{"detail":"missing or invalid token"}`"*. That string is this app's
own 401 body, rendered inside the frame.

Three facts, and each one alone is fatal:

1. The DOCUMENT request carried no credential. `frameSrc()` put the ticket inside noVNC's
   `path` parameter — which is for the websocket — so `/browser-view/vnc.html?…` arrived as a
   naked GET and `require_auth` refused it.
2. Even ticketing the document is not enough: noVNC then fetches its OWN siblings
   (`app/ui.js`, `app/styles/base.css`, the images) with no query string at all, because the
   page builds those paths itself. Every one of them would 401.
3. An SSE ticket lives 60 seconds. A browser session is watched for minutes or hours, so any
   short-TTL credential in the URL dies mid-session and the screen goes blank later instead
   of immediately — which is worse, because it looks intermittent.

So the screen gets its own credential: a cookie, set by an authenticated endpoint, scoped to
the relay's path, carried automatically by every sub-resource the frame requests, and living
as long as a watched session plausibly does.

These tests speak in requests the BROWSER makes — naked GETs — rather than in requests the
console makes with a bearer header, which is the distinction the shipped bug turned on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rsched.web.app import create_app


@pytest.fixture
def client(tmp_path):
    from rsched.config import ServerConfig

    cfg = ServerConfig(token="tok", routines_home=tmp_path / "routines",
                       conversations_home=tmp_path / "conversations",
                       background_home=tmp_path / "background",
                       libraries_home=tmp_path / "library")
    for d in (cfg.routines_home, cfg.conversations_home, cfg.background_home,
              cfg.libraries_home):
        d.mkdir(parents=True, exist_ok=True)
    # an upstream that is refused instantly: these tests are about AUTH, and a 502 proves
    # the request got past the gate and reached the relay
    cfg.browser_view_url = "http://127.0.0.1:1/vnc.html"
    return TestClient(create_app(cfg, with_scheduler=False)), cfg


AUTH = {"Authorization": "Bearer tok"}


def test_without_the_pass_the_relay_still_refuses(client):
    """The screen shows a live signed-in browser — it never becomes public."""
    c, _cfg = client
    assert c.get("/browser-view/vnc.html").status_code == 401


def test_the_pass_endpoint_needs_the_operator_token(client):
    """Minting the screen's credential is itself an authenticated act."""
    c, _cfg = client
    assert c.post("/api/browser-view/pass").status_code == 401


def test_a_granted_pass_admits_the_document_and_its_siblings(client):
    """The whole bug in one test: the frame asks for the page and then for its own assets,
    and NONE of those requests carries a header or a query the page controls."""
    c, _cfg = client
    granted = c.post("/api/browser-view/pass", headers=AUTH)
    assert granted.status_code == 200, granted.text

    # the document, exactly as an <iframe src> fetches it — no header, no ticket
    doc = c.get("/browser-view/vnc.html")
    assert doc.status_code == 502, doc.text          # past auth, upstream is dead

    # and the assets noVNC requests for itself, with no query string at all
    for asset in ("app/ui.js", "app/styles/base.css", "app/images/info.svg"):
        r = c.get(f"/browser-view/{asset}")
        assert r.status_code == 502, f"{asset} was refused: {r.status_code} {r.text[:120]}"


def test_the_pass_outlives_a_short_lived_sse_ticket(client):
    """A watched session lasts far longer than 60s; a credential that expires mid-session
    fails LATER, which reads as flakiness rather than as a bug."""
    from rsched.web import api_browser_view

    assert api_browser_view.PASS_TTL_S >= 3600, api_browser_view.PASS_TTL_S


def test_the_pass_is_scoped_to_the_screen_and_not_a_console_credential(client):
    """A cookie that authenticated the whole API would turn one embedded screen into a
    general credential living in the browser."""
    c, _cfg = client
    c.post("/api/browser-view/pass", headers=AUTH)
    # the cookie is set for the relay's path only
    jar = [ck for ck in c.cookies.jar if ck.name == "rsched_browser_view"]
    assert jar and jar[0].path == "/browser-view", [(x.name, x.path) for x in c.cookies.jar]
    # and it opens nothing else, even though the browser would send it if the path matched
    assert c.get("/api/routines").status_code == 401
