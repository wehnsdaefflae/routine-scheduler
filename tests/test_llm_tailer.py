"""tail_llm_sidecar: the daemon-side reader that turns an engine run's llm-tasks.jsonl into
TaskCenter records. Uses the transcript's partial-line-safe read_events; drains on cancel."""

from __future__ import annotations

import asyncio
import contextlib
import json

import rsched.daemon.llm_tailer as tailer_mod
from rsched.daemon.llm_tailer import tail_llm_sidecar


def test_tail_drains_records_as_they_are_appended(tmp_path, monkeypatch):
    """Each record must arrive while the tail RUNS, before anything cancels it. Asserting
    only after the cancel let the cancel-time drain deliver both, so a poll loop that never
    read a thing past its first pass passed too."""
    monkeypatch.setattr(tailer_mod, "POLL_S", 0.02)
    got: list[dict] = []
    path = tmp_path / "llm-tasks.jsonl"

    async def delivered(n: int) -> list[tuple]:
        for _ in range(100):                         # ~2 s: a hundred polls' worth
            if len(got) >= n:
                break
            await asyncio.sleep(0.02)
        return [(r["id"], r["phase"]) for r in got]

    async def scenario():
        path.write_text('{"id": "a", "phase": "started"}\n')
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, got.append))
        try:
            assert await delivered(1) == [("a", "started")]
            _append(path, '{"id": "a", "phase": "finished"}\n')  # append while the tailer runs
            assert await delivered(2) == [("a", "started"), ("a", "finished")]
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_final_drain_catches_records_written_before_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(tailer_mod, "POLL_S", 5.0)   # long poll → only the finally-drain can catch it
    got: list[dict] = []
    path = tmp_path / "llm-tasks.jsonl"
    path.write_text("")

    async def scenario():
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, got.append))
        await asyncio.sleep(0.05)                    # first (empty) read done; now in the long sleep
        _append(path, '{"id": "z", "phase": "finished"}\n')
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert any(r["id"] == "z" for r in got)


def test_a_torn_multibyte_write_does_not_end_the_tail(tmp_path, monkeypatch):
    """A record read while its write is still landing can stop inside a multi-byte character
    (the `·` of "Compaction · archival"), and the strict UTF-8 read raises on it. The offset has
    not moved, so the next poll reads the record whole — the tail must outlive that poll. It
    used to die there, and its exception then rewrote the run's own outcome (Runner._supervise).
    """
    monkeypatch.setattr(tailer_mod, "POLL_S", 0.02)
    got: list[dict] = []
    path = tmp_path / "llm-tasks.jsonl"
    whole = (json.dumps({"id": "a", "phase": "started", "purpose": "Compaction · archival"},
                        ensure_ascii=False) + "\n").encode()
    cut = whole.index("·".encode()) + 1                # inside the two-byte middle dot
    path.write_bytes(whole[:cut])

    async def scenario():
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, got.append))
        await asyncio.sleep(0.1)                      # polls that met the torn record
        assert not task.done()
        _append_bytes(path, whole[cut:])
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert [r["id"] for r in got] == ["a"]


def test_missing_sidecar_never_crashes(tmp_path):
    async def scenario():
        task = asyncio.create_task(tail_llm_sidecar(tmp_path, lambda r: None))
        await asyncio.sleep(0.02)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())  # no file present → clean no-op


def _append(path, line):
    """Sync append helper — keeps blocking file IO out of the async test bodies."""
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _append_bytes(path, data: bytes):
    with path.open("ab") as f:
        f.write(data)
