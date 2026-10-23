"""WHO aborted a run: the actor vocabulary, the breadcrumb each door leaves, and the sentence
the engine's close-out reads back.

An abort arrives as a signal, and a signal carries no sender. So the engine caught one, wrote
"Run aborted by the user/daemon." and attributed it to nobody: a person's cancel, a supervisor
stop and a parent run's kill were indistinguishable afterwards (F751; the specimen,
fau:20261016-125013, aborted at turn 0 while carrying six jobs forward, and its whole record of
who did it is that disjunction). The remedy is F480's, one layer down — THE ACTOR SAYS WHO IT IS
BEFORE IT SIGNALS, because after the signal there is no later moment in which to say it.

Three carriers, because the three kinds of door differ in what they can reach:

* a DOOR OUTSIDE THE PROCESS (the console/API abort, a conversation's stop, a background task's
  cancel, `rsched abort`) writes `abort.json` into the run's own directory and then signals;
* a SIGNAL HANDLER inside the engine process records what it knows (`signalled`) before raising
  the process-wide flag — the only witness for a stop no door wrote, e.g. a `systemctl restart`
  whose control-group SIGTERM reaches the engine and the daemon in the same instant, leaving no
  earlier moment for a breadcrumb;
* a PARENT RUN killing a child sets that child's own `AbortEvent`, which carries the reason — the
  kill never leaves the process, so it needs no file.

The breadcrumb is PID-STAMPED and NOT consumed. Stamped, because a resume reuses the run
directory and the run id (`daemon/runner.resume`): a mark left by an abort that never landed
would otherwise speak for a later leg's ending, the stale-mark failure
`restart.clear_shutdown_mark` exists to prevent. A new leg is a new process, so a mark naming
another pid is not about this leg and is ignored. Not consumed, because a parent and its child
runs close out in ONE process from ONE mark: whichever read first would eat the other's
evidence. What it costs instead is a small file left in the run dir — which is the
machine-readable half of this record, beside the `actor` field on the finish event.

Nothing here raises: an actor that cannot leave its breadcrumb still aborts the run, and the
honest cost of a missing one is `unknown` — which is what "nobody established it" should read
as, and why this vocabulary keeps that word for exactly that case.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

MARK_FILE = "abort.json"

#: actor → (how the finish text names it, the `cause` it files on the health stream). The causes
#: are `daemon/runner_reap.classify_cause`'s vocabulary, extended by `parent_kill` — one word per
#: different investigation, which is what makes a stream of them countable by actor.
ACTORS: dict[str, tuple[str, str]] = {
    "user": ("the user", "user_abort"),
    "parent": ("the parent run", "parent_kill"),
    "signal": ("a signal to the engine process", "signal_kill"),
    "unknown": ("an unrecorded signal", "unknown"),
}

#: What a signal handler in THIS process recorded. Process-wide and sticky, exactly like the
#: abort flag it accompanies (`engine.control._ABORT`): one signal stops every loop in the
#: process, so every one of their close-outs may read it. `forget()` clears it.
_SIGNAL: dict[str, str] = {"actor": "", "reason": ""}


@dataclass(frozen=True)
class Abort:
    """One resolved attribution: WHO aborted the run, the `cause` it files, and the sentence."""

    actor: str
    reason: str

    @property
    def cause(self) -> str:
        """The health stream's one word for this actor (`classify_cause`'s vocabulary)."""
        return ACTORS.get(self.actor, ACTORS["unknown"])[1]

    @property
    def sentence(self) -> str:
        """The finish text — it NAMES an actor instead of offering two, and when nothing
        established one it says so, rather than picking the likelier story.
        """
        who = ACTORS.get(self.actor, ACTORS["unknown"])[0]
        if self.actor == "unknown":
            return f"Run aborted by {who} (no actor left a record)."
        return f"Run aborted by {who}{f' — {self.reason}' if self.reason else ''}."


class AbortEvent(threading.Event):
    """A child run's abort event, carrying WHY its parent set it.

    A parent kills a child by setting this event (`engine/subruns.kill`, `kill_all`) in the same
    process, so the reason rides the object the child already polls — and the child's close-out
    can then say whether it was killed on purpose or stopped because its parent finished. The
    first reason set wins: a child killed deliberately must not be relabelled by the parent's
    exit sweep moments later.
    """

    reason: str = ""

    def set(self, reason: str = "") -> None:
        if reason and not self.reason:
            self.reason = reason
        super().set()


def signalled(actor: str, reason: str) -> None:
    """A signal handler's door: record WHO this signal is, then raise the process-wide flag.

    The handler is the only witness a signal has, so `rsched engine-run` and `run-once` install
    this instead of a bare `request_abort` — a stop nothing else recorded then still names what
    is known (a SIGTERM the engine caught, a Ctrl-C at a terminal) rather than nothing at all.
    First writer wins: a second signal arriving during the close-out must not rewrite it.
    """
    from .engine.control import request_abort

    if actor and not _SIGNAL["actor"]:
        _SIGNAL.update(actor=actor, reason=reason)
    request_abort()


def forget() -> None:
    """Drop this process's signal record — for a test process, which serves many runs."""
    _SIGNAL.update(actor="", reason="")


def write(run_dir: Path, *, actor: str, reason: str, pid: int | None) -> None:
    """Leave the breadcrumb a door owes the run it is about to signal. Best-effort.

    `pid` is the process the door is about to signal, and it is REQUIRED: an unstamped mark
    cannot be told from a stale one, because a resume reuses the run directory, so a door that
    does not know the pid writes nothing instead of a claim a later leg might read as its own.
    Nothing is lost by that — every door has the pid whenever an engine exists to read the mark
    (the run's status.json carries it; a run still QUEUED has no engine and no abort to explain).
    """
    if not pid:
        return
    try:
        atomic_write_json(Path(run_dir) / MARK_FILE,
                          {"actor": actor, "reason": reason, "ts": now_iso(), "pid": pid})
    except OSError:
        pass        # never block the abort this is describing


def resolve(run_dir: Path, event: threading.Event | None = None) -> Abort:
    """WHO aborted the run this engine is closing out.

    In order: the parent's kill (its event carries the reason), then the door's breadcrumb, then
    what a signal handler recorded in this process, then `unknown`. The parent comes first
    because a child killed by its parent is not the actor the process-wide flag names: a signal
    aborts every loop in the process, while the child's own event is the one fact only its
    parent set.
    """
    if event is not None and event.is_set() and (why := getattr(event, "reason", "")):
        return Abort("parent", str(why))
    mark = read_json(Path(run_dir) / MARK_FILE)
    if (isinstance(mark, dict) and mark.get("actor") in ACTORS
            and mark.get("pid") == os.getpid()):
        return Abort(str(mark["actor"]), str(mark.get("reason") or ""))
    if _SIGNAL["actor"]:
        return Abort(_SIGNAL["actor"], _SIGNAL["reason"])
    if event is not None and event.is_set():
        return Abort("parent", "the parent run stopped this child-task")
    return Abort("unknown", "")
