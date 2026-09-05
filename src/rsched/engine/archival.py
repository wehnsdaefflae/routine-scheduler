"""BACKGROUND archival — the navigable history is built off the hot path.

Compaction has three tiers and the good one is slow: `archive_middle` hands the elided
middle to a model and waits 180–600s for a set of navigable files back. That is a visible stall
in the middle of a run, and it lands at exactly the moment the run is busiest.

So the two tiers are split in TIME rather than traded off. The deterministic digest
(`maybe_compact`, one line per elided turn) lands INSTANTLY and the run carries straight on; the
archival call runs in a thread against the middle that was just elided; when it finishes, the
run is told — by an APPENDED note — that the navigable history is now there.

**The guardrail this is built around.** Claude Code's compaction is instant BECAUSE it is
summarize-and-replace, which is lossy: detail survives only in terminal scrollback. Do not chase
that speed by adopting that mechanism. Nothing here summarizes anything away — the transcript
has always kept every byte, the archive is still built losslessly from the real middle, and the
digest is a PLACEHOLDER in the prompt for the minute the archive takes, not the product. What
changed is when the run gets to keep working, not what it ends up with.

Two consequences worth stating plainly rather than discovering later:

- **The note is APPENDED, never swapped in.** Rewriting the digest message would be a second
  rewrite of the prefix and a second cache invalidation for one compaction. Appending costs
  nothing and keeps the message list append-only, which is the contract everywhere else.
- **A run that ends inside the archival window keeps only the digest.** That is today's
  degraded fallback, reached by a different route, and the transcript is untouched either way —
  so the floor is exactly where it already was. Waiting for the thread at finish would
  reintroduce the stall at the worst possible moment.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


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
    from .compaction import archive_middle

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


#: How long a FINISHING run waits for an archival already in flight. The run's work is over,
#: so this delays nothing the model or the user is waiting on — but a conversation's reply is
#: rendered from the finish, so it stays short enough to be invisible. An archive that needs
#: longer is abandoned: the thread is a daemon, `_swap_in_history` is atomic, and the digest
#: plus the full transcript are what remain — exactly today's degraded fallback.
SETTLE_SECONDS = 5.0


def settle(loop) -> None:
    """At run end: give an in-flight archival a moment to land, then record what happened.

    Without this, a run that finishes shortly after compacting drops its archive on the floor.
    That archive has no reader left in the run itself, but it is not worthless — the search
    index covers `history/` and a later reader (a person, an audit routine) goes looking there.
    Waiting a few seconds for a write already in flight is cheap; waiting the full archival
    timeout would put the 180-600s stall back, at the moment a conversation's reply is due.
    """
    pending = getattr(loop, "_archival", None)
    if pending is None:
        return
    pending.thread.join(timeout=SETTLE_SECONDS)
    if not pending.done:
        loop._archival = None
        loop.ctx.transcript.event("compaction", {
            "background": True, "archival_abandoned": True,
            "elided_messages": pending.elided,
            "note": "the run ended before the archive finished; the digest and the full "
                    "transcript stand"})
        return
    _record(loop, pending, announce=False)


def collect(loop) -> None:
    """At a turn boundary: if the archive has landed, tell the run where it is.

    Called from the boundary rather than mid-turn on purpose — the message list is only ever
    appended to between turns, and a note arriving in the middle of a turn's own bookkeeping is
    how an appended tail gets corrupted.
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
        # The digest stands, which is exactly where the synchronous path landed on a failure.
        # Visible, because a silent degrade is how a broken archival model goes unnoticed.
        ctx.transcript.event("compaction", {**info, "background": True})
        return
    if info.get("usage"):
        ctx.add_usage(info["usage"])          # the archival call's own spend hits the books
    loop._history_active = True
    loop._hist_note_countdown = 0
    ctx.transcript.event("compaction", {**info, "background": True})
    if not announce:
        return
    ctx.transcript.event("user_injection", {"text": "[engine] navigable history ready",
                                            "source": "engine"})
    loop.messages.append({"role": "user", "content":
        f"ENGINE NOTE: the {pending.elided} messages elided earlier have finished archiving into "
        f"a NAVIGABLE history — `{loop._hist_rel}/INDEX.md` lists {info.get('history_files', 0)} "
        "files with a line each on what they hold. The one-line digest above stays as a map of "
        "what happened; read the index and then the specific files when you need the actual "
        "detail of an earlier turn."})
