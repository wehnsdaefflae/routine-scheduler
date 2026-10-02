"""Live streams over WebSockets: the run tail (transcript file + status watcher) and the global bus.

Why WebSockets (F606): over plain HTTP/1.1 a browser keeps about six connections per origin, and
an EventSource holds one for its whole life. The console holds the global bus plus a tail per
live view, so a fourth tab found no socket left and never loaded. Browsers count WebSockets
against a separate, far larger limit, so a stream no longer competes with the page's own fetches
and the console needs neither HTTP/2 nor TLS for it.

The generators yield `{"event", "data"}` items and know nothing of the transport; `serve`
admits a socket, then `pump` sends each item as one JSON text frame, a `ping` frame after
KEEPALIVE_S of silence (the client treats three missed pings as a dead socket), and closes 1000
when the generator ends. The transcript tailer is offset-based via engine.transcript.read_events,
which holds back partial lines — a mid-write read never yields broken JSON.

A socket is admitted on a STREAM TICKET in its query string, because the WebSocket API cannot
send an Authorization header and the bearer token in a URL would leak into logs and history.
A ticket is minted over the authed channel (`POST /api/stream-ticket`), lives STREAM_TICKET_TTL_S,
opens exactly ONE socket and authenticates nothing else — no HTTP route reads it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from fastapi import WebSocket

from ..engine.transcript import read_events
from ..paths import read_json
from ..registry import TERMINAL_STATES

POLL_S = 0.4
KEEPALIVE_S = 15.0
STREAM_TICKET_TTL_S = 60

#: Close codes. 1008 is the protocol's own "policy violation"; the 4xxx range is the
#: application's, and 4404 mirrors the HTTP status the console already reads as "gone".
CLOSE_UNAUTHORIZED = 1008
CLOSE_GONE = 4404


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": data}


async def run_stream(run_dir: Path, start_offset: int = 0) -> AsyncGenerator[dict, None]:
    """Yield transcript events (event: transcript) interleaved with state changes
    (event: state); ends shortly after the run reaches a terminal state.
    """
    transcript = run_dir / "transcript.jsonl"
    offset = start_offset
    last_state = None
    terminal_grace = 3  # extra polls after terminal state to drain the file tail
    while True:
        # disk reads happen off the loop — a stream generator runs ON it, and a slow
        # (networked) filesystem would otherwise stall every other request per poll
        events, offset = await asyncio.to_thread(read_events, transcript, offset)
        for ev in events:
            yield _event("transcript", ev)
        raw = await asyncio.to_thread(read_json, run_dir / "status.json")
        st: dict = raw if isinstance(raw, dict) else {}
        state = st.get("state")
        phase = st.get("phase")
        question = st.get("question")
        # A changed pending question must ride its own state event even when state+phase are
        # unchanged (F93: the run-page question form only re-renders on a `state` event, so a
        # question that changes without a state/phase transition would never reach an open run
        # page). phase transitions ride the same event — the state-graph diagram updates on them.
        qid = question.get("qid") if isinstance(question, dict) else None
        if state and (state, phase, qid) != last_state:
            last_state = (state, phase, qid)
            yield _event("state", {"state": state, "phase": phase,
                                   "question": question,
                                   "turn": st.get("turn"), "usage": st.get("usage"),
                                   "model": st.get("model"), "updated": st.get("updated"),
                                   "deliberation": st.get("deliberation")})
        if state in TERMINAL_STATES:
            terminal_grace -= 1
            if terminal_grace <= 0:
                yield _event("end", {"state": state})
                return
        await asyncio.sleep(POLL_S)


async def traced_run_stream(run_dir: Path, start_offset: int, server) -> AsyncGenerator[dict, None]:
    """run_stream wrapped in close-cause telemetry (F175): when the stream closes — the run
    ended (`end`), the transport was torn down under us (`cancelled` mid-await, `closed` on
    generator teardown), or the generator itself failed (`error`) — one `stream-close` line
    lands in the ui-trace day file with the stream's lifetime and events carried. Client
    `reconnect` traces record how a drop LOOKED from the browser; this records what the
    server SAW, so an audit can tell a server-side fault from a transport kill: a drop the
    client reports while the server logs `cancelled` originated outside the app.
    """
    from .api_traces import record_server_trace

    started = time.monotonic()
    carried = 0
    cause = "end"
    try:
        async for ev in run_stream(run_dir, start_offset):
            if ev.get("event") == "transcript":
                carried += 1
            yield ev
    except asyncio.CancelledError:
        cause = "cancelled"
        raise
    except GeneratorExit:
        cause = "closed"
        raise
    except Exception:
        cause = "error"
        raise
    finally:
        run_id = f"{run_dir.parent.parent.name}:{run_dir.name}"
        record_server_trace(server, kind="stream-close", target=run_id,
                            detail=f"{cause} after {round(time.monotonic() - started)}s, "
                                   f"{carried} events")


async def bus_stream(bus) -> AsyncGenerator[dict, None]:
    """The global event bus (dashboard badges, run toasts, every view's live refresh)."""
    with bus.subscribe() as q:
        while True:
            yield _event("bus", await q.get())


def admits(ws: WebSocket) -> bool:
    """Spend the socket's stream ticket. An instance with auth disabled admits everyone, as
    `require_auth` does; otherwise the ticket must be one this process minted, unexpired, and
    unused — it is removed here, so a URL that leaked opens nothing a second time.
    """
    if not ws.app.state.server.token:
        return True
    ticket = ws.query_params.get("ticket") or ""
    expiry = ws.app.state.stream_tickets.pop(ticket, None) if ticket else None
    return expiry is not None and expiry >= time.monotonic()


async def serve(ws: WebSocket, open_stream: Callable[[], AsyncGenerator[dict, None]]) -> None:
    """Admit the socket, then pump the stream `open_stream()` returns into it.

    Admission comes FIRST, so an unauthenticated caller learns nothing — not even whether
    the run it named exists. `open_stream` may raise LookupError for a resource that is not
    there; the socket is then closed CLOSE_GONE. A refusal is sent AFTER accepting, because
    a browser reports every rejected handshake as the same bare 1006 — accepted and then
    closed, the code and reason reach the console and its traces.
    """
    await ws.accept()
    if not admits(ws):
        await ws.close(code=CLOSE_UNAUTHORIZED, reason="missing or invalid stream ticket")
        return
    try:
        stream = open_stream()
    except LookupError as exc:
        await ws.close(code=CLOSE_GONE, reason=str(exc)[:120])
        return
    await pump(ws, stream)


async def pump(ws: WebSocket, stream: AsyncGenerator[dict, None]) -> None:
    """Send each item as one JSON text frame until the stream ends or the peer goes away.

    Three tasks share the socket: `forward` drains the generator, `keepalive` sends a ping
    after each KEEPALIVE_S of silence, and `peer` waits for the disconnect (the console sends
    nothing, so a receive only ever returns when the browser closes). Whichever finishes
    first ends the other two — cancelling `forward` cancels the generator mid-await, which is
    the `cancelled` close cause the run tail's trace records. Only a stream that ENDED closes
    the socket normally; one that failed closes 1011, and a vanished peer needs no close.
    """
    lock = asyncio.Lock()
    last_sent = time.monotonic()

    async def send(frame: dict) -> None:
        nonlocal last_sent
        async with lock:
            await ws.send_text(json.dumps(frame, ensure_ascii=False))
            last_sent = time.monotonic()

    async def forward() -> None:
        # Closed explicitly on EVERY exit: a send that fails leaves the generator suspended at
        # its yield, and only aclose() runs its cleanup now rather than at garbage collection —
        # the bus subscription and the run tail's close trace both live in that cleanup.
        try:
            async for item in stream:
                await send(item)
        finally:
            await stream.aclose()

    async def keepalive() -> None:
        while True:
            await asyncio.sleep(max(0.0, last_sent + KEEPALIVE_S - time.monotonic()))
            if time.monotonic() - last_sent >= KEEPALIVE_S:
                await send({"event": "ping"})

    async def peer() -> None:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass

    forwarding = asyncio.create_task(forward())
    others = [asyncio.create_task(keepalive()), asyncio.create_task(peer())]
    try:
        await asyncio.wait([forwarding, *others], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (forwarding, *others):
            task.cancel()
        await asyncio.gather(forwarding, *others, return_exceptions=True)
    if forwarding.cancelled():
        return                              # the peer left, or the server is shutting down
    code = 1000 if forwarding.exception() is None else 1011
    with contextlib.suppress(Exception):    # the peer may have gone in the same instant
        await ws.close(code=code)
