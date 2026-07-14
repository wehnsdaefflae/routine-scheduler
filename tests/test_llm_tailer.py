"""tail_llm_sidecar: the daemon-side reader that turns an engine run's llm-tasks.jsonl into
TaskCenter records. Uses the transcript's partial-line-safe read_events; drains on cancel."""

from __future__ import annotations

import asyncio
import contextlib

import rsched.daemon.llm_tailer as tailer_mod
from rsched.daemon.llm_tailer import tail_llm_sidecar


def test_tail_drains_records_as_they_are_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(tailer_mod, "POLL_S", 0.02)
    got: list[dict] = []
    path = tmp_path / "llm-tasks.jsonl"

    async def scenario():
        path.write_text('{"id": "a", "phase": "started"}\n')
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, got.append))
        await asyncio.sleep(0.1)                     # a poll picks up "a: started"
        with open(path, "a", encoding="utf-8") as f:  # append while the tailer runs
            f.write('{"id": "a", "phase": "finished"}\n')
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    seen = [(r["id"], r["phase"]) for r in got]
    assert ("a", "started") in seen and ("a", "finished") in seen


def test_final_drain_catches_records_written_before_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(tailer_mod, "POLL_S", 5.0)   # long poll → only the finally-drain can catch it
    got: list[dict] = []
    path = tmp_path / "llm-tasks.jsonl"
    path.write_text("")

    async def scenario():
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, got.append))
        await asyncio.sleep(0.05)                    # first (empty) read done; now in the long sleep
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"id": "z", "phase": "finished"}\n')
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert any(r["id"] == "z" for r in got)


def test_missing_sidecar_never_crashes(tmp_path):
    async def scenario():
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, lambda r: None))
        await asyncio.sleep(0.02)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())  # no file present → clean no-op
