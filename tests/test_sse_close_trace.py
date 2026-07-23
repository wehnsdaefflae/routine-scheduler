"""F175 instrumentation: every run-SSE stream close appends an `sse-close` ui-trace line
with its cause — `end` for a natural terminal close, `cancelled`/`closed` when the
transport is torn down mid-stream — plus lifetime and events carried, so the client's
`reconnect` traces can be told apart from server-side faults.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from rsched.web.sse import traced_run_stream


def _mk_run(tmp_path: Path, state: str) -> tuple[Path, types.SimpleNamespace]:
    run_dir = tmp_path / "routines" / "uir" / "runs" / "20260723-140000"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        '{"type": "header", "run_id": "uir:20260723-140000"}\n', encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({"state": state}), encoding="utf-8")
    return run_dir, types.SimpleNamespace(routines_home=tmp_path / "routines")


def _sse_close_lines(server) -> list[dict]:
    d = server.routines_home / ".ui-traces"
    lines = [json.loads(line) for p in sorted(d.glob("*.jsonl"))
             for line in p.read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln["kind"] == "sse-close"]


def test_terminal_close_records_end(tmp_path):
    run_dir, server = _mk_run(tmp_path, "finished")

    async def consume():
        return [ev async for ev in traced_run_stream(run_dir, 0, server)]

    events = asyncio.run(consume())
    assert events[-1]["event"] == "end"

    (line,) = _sse_close_lines(server)
    assert line["target"] == "uir:20260723-140000"
    assert line["view"] == "server"
    assert line["detail"].startswith("end after")
    assert "1 events" in line["detail"]


def test_transport_cancel_records_cancelled(tmp_path):
    run_dir, server = _mk_run(tmp_path, "running")

    async def scenario():
        gen = traced_run_stream(run_dir, 0, server)
        first = await gen.__anext__()    # the stream is live and carrying events
        assert first["event"] == "transcript"
        second = await gen.__anext__()   # the state event a fresh stream always emits
        assert second["event"] == "state"
        nxt = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)        # now parked in the poll sleep, mid-stream
        nxt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await nxt
        await gen.aclose()

    asyncio.run(scenario())

    (line,) = _sse_close_lines(server)
    assert line["target"] == "uir:20260723-140000"
    assert line["detail"].startswith(("cancelled after", "closed after"))
    assert "1 events" in line["detail"]
