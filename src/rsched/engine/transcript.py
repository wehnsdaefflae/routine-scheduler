"""Run transcript: append-only JSONL, line-buffered, plus an offset-based reader/tailer.

The event vocabulary is a CONTRACT consumed by the web renderer and the meta routine:
header, assistant_action, observation, question, answer, user_injection, subrun_start,
subrun_end, compaction, error, finish. Extend, never repurpose.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import IO

from ..ids import now_iso

EVENT_TYPES = (
    "header", "assistant_action", "observation", "question", "answer", "user_injection",
    "subrun_start", "subrun_end", "compaction", "error", "finish",
)


class Transcript:
    """Append-side handle. One instance per (sub)run; the engine is the only writer."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered

    def write(self, obj: dict) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fh.flush()

    def header(self, *, run_id: str, routine: str, workflow: dict, orchestrator: dict,
               depth: int = 0, parent: str | None = None) -> None:
        self.write({"type": "header", "run_id": run_id, "routine": routine,
                    "workflow": workflow, "orchestrator": orchestrator,
                    "started": now_iso(), "depth": depth, "parent": parent})

    def event(self, type_: str, payload: dict, *, turn: int | None = None,
              usage: dict | None = None, **extra) -> dict:
        assert type_ in EVENT_TYPES, f"unknown transcript event type {type_!r}"
        obj: dict = {"ts": now_iso(), "type": type_, "payload": payload}
        if turn is not None:
            obj["turn"] = turn
        if usage is not None:
            obj["usage"] = usage
        obj.update(extra)
        self.write(obj)
        return obj

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


def _open_maybe_gz(path: Path):
    """Returns (fh, is_gz). Falls back to <path>.gz when the plain file is rotated away."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8"), True
    if not path.exists() and path.with_suffix(path.suffix + ".gz").exists():
        return gzip.open(path.with_suffix(path.suffix + ".gz"), "rt", encoding="utf-8"), True
    return open(path, "r", encoding="utf-8"), False


def read_events(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Read complete JSONL lines from byte `offset`. Returns (events, new_offset).
    A partial final line (mid-write) is held back — new_offset stops before it, so a
    concurrent reader never sees broken JSON. Gzipped transcripts only support offset 0."""
    events: list[dict] = []
    try:
        fh, is_gz = _open_maybe_gz(path)
    except OSError:
        return [], offset
    with fh:
        if is_gz:
            data = fh.read()
            for line in data.splitlines():
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return events, len(data.encode("utf-8"))
        fh.seek(offset)
        pos = offset
        for line in fh:
            raw = line.encode("utf-8")
            if not line.endswith("\n"):
                break  # partial write in progress — retry from `pos` next poll
            pos += len(raw)
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # count the bytes, skip the noise
        return events, pos
