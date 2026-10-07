"""The agent desktops' screens, relayed through the console like the browser's (F527/F530).

ONE relay implementation serves both screens (`screen_proxy.SCREENS`), so what the browser
files pin — traversal, no redirect following, the pass cookie's flags — holds here by
construction. What this file pins is what the desktop screen adds, and what must not leak
between the two:

- one websockify serves EVERY desktop and the socket's `?token=` picks the screen, so that one
  query IS forwarded upstream — and only once it has the broker's shape (32 hex), because it
  is the one part of the upstream URL the browser chooses;
- the upstream's bearer is the OPERATOR's token, a secret no routine is granted;
- the desktop pass is its own: path-scoped to `/desktop-view`, kept in its own store, so a
  browser pass opens no desktop and a desktop pass no browser.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import TEST_TOKEN, make_test_server
from rsched.web import api_screen_view, screen_proxy
from rsched.web.app import create_app
from rsched.web.screen_proxy import BROWSER, DESKTOP

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
SCREEN = "0123456789abcdef0123456789abcdef"      # the shape `secrets.token_hex(16)` makes
PAGE = "http://172.30.7.20:6080/vnc.html"


class _Cfg:
    def __init__(self, url: str) -> None:
        self.desktop_view_url = url


@pytest.fixture
def client(tmp_path):
    server = make_test_server(tmp_path, desktop_view_url=PAGE,
                              browser_view_url="http://127.0.0.1:6080/vnc.html")
    with TestClient(create_app(server, with_scheduler=False)) as c:
        yield c


# ---- the URL arithmetic --------------------------------------------------------------------

def test_the_socket_upstream_carries_the_screen_token_and_nothing_else():
    assert DESKTOP.ws_upstream_for(_Cfg(PAGE), SCREEN) == \
        f"ws://172.30.7.20:6080/websockify?token={SCREEN}"
    assert DESKTOP.ws_upstream_for(_Cfg("https://desk.example/novnc/vnc.html"), SCREEN) == \
        f"wss://desk.example/novnc/websockify?token={SCREEN}"


@pytest.mark.parametrize("token", [
    "",                                        # no screen chosen: there is no default desktop
    SCREEN[:-1],                               # too short
    SCREEN.upper(),                            # the broker makes lower-case hex only
    SCREEN + "&host=10.0.0.1",                 # a second parameter smuggled in
    "../../etc/passwd" + "0" * 16,
    "routines--alpha",                         # a NAME: guessable, and never the key
])
def test_a_token_of_any_other_shape_is_refused(token):
    with pytest.raises(ValueError, match="32 hex"):
        DESKTOP.ws_upstream_for(_Cfg(PAGE), token)


def test_the_browser_socket_forwards_no_query_at_all():
    """The browser has one screen; a token the caller adds is not a parameter it relays."""
    class _B:
        browser_view_url = "http://10.0.0.5:6080/vnc.html"

    assert BROWSER.ws_upstream_for(_B(), SCREEN) == "ws://10.0.0.5:6080/websockify"


def test_assets_resolve_against_the_configured_page_and_refuse_traversal():
    assert DESKTOP.upstream_for(_Cfg(PAGE), "app/ui.js") == "http://172.30.7.20:6080/app/ui.js"
    assert DESKTOP.upstream_for(_Cfg(PAGE), "") == PAGE
    assert DESKTOP.upstream_for(_Cfg(""), "vnc.html") is None
    with pytest.raises(ValueError):
        DESKTOP.upstream_for(_Cfg(PAGE), "%2e%2e/%2e%2e/etc/passwd")


def test_a_path_belongs_to_the_screen_whose_prefix_it_is_under():
    assert screen_proxy.screen_for_path("/desktop-view/app/ui.js") is DESKTOP
    assert screen_proxy.screen_for_path("/browser-view") is BROWSER
    assert screen_proxy.screen_for_path("/desktop-viewer/x") is None
    assert screen_proxy.screen_for_path("/api/desktops") is None


# ---- the routes ----------------------------------------------------------------------------

def _stub_upstream(monkeypatch, response: httpx.Response) -> dict:
    seen: dict = {}

    class _Client:
        def __init__(self, *_a, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["request_headers"] = dict(headers or {})
            response.request = httpx.Request("GET", url)
            return response

    monkeypatch.setattr(api_screen_view.httpx, "AsyncClient", _Client)
    return seen


def _secrets(monkeypatch):
    monkeypatch.setattr(screen_proxy, "auth_headers",
                        lambda key: {"Authorization": f"Bearer <{key}>"})


def test_an_asset_is_relayed_with_the_operator_bearer_and_the_page_query(client, monkeypatch):
    _secrets(monkeypatch)
    built = _stub_upstream(monkeypatch, httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>novnc</html>"))
    q = f"autoconnect=1&path=desktop-view%2Fwebsockify%3Ftoken%3D{SCREEN}&view_only=1"
    r = client.get(f"/desktop-view/vnc.html?{q}", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.text == "<html>novnc</html>"
    assert built["request_headers"]["Authorization"] == "Bearer <DESKTOP_OPERATOR_TOKEN>"
    assert built["url"] == f"{PAGE}?{q}"
    assert built["follow_redirects"] is False


def test_a_redirecting_desktop_upstream_is_refused_and_named(client, monkeypatch):
    _stub_upstream(monkeypatch, httpx.Response(
        302, headers={"location": "http://169.254.169.254/"}, content=b"never-fetched"))
    r = client.get("/desktop-view/app/ui.js", headers=AUTH)
    assert r.status_code == 502
    assert "169.254.169.254" in r.text and "desktop screen" in r.text
    assert "never-fetched" not in r.text


def test_an_unconfigured_desktop_screen_says_which_field_to_set(client):
    client.app.state.server.desktop_view_url = ""
    r = client.get("/desktop-view/vnc.html", headers=AUTH)
    assert r.status_code == 503 and "desktop_view_url" in r.text
    assert client.post("/api/desktop-view/pass", headers=AUTH).status_code == 503


def test_the_desktop_pass_is_its_own_cookie_on_its_own_path(client):
    granted = client.post("/api/desktop-view/pass", headers=AUTH)
    assert granted.status_code == 200, granted.text
    jar = [ck for ck in client.cookies.jar if ck.name == "rsched_desktop_view"]
    assert jar and jar[0].path == "/desktop-view", [(c.name, c.path) for c in client.cookies.jar]
    cookie = granted.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    # the frame's own requests carry the cookie and nothing else, and are admitted
    assert client.get("/desktop-view/app/ui.js").status_code != 401


def test_a_pass_opens_only_the_screen_it_was_minted_for(client):
    """Two screens, two stores: the cookie paths keep a browser from SENDING the wrong pass,
    and the stores keep the server from ACCEPTING one sent anyway."""
    desktop = client.post("/api/desktop-view/pass", headers=AUTH).cookies["rsched_desktop_view"]
    browser = client.post("/api/browser-view/pass", headers=AUTH).cookies["rsched_browser_view"]
    for cookie, value, path in (("rsched_browser_view", desktop, "/browser-view/app/ui.js"),
                                ("rsched_desktop_view", browser, "/desktop-view/app/ui.js")):
        bare = TestClient(client.app)
        bare.cookies.set(cookie, value)
        assert bare.get(path).status_code == 401, (cookie, path)


def _fake_upstream(monkeypatch) -> dict:
    """Stand in for `websockets.connect`: record what the relay dialled, then end at once."""
    import websockets

    seen: dict = {}

    class _Up:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def send(self, _data):
            return None

    def connect(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return _Up()

    monkeypatch.setattr(websockets, "connect", connect)
    return seen


def test_the_socket_forwards_the_screen_token_with_the_operator_bearer(client, monkeypatch):
    _secrets(monkeypatch)
    seen = _fake_upstream(monkeypatch)
    client.post("/api/desktop-view/pass", headers=AUTH)
    with client.websocket_connect(f"/desktop-view/websockify?token={SCREEN}",
                                  subprotocols=["binary"]):
        pass
    assert seen["url"] == f"ws://172.30.7.20:6080/websockify?token={SCREEN}"
    assert seen["additional_headers"] == {"Authorization": "Bearer <DESKTOP_OPERATOR_TOKEN>"}


@pytest.mark.parametrize("query", ["", "?token=routines--alpha", f"?token={SCREEN}x"])
def test_the_socket_refuses_a_token_of_the_wrong_shape_before_dialling(client, monkeypatch,
                                                                       query):
    seen = _fake_upstream(monkeypatch)
    client.post("/api/desktop-view/pass", headers=AUTH)
    with pytest.raises(WebSocketDisconnect) as refused, \
            client.websocket_connect(f"/desktop-view/websockify{query}"):
        pass
    assert refused.value.code == 1008
    assert seen == {}, "the relay dialled upstream for a token it should have refused"


def test_the_socket_needs_the_desktop_pass_not_the_browsers(client, monkeypatch):
    seen = _fake_upstream(monkeypatch)
    browser = client.post("/api/browser-view/pass", headers=AUTH).cookies["rsched_browser_view"]
    bare = TestClient(client.app)
    bare.cookies.set("rsched_desktop_view", browser)
    with pytest.raises(WebSocketDisconnect) as refused, \
            bare.websocket_connect(f"/desktop-view/websockify?token={SCREEN}"):
        pass
    assert refused.value.code == 1008
    assert seen == {}
