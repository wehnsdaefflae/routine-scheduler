"""The same-origin relay for the shared browser's noVNC screen (F527).

Two routes, both under `browser_proxy.PREFIX`:

  GET  /browser-view/{path}        — the noVNC page and its assets
  WS   /browser-view/websockify    — the VNC stream itself

Why a relay at all is argued in `browser_proxy`: a console served over https cannot embed a
page that opens `ws://`, so the screen stayed blank exactly where the operator uses it. Making
the console the origin means the browser upgrades to `wss://` on its own, under the TLS and the
auth the console already has.

The websocket carries a TICKET rather than a bearer header, for the same reason the SSE streams
do (`app._is_sse_path`): the browser's WebSocket API cannot send headers. Same 60 s TTL, same
single-purpose scope — a leaked ticket reaches this screen and nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import browser_proxy

log = logging.getLogger("rsched.web.browser_view")

#: The screen's own credential (F530). An iframe cannot send an Authorization header, and —
#: the part that actually broke — noVNC builds its OWN asset URLs (`app/ui.js`,
#: `app/styles/base.css`, the images), so no query parameter the embedding page chooses
#: reaches them either. A cookie is the one credential a browser attaches to every
#: sub-resource of a frame without the page being involved at all.
SCREEN_COOKIE = "rsched_browser_view"

#: How long the pass lives. An SSE ticket is 60s, which is right for a stream the console
#: reopens on its own and catastrophic here: a watched browser session lasts minutes to
#: hours, and a credential that expires mid-session makes the screen fail LATER — which
#: reads as flakiness rather than as a bug, and is harder to diagnose than an instant refusal.
PASS_TTL_S = 12 * 3600

#: The GET half. Included WITH the console's auth dependency, so a relayed asset is exactly
#: as protected as any other console route (noVNC's own asset fetches carry no header, so
#: require_auth's `_is_browser_view_path` ticket branch is what lets them through).
router = APIRouter(tags=["browser-view"])

#: The WEBSOCKET half, deliberately a SEPARATE router. A FastAPI HTTP dependency cannot be
#: applied to a websocket route — it fails at connect time with "require_auth() missing 1
#: required positional argument: 'request'", because a websocket scope has no Request. So
#: this router is included without dependencies and the endpoint checks the ticket itself.
#: Keeping the two apart is what stops that fix from silently unauthenticating the GETs.
ws_router = APIRouter(tags=["browser-view"])

#: The pass-minting route. It lives on the /api surface WITH the console's bearer dependency —
#: handing out the screen's credential must be an authenticated act, even though the credential
#: it hands out is deliberately weaker than a token (one path, fixed lifetime, HttpOnly).
pass_router = APIRouter(tags=["browser-view"])

#: Upstream read timeout for an asset. noVNC's files are small and local; a slow upstream
#: here means websockify is wedged, and failing fast beats holding a console worker.
ASSET_TIMEOUT_S = 15.0


def _issue_pass(request: Request) -> str:
    """Mint a screen pass and remember it on the app, like the SSE tickets beside it."""
    import secrets
    import time

    passes = request.app.state.browser_view_passes
    now = time.monotonic()
    for token, expiry in list(passes.items()):   # purge on issue; the set is tiny
        if expiry < now:
            passes.pop(token, None)
    token = secrets.token_urlsafe(32)
    passes[token] = now + PASS_TTL_S
    return token


def pass_is_valid(app, token: str) -> bool:
    """True while `token` is an unexpired screen pass. Read by `require_auth`, which is the
    only gate the frame's requests pass through.
    """
    import time

    if not token:
        return False
    expiry = getattr(app.state, "browser_view_passes", {}).get(token)
    return expiry is not None and expiry >= time.monotonic()


@pass_router.post("/browser-view/pass")
def grant_pass(request: Request) -> JSONResponse:
    """Mint the screen's pass, for a caller who already holds the console's token.

    This route sits on the authenticated API router, so minting is an operator act; the
    COOKIE it returns is what the frame's own requests carry afterwards. Scoped to the
    relay's path (`/browser-view`) so the browser never attaches it to anything else, and
    marked HttpOnly + SameSite=Strict: it authenticates one embedded screen, not the console.

    `secure` reads the FORWARDED scheme as well as the request's own: behind a
    TLS-terminating proxy (tailscale serve --https, a reverse proxy) the app sees `http`, so
    the flag came off exactly where the connection the user makes is the encrypted one.
    """
    if not request.app.state.server.browser_view_url:
        raise HTTPException(503, "no browser screen is configured — set browser_view_url "
                                 "in Settings → server process")
    token = _issue_pass(request)
    reply = JSONResponse({"ok": True, "ttl": PASS_TTL_S})
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    reply.set_cookie(SCREEN_COOKIE, token, max_age=PASS_TTL_S, path=browser_proxy.PREFIX,
                     httponly=True, samesite="strict",
                     secure=request.url.scheme == "https" or forwarded == "https")
    return reply


@router.get(browser_proxy.PREFIX + "/{path:path}")
async def relay_asset(request: Request, path: str = "") -> Response:
    """Relay one noVNC asset from the configured upstream, same-origin."""
    server = request.app.state.server
    try:
        url = browser_proxy.upstream_for(server, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if url is None:
        raise HTTPException(
            503, "no browser screen is configured — set browser_view_url in Settings")
    # the page's own query (resize=scale, autoconnect…) comes from the configured URL; a
    # caller's query is forwarded so the dock can ask for view_only
    query = str(request.url.query or "")
    if query and "?" not in url:
        url = f"{url}?{query}"
    # NO redirect following. The FIRST hop cannot be aimed (`upstream_for` takes scheme+netloc
    # from the configured URL alone and `_safe_suffix` rejects absolute paths and both encoded
    # and decoded `..`), but a followed redirect is a hop the UPSTREAM chooses — and the
    # daemon's network position reaches cliproxy, the chrome CDP port, the LAN and the
    # internet, so a hostile or spoofed noVNC had an SSRF proxy here answering to the
    # browser-view pass alone. The upstream is a raw websockify serving static assets and
    # emits no redirects, so refusing one costs nothing and stays legible — loud, with the
    # target named, rather than a body fetched from somewhere nobody chose.
    try:
        async with httpx.AsyncClient(timeout=ASSET_TIMEOUT_S, follow_redirects=False) as client:
            up = await client.get(url, headers=browser_proxy.auth_headers())
    except httpx.HTTPError as exc:
        # loud, and with the upstream named: a blank frame with no explanation is the whole
        # defect this module exists to fix
        log.warning("browser-view: upstream %s unreachable: %s", url, exc)
        raise HTTPException(502, f"the browser screen upstream did not answer: {exc}") from exc
    if up.is_redirect:
        target = up.headers.get("location", "")
        log.warning("browser-view: upstream %s redirected to %s — refused", url, target)
        raise HTTPException(502, "the browser screen upstream redirected this asset to "
                                 f"{target!r}; the relay does not follow redirects")
    return Response(content=up.content, status_code=up.status_code,
                    headers=browser_proxy.relay_headers(up.headers),
                    media_type=up.headers.get("content-type"))


@ws_router.websocket(browser_proxy.PREFIX + "/websockify")
async def relay_socket(ws: WebSocket) -> None:
    """Relay the VNC stream between the browser and websockify.

    Binary both ways, with each direction cancelled when the other ends — a half-closed
    relay would otherwise hold a worker and the upstream socket open until a timeout.
    """
    server = ws.app.state.server
    upstream = browser_proxy.ws_upstream_for(server)
    if upstream is None:
        await ws.close(code=1011, reason="no browser screen configured")
        return
    if server.token and not pass_is_valid(ws.app, ws.cookies.get(SCREEN_COOKIE) or ""):
        # The handshake carries the frame's cookies, so the same pass that admits the page
        # admits its socket — one credential for the whole screen, and no ticket to expire
        # 60 seconds into a session someone is watching.
        await ws.close(code=1008, reason="missing or invalid browser-view pass")
        return

    try:
        import websockets
    except ImportError:  # pragma: no cover - uvicorn[standard] ships it
        log.error("browser-view: the websockets package is unavailable")
        await ws.close(code=1011, reason="websocket relay unavailable")
        return

    await ws.accept(subprotocol="binary")
    try:
        # `binary` is what noVNC negotiates; websockets types it as a NewType over str, so
        # the cast is the library's own vocabulary rather than a silenced check.
        binary = websockets.Subprotocol("binary")
        async with websockets.connect(upstream, subprotocols=[binary],
                                      additional_headers=browser_proxy.auth_headers(),
                                      max_size=None, open_timeout=10) as up:
            async def to_upstream() -> None:
                while True:
                    data = await ws.receive_bytes()
                    await up.send(data)

            async def to_browser() -> None:
                async for message in up:
                    if isinstance(message, str):
                        message = message.encode()
                    await ws.send_bytes(message)

            _done, pending = await asyncio.wait(
                [asyncio.create_task(to_upstream()), asyncio.create_task(to_browser())],
                return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                # Await each cancelled task and swallow its exception. Without this, the
                # loser of the race — normally `to_upstream` blocked in receive_bytes when
                # the user navigates away — is garbage-collected still holding an unretrieved
                # WebSocketDisconnect, and asyncio prints "Task exception was never
                # retrieved" with a traceback on EVERY page close. That is a clean shutdown
                # reported as a fault, which is exactly the noise that trains a reader to
                # ignore this log.
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect,
                                         Exception):
                    await task
            # the winner too: whichever side ended first may also carry a disconnect
            for task in _done:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect,
                                         Exception):
                    await task
    except WebSocketDisconnect:
        pass                          # the ordinary end: the user navigated away
    except Exception as exc:          # a relay must REPORT a failure, never vanish quietly
        log.warning("browser-view: relay to %s ended: %s", upstream, exc)
        with contextlib.suppress(RuntimeError):
            await ws.close(code=1011, reason="upstream relay failed")
