"""F527 — the browser screen must be served from the CONSOLE's own origin.

0.359.0 shipped the screen as a raw `browser_view_url` in an iframe. That works when the
console is reached over http on the LAN, and fails exactly where the operator actually uses
it: over `https://ubuntuserver.taild5768c.ts.net`, both the page and the right-rail preview
came out blank.

Measured with `gu mixed-content-probe` rather than guessed:
  * the http iframe itself LOADS — mixed content is only a *warning* for a bare-IP host,
    because Chromium does not upgrade IP hosts;
  * then noVNC opens `ws://100.77.58.75:6080/websockify` and the browser reports
    "attempted to connect to the insecure WebSocket endpoint ... should be available via
    WSS. Insecure access is deprecated." On a NAMED https origin that socket is refused,
    so the client never gets a framebuffer and the screen stays empty.

A URL the user types can never fix this: the socket's scheme is decided by the embedding
page's origin. So the console proxies the endpoint — `/browser-view/*` for the assets and
`/browser-view/websockify` for the upgrade — and the frame becomes same-origin, inheriting
whatever TLS the console itself is served with. A `wss://` then happens automatically,
with no certificate for the noVNC port and no second hostname to publish.
"""

from __future__ import annotations

import pytest

from rsched.web import browser_proxy


class _Cfg:
    def __init__(self, url: str) -> None:
        self.browser_view_url = url


@pytest.mark.parametrize(
    ("configured", "path", "expected"),
    [
        # the configured value points at the noVNC PAGE; assets hang off its directory
        ("http://10.0.0.5:6080/vnc.html", "", "http://10.0.0.5:6080/vnc.html"),
        ("http://10.0.0.5:6080/vnc.html", "app/styles/base.css",
         "http://10.0.0.5:6080/app/styles/base.css"),
        ("http://10.0.0.5:6080/vnc.html", "websockify", "http://10.0.0.5:6080/websockify"),
        # a bare origin is just as valid a thing to configure
        ("http://10.0.0.5:6080", "vnc.html", "http://10.0.0.5:6080/vnc.html"),
        # a query on the configured URL must not leak into an asset path
        ("http://10.0.0.5:6080/vnc.html?resize=scale", "app/ui.js",
         "http://10.0.0.5:6080/app/ui.js"),
    ],
)
def test_upstream_url_resolves_against_the_configured_page(configured, path, expected):
    assert browser_proxy.upstream_for(_Cfg(configured), path) == expected


def test_an_unset_url_has_no_upstream_at_all():
    """Nothing configured means the proxy refuses rather than inventing a target."""
    assert browser_proxy.upstream_for(_Cfg(""), "vnc.html") is None


@pytest.mark.parametrize("path", [
    "../../etc/passwd",          # climb out of the mount
    "/absolute",                 # escape to the upstream root
    "a/../../b",                 # climb after a legitimate segment
    "..%2f..%2fetc",             # pre-decoded traversal
])
def test_a_path_that_escapes_the_upstream_is_refused(path):
    """The proxy forwards a path the BROWSER chose, so traversal is refused here rather
    than trusted to the upstream's own hygiene."""
    with pytest.raises(ValueError):
        browser_proxy.upstream_for(_Cfg("http://10.0.0.5:6080/vnc.html"), path)


def test_the_websocket_upstream_is_the_ws_scheme_of_the_configured_host():
    """The relay talks plain ws:// to the upstream — the TLS the user needs is between the
    BROWSER and the console, which the console already terminates."""
    assert browser_proxy.ws_upstream_for(_Cfg("http://10.0.0.5:6080/vnc.html")) == \
        "ws://10.0.0.5:6080/websockify"
    assert browser_proxy.ws_upstream_for(_Cfg("https://novnc.example/vnc.html")) == \
        "wss://novnc.example/websockify"


def test_ws_upstream_is_none_when_nothing_is_configured():
    assert browser_proxy.ws_upstream_for(_Cfg("")) is None
