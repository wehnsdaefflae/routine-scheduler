"""Server-sent events: the run tail (transcript file + status watcher) and the global bus.

Wire format, headers, and ping keepalives come from sse-starlette's EventSourceResponse —
these generators only yield {"event", "data"} dicts. The transcript tailer is offset-based
via engine.transcript.read_events, which holds back partial lines — a mid-write read never
yields broken JSON.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from ..engine.transcript import read_events
from ..paths import read_json
from ..registry import TERMINAL_STATES

POLL_S = 0.4


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": json.dumps(data, ensure_ascii=False)}


async def run_stream(run_dir: Path, start_offset: int = 0):
    """Yield transcript events (event: transcript) interleaved with state changes
    (event: state); ends shortly after the run reaches a terminal state.
    """
    transcript = run_dir / "transcript.jsonl"
    offset = start_offset
    last_state = None
    terminal_grace = 3  # extra polls after terminal state to drain the file tail
    while True:
        # disk reads happen off the loop — an SSE generator runs ON it, and a slow
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


async def traced_run_stream(run_dir: Path, start_offset: int, server):
    """run_stream wrapped in close-cause telemetry (F175): when the stream closes — the run
    ended (`end`), the transport was torn down under us (`cancelled` mid-await, `closed` on
    generator teardown), or the generator itself failed (`error`) — one `sse-close` line
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
        record_server_trace(server, kind="sse-close", target=run_id,
                            detail=f"{cause} after {round(time.monotonic() - started)}s, "
                                   f"{carried} events")


async def bus_stream(bus):
    """The global event bus as SSE (dashboard badges); EventSourceResponse's periodic ping
    comments keep the connection alive between events.
    """
    with bus.subscribe() as q:
        while True:
            yield _event("bus", await q.get())
