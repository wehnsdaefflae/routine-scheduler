"""Serve a noVNC screen from the CONSOLE's own origin (F527) — the shared browser's, and every
routine's agent desktop.

0.359.0 put `browser_view_url` straight into an iframe. On the LAN over http that works; over
`https://<host>` — which is how the operator actually reaches this console — both the page and
the right-rail preview came out blank, and nothing in the UI said why.

The cause, measured with `gu mixed-content-probe` rather than assumed: the http frame itself
loads (mixed content is only a *warning* for a bare-IP host, since Chromium never upgrades an
IP), but noVNC then opens `ws://<host>:6080/websockify`, and a page served over https may not
open an insecure websocket. No framebuffer ever arrives, so the screen stays empty.

That cannot be fixed by a different URL: the socket's permitted schemes follow the EMBEDDING
page's origin. So the console becomes the origin. `<prefix>/<path>` relays the noVNC assets and
`<prefix>/websockify` relays the VNC stream, both under the console's own TLS and its own auth
— the browser sees one origin, upgrades to `wss://` by itself, and the noVNC port needs no
certificate and no second published hostname.

Two screens go through it, and they are ONE implementation with two `Screen` rows rather than
two relays: everything that made the first one safe — the traversal refusal, the dropped
hop-by-hop headers, the refused redirect, the path-scoped pass — is a property every relayed
screen needs, and a copy is where one of them would quietly go missing.

- `BROWSER` — the shared signed-in browser (`/browser-view`, bearer `BROWSER_CDP_TOKEN`).
- `DESKTOP` — the agent desktops (`/desktop-view`, bearer `DESKTOP_OPERATOR_TOKEN`). One
  websockify serves EVERY desktop and picks the screen by the socket's `?token=` — the random
  token the broker's operator-only `/fleet` reports per desktop — so that one query is
  forwarded upstream, and only once its shape (32 hex) is proved.

Deliberately NOT done here: terminating TLS on the noVNC port, or asking the operator to
publish it properly. Both put a second public surface (and a second certificate) in front of a
screen that can read every logged-in account, for no gain over relaying it behind the auth the
console already enforces.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

log = logging.getLogger("rsched.web.screen_proxy")

#: Hop-by-hop headers a relay must not forward (RFC 9110 §7.6.1): they describe THIS
#: connection, not the message, and passing them on is how a proxied response ends up
#: double-encoded or with a connection the client never negotiated.
DROP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-encoding",
    "content-length", "host",
})

#: A desktop's screen token: `secrets.token_hex(16)` in the broker. Checked before it is put
#: into an upstream URL, so the browser can choose WHICH screen and nothing else about the hop.
SCREEN_TOKEN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Screen:
    """One relayed noVNC screen: where it is mounted, what configures it, what opens it."""

    key: str            # "browser" / "desktop": the pass store's key and the log's word
    prefix: str         # the mount point; also spelled in static/components/screen.js
    config_field: str   # the ServerConfig field naming the upstream noVNC PAGE
    secret_key: str     # the central-store key whose value the upstream's bearer door checks
    cookie: str         # the pass cookie's name (api_screen_view)
    label: str          # every refusal's noun: "no browser screen is configured"
    #: True when one upstream serves MANY screens and the websocket's `?token=` picks one.
    takes_token: bool = False

    def configured(self, server) -> str:
        """The configured noVNC page address, '' when this screen is not published."""
        return (getattr(server, self.config_field, "") or "").strip()

    def owns(self, path: str) -> bool:
        """True for the mount point itself and anything under it — segment-wise, never a bare
        startswith, so `/browser-view-x` is not this screen's.
        """
        return path == self.prefix or path.startswith(self.prefix + "/")

    @property
    def pass_route(self) -> str:
        """Where the pass is minted, under /api: `/api/browser-view/pass`."""
        return self.prefix + "/pass"

    def upstream_for(self, server, path: str) -> str | None:
        """The absolute upstream URL for a relayed asset, or None when nothing is configured.

        `path` is the part after the mount prefix; "" means the noVNC page itself. Raises
        ValueError for a path that would climb out of the upstream's own tree.
        """
        configured = self.configured(server)
        base = _base(configured, self.config_field)
        if base is None:
            return None
        scheme, netloc, dirpath = base
        suffix = _safe_suffix(path)
        if not suffix:
            # the page itself: exactly what was configured, query and all
            page = urlsplit(configured)
            return urlunsplit((page.scheme, page.netloc, page.path or "/", page.query, ""))
        return urlunsplit((scheme, netloc, dirpath + suffix, "", ""))

    def ws_upstream_for(self, server, token: str = "") -> str | None:
        """The upstream websockify URL, with the ws scheme matching the upstream's own.

        Plain `ws://` upstream is correct and is not the leak it looks like: the TLS the user
        needs is between the BROWSER and this console, which the console already terminates.
        The upstream hop is loopback or a private network, to a port that speaks no TLS at all.

        A screen that `takes_token` REQUIRES one of the right shape (ValueError otherwise): it
        is the only part of the upstream URL the browser chooses, so it is the part proved.
        Any other screen forwards no query at all.
        """
        base = _base(self.configured(server), self.config_field)
        if base is None:
            return None
        scheme, netloc, dirpath = base
        query = ""
        if self.takes_token:
            if not SCREEN_TOKEN.fullmatch(token or ""):
                raise ValueError(f"the {self.label} socket needs ?token=<32 hex> — the token "
                                 "/api/desktops reports for the desktop to open")
            query = urlencode({"token": token})
        ws_scheme = "wss" if scheme == "https" else "ws"
        return urlunsplit((ws_scheme, netloc, dirpath + "websockify", query, ""))


BROWSER = Screen(key="browser", prefix="/browser-view", config_field="browser_view_url",
                 secret_key="BROWSER_CDP_TOKEN",  # noqa: S106 — a store KEY, not a value
                 cookie="rsched_browser_view", label="browser screen")
DESKTOP = Screen(key="desktop", prefix="/desktop-view", config_field="desktop_view_url",
                 secret_key="DESKTOP_OPERATOR_TOKEN",  # noqa: S106 — a store KEY, not a value
                 cookie="rsched_desktop_view", label="desktop screen", takes_token=True)
SCREENS: tuple[Screen, ...] = (BROWSER, DESKTOP)


def screen_for_path(path: str) -> Screen | None:
    """The relayed screen a request path belongs to, if any — what `require_auth` asks before
    it looks for that screen's pass.
    """
    return next((s for s in SCREENS if s.owns(path)), None)


def _base(configured: str, field: str) -> tuple[str, str, str] | None:
    """(scheme, netloc, dir-path) of the configured noVNC page, or None if unset."""
    if not configured:
        return None
    parts = urlsplit(configured)
    if not parts.scheme or not parts.netloc:
        log.warning("%s %r has no scheme/host — ignoring it", field, configured)
        return None
    # the configured value names the PAGE (…/vnc.html); assets are siblings of it. A value
    # that is a bare origin has no filename to strip, so its directory is the root.
    path = parts.path or "/"
    dirpath = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return parts.scheme, parts.netloc, dirpath


def _safe_suffix(path: str) -> str:
    """The relative path, refused if it could escape the upstream's mount.

    The browser chooses this path, so traversal is rejected HERE rather than trusted to the
    upstream's hygiene — websockify is a development server and not a hardened one. It is
    judged fully DECODED: the router decoded it once, and the relay forwards what is left as
    it stands, so a dot still encoded here (`%2e%2e`, or `%252e%252e` before the router's
    pass) is a dot to the server that decodes the rest. Any `..` at all is refused — no asset
    noVNC serves has one in its name — which makes the separator spelling (a slash, a
    backslash, their encodings) irrelevant.
    """
    p = (path or "").strip()
    if p.startswith("/"):
        raise ValueError(f"absolute path is not relayable: {path!r}")
    decoded = p
    while (step := unquote(decoded)) != decoded:     # each pass shortens it: terminates
        decoded = step
    if ".." in decoded:
        raise ValueError(f"path escapes the upstream: {path!r}")
    return p


def relay_headers(headers) -> dict[str, str]:
    """Upstream headers worth returning to the browser (hop-by-hop dropped)."""
    return {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS}


def auth_headers(secret_key: str) -> dict[str, str]:
    """The bearer a screen's upstream door requires, read from the central secrets store.

    Neither CDP nor VNC authenticates anything of its own, so each sidecar's ports sit behind
    a token door (deploy/browser-auth-proxy.py) and this relay is one of its callers — with
    the browser's `BROWSER_CDP_TOKEN`, or the desktops' `DESKTOP_OPERATOR_TOKEN`, a second
    secret no routine is granted, so a run holding the desktops' own token opens no screen.
    Returns an empty mapping when the store has no such key, so a misconfigured instance gets
    the door's own 401 — which names what is missing — rather than a confusing failure here.
    """
    from ..secrets import load_secrets
    try:
        token = (load_secrets().get(secret_key) or "").strip()
    except OSError:
        return {}
    return {"Authorization": f"Bearer {token}"} if token else {}
