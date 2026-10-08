"""Run transcript: append-only JSONL, line-buffered, plus an offset-based reader/tailer.

The event vocabulary is a CONTRACT consumed by the web renderer and the meta routine — every
type in `EVENT_TYPES` below and no other. An unknown type reads as corruption to both, so the
tuple and both readers are extended together, never one alone, and no type is repurposed.
(This line used to state the count in words. It said "fourteen" while the tuple held 18, which
is what a written-out count does: `EVENT_TYPES` is the number, and `len()` is how to ask.)
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import IO

from ..ids import now_iso

log = logging.getLogger("rsched.transcript")

EVENT_TYPES = (
    "header", "assistant_action", "observation", "question", "answer", "user_injection",
    "subrun_start", "subrun_end", "compaction", "error", "refusal", "stopping_update",
    # A finish that STOOD while declared stages went unentered (F521/R1681) — a notice,
    # never a refusal: skipping is sometimes right, going unnoticed never is.
    "stages_skipped", "finish",
    # The escalation ladder's five records (engine/ladder.py). A rung is oversight: it must
    # leave a trace whether it fired, ruled, ruled nothing, never ran, or BROKE — an oversight
    # mechanism nobody can audit is not oversight. `oversight_dispatch` is the rung STARTING
    # (why it fired, over which interval, on what budget); `oversight_skipped` the reason it
    # DECLINED to run (no supervisor pattern, the tree's ceiling, a spent budget) — a decision
    # the ladder took on purpose. `oversight_failed` is the mechanism RAISING, which is not a
    # decision at all: for most of the ladder's life an AttributeError was filed as a skip
    # (`loop.subs` for `loop.subruns`, ladder.py:297, R2348/R2352), so every rung on every run
    # died and read exactly like a healthy decline on every surface. The two must never share an
    # event again. The directive's TEXT reaches the worker as a `user_injection` on the
    # `oversight` channel, so `oversight_directive` records the VERDICT, not the prose.
    "oversight_dispatch", "oversight_directive", "oversight_skipped", "oversight_no_directive",
    "oversight_failed",
    # A PERSON stopped the call of one turn (F586, decided as D160-C: "B plus a transcript
    # event, so an audit of the run afterwards SEES the human intervention instead of inferring
    # it from a gap"). The observation already carries `cancelled`, but an observation says what
    # the RUN was told; this says that somebody outside the run reached in, which is the thing
    # an audit cannot reconstruct afterwards — a cancelled call and a call that failed fast look
    # the same in a transcript that records only outcomes.
    "action_cancelled",
)


class Transcript:
    """Append-side handle. One instance per (sub)run; the engine is the only writer.

    `on_event` is an optional observer called with every event object right after it hits
    disk — the CLI's live stream. It can only watch; what lands in the file is fixed here.
    """

    def __init__(self, path: Path, on_event: Callable[[dict], None] | None = None):
        self.path = path
        self.on_event = on_event
        path.parent.mkdir(parents=True, exist_ok=True)
        # Long-lived line-buffered append handle by design (closed via close()); a
        # with-block per event would re-open the file on every write.
        self._fh: IO[str] = path.open("a", encoding="utf-8", buffering=1)

    def write(self, obj: dict) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fh.flush()
        if self.on_event is not None:
            self.on_event(obj)

    def header(self, *, run_id: str, routine: str, workflow: dict, orchestrator: dict,
               depth: int = 0, parent: str | None = None, brief: str = "") -> None:
        self.write({"type": "header", "run_id": run_id, "routine": routine,
                    "workflow": workflow, "orchestrator": orchestrator,
                    "started": now_iso(), "depth": depth, "parent": parent,
                    **({"brief": brief} if brief else {})})

    def event(self, type_: str, payload: dict, *, turn: int | None = None,
              usage: dict | None = None, **extra) -> dict:
        # engine-internal invariant (the vocabulary is a contract), not input validation
        assert type_ in EVENT_TYPES, f"unknown transcript event type {type_!r}"  # noqa: S101
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

    def __del__(self) -> None:
        # Backstop, not the contract: owners close explicitly (the loop's finally, subrun
        # exit). This keeps a Transcript that never got a run — a rejected child, a test
        # fixture — from leaking its append handle.
        try:
            self.close()
        except Exception:
            pass


def _open_bytes(path: Path) -> io.BufferedIOBase:
    """The transcript's BYTES — the plain file, or `<path>.gz` once retention has rotated the
    plain one away. Both hold the same bytes, so a byte offset means the same thing in either.
    """
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    rotated = path.with_suffix(path.suffix + ".gz")
    if not path.exists() and rotated.exists():
        return gzip.open(rotated, "rb")
    return path.open("rb")


def read_events(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Read complete JSONL lines from byte `offset`. Returns (events, new_offset).
    A partial final line (mid-write) is held back — new_offset stops before it, so a
    concurrent reader never sees broken JSON. A rotated (.gz) transcript reads the same way:
    it never grows, so a reader that has reached its end is simply told nothing is new.

    BYTES, split at the newline byte alone, and only a COMPLETE line is decoded (json.loads
    takes bytes).
    Reading text got two things wrong. `str.splitlines()` also breaks at U+2028, U+2029 and
    U+0085, which `json.dumps(ensure_ascii=False)` leaves raw inside a line — so a rotated
    transcript lost every event that quoted one. And decoding a line the writer had not
    finished could stop inside a multi-byte character, raising UnicodeDecodeError out of a
    polling reader instead of holding the line back.
    """
    events: list[dict] = []
    try:
        fh = _open_bytes(path)
    except OSError:
        return [], offset
    with fh:
        fh.seek(offset)   # a gz file seeks forward by decompressing: same bytes, same offset
        pos = offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                break  # partial write in progress — retry from `pos` next poll
            pos += len(raw)
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except ValueError:   # JSONDecodeError, or a complete line that is not UTF-8
                ev = None
            # Valid JSON that is not an OBJECT (a list, a string, a number) is as malformed as
            # broken JSON here: every reader does `ev.get(...)`, and one such line crashed
            # tasktree, fileactivity and statemap for the whole transcript.
            if isinstance(ev, dict):
                events.append(ev)
            else:
                # the bytes are counted (the offset moves past it) so it is skipped exactly once
                log.warning("transcript %s: skipping malformed line ending at byte %d", path, pos)
    return events, pos
