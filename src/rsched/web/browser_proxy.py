"""Serve the shared browser's noVNC screen from the CONSOLE's own origin (F527).

0.359.0 put `browser_view_url` straight into an iframe. On the LAN over http that works; over
`https://<host>` — which is how the operator actually reaches this console — both the page and
the right-rail preview came out blank, and nothing in the UI said why.

The cause, measured with `gu mixed-content-probe` rather than assumed: the http frame itself
loads (mixed content is only a *warning* for a bare-IP host, since Chromium never upgrades an
IP), but noVNC then opens `ws://<host>:6080/websockify`, and a page served over https may not
open an insecure websocket. No framebuffer ever arrives, so the screen stays empty.

That cannot be fixed by a different URL: the socket's permitted schemes follow the EMBEDDING
page's origin. So the console becomes the origin. `/browser-view/<path>` relays the noVNC
assets and `/browser-view/websockify` relays the VNC stream, both under the console's own TLS
and its own auth — the browser sees one origin, upgrades to `wss://` by itself, and the noVNC
port needs no certificate and no second published hostname.

Deliberately NOT done here: terminating TLS on 6080, or asking the operator to publish it
properly. Both put a second public surface (and a second certificate) in front of a browser
session that can read every logged-in account, for no gain over relaying it behind the auth
the console already enforces.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("rsched.web.browser_proxy")

#: Mount point. Also hardcoded in static/views/browser.js and components/browserdock.js —
#: they are the only callers, and a same-origin path is not worth a config field.
PREFIX = "/browser-view"

#: Hop-by-hop headers a relay must not forward (RFC 9110 §7.6.1): they describe THIS
#: connection, not the message, and passing them on is how a proxied response ends up
#: double-encoded or with a connection the client never negotiated.
DROP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-encoding",
    "content-length", "host",
})


def _base(configured: str) -> tuple[str, str, str] | None:
    """(scheme, netloc, dir-path) of the configured noVNC page, or None if unset."""
    raw = (configured or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        log.warning("browser_view_url %r has no scheme/host — ignoring it", raw)
        return None
    # the configured value names the PAGE (…/vnc.html); assets are siblings of it. A value
    # that is a bare origin has no filename to strip, so its directory is the root.
    path = parts.path or "/"
    dirpath = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return parts.scheme, parts.netloc, dirpath


def _safe_suffix(path: str) -> str:
    """The relative path, refused if it could escape the upstream's mount.

    The browser chooses this path, so traversal is rejected HERE rather than trusted to the
    upstream's hygiene — websockify is a development server and not a hardened one.
    """
    p = (path or "").strip()
    if p.startswith("/"):
        raise ValueError(f"absolute path is not relayable: {path!r}")
    # catch a traversal that arrived still-encoded as well as a decoded one
    lowered = p.lower().replace("%2f", "/").replace("%5c", "\\")
    if ".." in lowered.split("/") or ".." in lowered.replace("\\", "/").split("/"):
        raise ValueError(f"path escapes the upstream: {path!r}")
    if ".." in lowered:
        raise ValueError(f"path escapes the upstream: {path!r}")
    return p


def upstream_for(server, path: str) -> str | None:
    """The absolute upstream URL for a relayed asset, or None when nothing is configured.

    `path` is the part after the mount prefix; "" means the noVNC page itself. Raises
    ValueError for a path that would climb out of the upstream's own tree.
    """
    base = _base(getattr(server, "browser_view_url", "") or "")
    if base is None:
        return None
    scheme, netloc, dirpath = base
    suffix = _safe_suffix(path)
    if not suffix:
        # the page itself: exactly what was configured, query and all
        configured = urlsplit((server.browser_view_url or "").strip())
        return urlunsplit((configured.scheme, configured.netloc,
                           configured.path or "/", configured.query, ""))
    return urlunsplit((scheme, netloc, dirpath + suffix, "", ""))


def ws_upstream_for(server) -> str | None:
    """The upstream websockify URL, with the ws scheme matching the upstream's own.

    Plain `ws://` upstream is correct and is not the leak it looks like: the TLS the user
    needs is between the BROWSER and this console, which the console already terminates.
    The upstream hop is loopback-or-tailnet to a port that speaks no TLS at all.
    """
    base = _base(getattr(server, "browser_view_url", "") or "")
    if base is None:
        return None
    scheme, netloc, dirpath = base
    ws_scheme = "wss" if scheme == "https" else "ws"
    return urlunsplit((ws_scheme, netloc, dirpath + "websockify", "", ""))


def relay_headers(headers) -> dict[str, str]:
    """Upstream headers worth returning to the browser (hop-by-hop dropped)."""
    return {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS}
