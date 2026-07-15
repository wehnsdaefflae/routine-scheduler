"""Server-sent events: the run tail (transcript file + status watcher) and the global bus.

Wire format, headers, and ping keepalives come from sse-starlette's EventSourceResponse —
these generators only yield {"event", "data"} dicts. The transcript tailer is offset-based
via engine.transcript.read_events, which holds back partial lines — a mid-write read never
yields broken JSON.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..daemon.registry import TERMINAL_STATES
from ..engine.transcript import read_events
from ..paths import read_json

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
        # phase transitions ride the same event — the state-graph diagram updates on them
        if state and (state, phase) != last_state:
            last_state = (state, phase)
            yield _event("state", {"state": state, "phase": phase,
                                   "question": st.get("question"),
                                   "turn": st.get("turn"), "usage": st.get("usage"),
                                   "model": st.get("model"), "updated": st.get("updated")})
        if state in TERMINAL_STATES:
            terminal_grace -= 1
            if terminal_grace <= 0:
                yield _event("end", {"state": state})
                return
        await asyncio.sleep(POLL_S)


async def bus_stream(bus):
    """The global event bus as SSE (dashboard badges); EventSourceResponse's periodic ping
    comments keep the connection alive between events.
    """
    with bus.subscribe() as q:
        while True:
            yield _event("bus", await q.get())
