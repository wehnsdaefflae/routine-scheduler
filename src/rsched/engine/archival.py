"""BACKGROUND archival — the navigable history is built off the hot path.

`archive_middle` (a model building navigable files from the elided middle) is slow, so the
tiers are split in TIME: the deterministic digest (`maybe_compact`) lands INSTANTLY and the run
carries on, while the archival runs in a thread; when it finishes the run is told by an APPENDED
note.

Nothing is summarized away: the transcript keeps every byte, the archive is built losslessly from
the real middle, and the digest is only a placeholder for the time the archive takes. Do not chase
speed with a lossy summarize-and-replace.

- **The note is APPENDED, never swapped in** — rewriting the digest would invalidate the cache a
  second time for one compaction.
- **A run that ends inside the archival window keeps only the digest** (the degraded fallback;
  the transcript is untouched). Waiting for the thread at finish would reintroduce the stall.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..endpoints import instrument
from . import enginenote
from .historyarchive import ARCHIVAL_PURPOSE


@dataclass
class Pending:
    """One archival running off the hot path."""

    thread: threading.Thread
    turn: int
    elided: int
    result: dict = field(default_factory=dict)   # written by the thread, read after join

    @property
    def done(self) -> bool:
        return not self.thread.is_alive()


def configure(loop) -> None:
    loop._archival = None


def start(loop, middle: list[dict], endpoint, ref, turn: int) -> None:
    """Archive `middle` in a thread. At most one at a time — the anti-thrash guards already
    keep compactions far apart, and a second would be racing the first's swap.
    """
    from .historyarchive import archive_middle

    if getattr(loop, "_archival", None) is not None:
        return
    ctx = loop.ctx
    pending = Pending(thread=None, turn=turn, elided=len(middle))  # type: ignore[arg-type]

    def work() -> None:
        try:
            got = archive_middle(middle, endpoint, ref, ctx.run_dir, turn)
        except Exception as exc:      # a failed archival is a designed degrade, never an error
            pending.result = {"archival_degraded": str(exc)[:300]}
            return
        pending.result = got or {"archival_degraded": "the archival model returned no files"}

    pending.thread = threading.Thread(target=work, name=f"archival-{turn}", daemon=True)
    loop._archival = pending
    pending.thread.start()


#: How long a FINISHING run waits for an archival already in flight — short, because a
#: conversation's reply is rendered from the finish. A longer one is abandoned (daemon thread,
#: atomic `_swap_in_history`); the digest plus the full transcript remain.
SETTLE_SECONDS = 5.0


def settle(loop) -> None:
    """At run end: give an in-flight archival a moment to land, then record what happened.

    The archive still has later readers (the search index covers `history/`), so a run that
    finishes just after compacting waits briefly for it — never the full archival timeout.
    """
    pending = getattr(loop, "_archival", None)
    if pending is None:
        return
    pending.thread.join(timeout=SETTLE_SECONDS)
    if not pending.done:
        loop._archival = None
        # The daemon thread is killed at exit WITHOUT unwinding, so the instrumentation never
        # writes its `finished`/`failed` record and the task would stay `running` forever
        # (F572). We know, and are still alive: close it here.
        abandoned = instrument.abandon_open_calls(
            purpose=ARCHIVAL_PURPOSE,
            error="the run ended before the archive finished")
        loop.ctx.transcript.event("compaction", {
            "background": True, "archival_abandoned": True,
            "elided_messages": pending.elided,
            "llm_calls_abandoned": abandoned,
            "note": "the run ended before the archive finished; the digest and the full "
                    "transcript stand"})
        return
    _record(loop, pending, announce=False)


def collect(loop) -> None:
    """At a turn boundary: if the archive has landed, tell the run where it is.

    At the boundary on purpose: the message list is only appended to between turns.
    """
    pending = getattr(loop, "_archival", None)
    if pending is None or not pending.done:
        return
    _record(loop, pending, announce=True)


def _record(loop, pending: Pending, *, announce: bool) -> None:
    """Book the archival's outcome. `announce` adds the note telling the run where to look —
    true at a turn boundary, false at finish, where there is no next turn to read it.
    """
    loop._archival = None
    pending.thread.join(timeout=0)
    info: dict[str, Any] = dict(pending.result or {})
    ctx = loop.ctx
    if info.get("archival_degraded"):
        # The digest stands. Recorded, so a broken archival model does not go unnoticed.
        ctx.transcript.event("compaction", {**info, "background": True})
        return
    if info.get("usage"):
        ctx.add_usage(info["usage"])          # the archival call's own spend hits the books
    loop._history_active = True
    loop._hist_note_countdown = 0
    ctx.transcript.event("compaction", {**info, "background": True})
    if not announce:
        return
    enginenote.append(loop,
        f"the {pending.elided} messages elided earlier have finished archiving into "
        f"a NAVIGABLE history — `{loop._hist_rel}/INDEX.md` lists {info.get('history_files', 0)} "
        "files with a line each on what they hold. The one-line digest above stays as a map of "
        "what happened; read the index and then the specific files when you need the actual "
        "detail of an earlier turn.")
