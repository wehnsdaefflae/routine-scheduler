"""Tail an engine run's LLM sidecar into the daemon.

Engine subprocesses can't reach the in-process bus, so each appends per-call lifecycle records
to `runs/<ts>/llm-tasks.jsonl` (via endpoints.instrument.FileSink). This coroutine tails that
file with the transcript's partial-line-safe reader and hands each new record to a callback
(which stamps run/process attribution and forwards it to the TaskCenter). The Runner runs one
per active run; the wizard runs one for its clarify subprocess.

Cancel the task to stop it — a final drain in the `finally` catches records the engine wrote
just before exiting, so the last calls land before the run's process is closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from ..engine.transcript import read_events

POLL_S = 0.5


async def tail_llm_sidecar(run_dir: Path, on_record: Callable[[dict], None]) -> None:
    path = Path(run_dir) / "llm-tasks.jsonl"
    offset = 0
    try:
        while True:
            events, offset = await asyncio.to_thread(read_events, path, offset)
            for rec in events:
                on_record(rec)
            await asyncio.sleep(POLL_S)
    finally:
        # final drain (runs on cancellation): the engine may have written finished/failed
        # records between the last poll and its exit.
        try:
            events, _ = read_events(path, offset)
            for rec in events:
                on_record(rec)
        except Exception:
            pass
