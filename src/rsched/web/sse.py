"""Server-sent events: the run tail (transcript file + status watcher) and the global bus.

The transcript tailer is offset-based via engine.transcript.read_events, which holds back
partial lines — a mid-write read never yields broken JSON.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.responses import StreamingResponse

from ..engine.transcript import read_events
from ..paths import read_json

TERMINAL_STATES = ("finished", "failed", "aborted")
POLL_S = 0.4


def format_sse(data: dict, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return prefix + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def run_stream(run_dir: Path, start_offset: int = 0):
    """Yield transcript events (event: transcript) interleaved with state changes
    (event: state); ends shortly after the run reaches a terminal state."""
    transcript = run_dir / "transcript.jsonl"
    offset = start_offset
    last_state = None
    terminal_grace = 3  # extra polls after terminal state to drain the file tail
    while True:
        events, offset = read_events(transcript, offset)
        for ev in events:
            yield format_sse(ev, "transcript")
        st = read_json(run_dir / "status.json")
        state = st.get("state") if isinstance(st, dict) else None
        if state and state != last_state:
            last_state = state
            yield format_sse({"state": state, "question": st.get("question"),
                              "turn": st.get("turn"), "usage": st.get("usage"),
                              "model": st.get("model")}, "state")
        if state in TERMINAL_STATES:
            terminal_grace -= 1
            if terminal_grace <= 0:
                yield format_sse({"state": state}, "end")
                return
        await asyncio.sleep(POLL_S)


async def bus_stream(bus):
    """The global event bus as SSE (dashboard badges), with keepalive comments."""
    with bus.subscribe() as q:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield format_sse(event, "bus")
