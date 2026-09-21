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
from fastapi.responses import Response

from . import browser_proxy

log = logging.getLogger("rsched.web.browser_view")

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

#: Upstream read timeout for an asset. noVNC's files are small and local; a slow upstream
#: here means websockify is wedged, and failing fast beats holding a console worker.
ASSET_TIMEOUT_S = 15.0


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
    try:
        async with httpx.AsyncClient(timeout=ASSET_TIMEOUT_S, follow_redirects=True) as client:
            up = await client.get(url)
    except httpx.HTTPError as exc:
        # loud, and with the upstream named: a blank frame with no explanation is the whole
        # defect this module exists to fix
        log.warning("browser-view: upstream %s unreachable: %s", url, exc)
        raise HTTPException(502, f"the browser screen upstream did not answer: {exc}") from exc
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
    token = server.token
    if token:
        ticket = ws.query_params.get("ticket") or ""
        expiry = ws.app.state.sse_tickets.get(ticket)
        if not ticket or expiry is None:
            await ws.close(code=1008, reason="missing or invalid ticket")
            return
        import time as _time

        if expiry < _time.monotonic():
            await ws.close(code=1008, reason="ticket expired")
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
