"""BACKGROUNDED ACTIONS — one read or fetch running off the turn's hot path (D118 phase 1).

A run advances one action per turn and every action is dispatched synchronously, which is right
for a scheduled routine — nobody is watching it work — and wrong in a conversation, where a slow
`util`, a long `llm` or a ten-minute test run FREEZES the human: a message sent meanwhile is only
injected, picked up after the slow call finally lands.

So a run may mark a read or a fetch `background: true`. The call starts in a thread, the turn
comes back AT ONCE with a *started* observation naming a handle, and the real observation is
appended at a later turn boundary, tagged with that handle. The conversation keeps its speaking
turn throughout.

**Why this is built on `engine/archival.py` and not on `daemon/detached.py`.** The design entry
proposed the detached machinery, "sized to a single action rather than a whole run". Measured
against the code, that is the wrong parent: `daemon/detached.py` is a DAEMON-TICKED,
multi-process manager over `background_home` whose unit is a routine-shaped directory with its
own `routine.yaml`, budget and run subprocess, delivered back by a scheduler tick. A call
backgrounded inside one reply would not START until the next daemon tick, and by then the reply's
process — the thing waiting to read the observation — is gone. `archival.py` is the shape that
already answers this exact need: a daemon thread started by the engine mid-turn, collected
append-only at the turn boundary, with the two lessons it learned the hard way (a settle window
at run end, and `instrument.abandon_open_calls` so an abandoned thread's in-flight model call
cannot leak a permanently-`running` task). This module is that pattern, parameterised by action.

**The ordering contract is preserved, not weakened.** One action still starts per turn and one
observation still comes back for it; only the arrival is deferred. What keeps that safe is the
KIND allowlist, not anything here: `actions.BACKGROUNDABLE_KINDS` admits reads and fetches, whose
only effect IS the observation, and refuses every mutation — a backgrounded write could be read
stale by the next synchronous action, which needs a dependency model (the design's phase 3).

**No new transcript event types.** The started record and the real observation are both
`observation` events, carrying `background` and `handle`. A new type would mean five coupled
changes (`EVENT_TYPES`, the console renderer, the CLI renderer, the docs' written-out list, the
search sources) and records nothing until all five land; the design's own wording — "appended as
an `observation` event tagged with its handle" — asks for exactly this instead.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..endpoints import instrument

#: The purpose tag an abandoned background call's in-flight model calls are closed under.
BACKGROUND_PURPOSE = "background action"

#: How long a FINISHING run waits for a background call still in flight. The run's work is over,
#: so this delays nothing anybody is waiting on — but a conversation's reply is rendered from the
#: finish, so it stays short enough to be invisible. Deliberately the archival window: the two
#: are the same trade, made at the same moment, and two numbers would only drift apart.
SETTLE_SECONDS = 5.0


@dataclass
class Pending:
    """One backgrounded action: the thread running it and where its observation lands."""

    handle: str
    kind: str
    brief: str
    turn: int                                   # the turn that STARTED it
    thread: threading.Thread
    result: dict = field(default_factory=dict)  # written by the thread, read after it is done

    @property
    def done(self) -> bool:
        return not self.thread.is_alive()


def configure(loop) -> None:
    """Called once at loop construction, like every other per-loop channel."""
    loop._background = []
    loop._background_seq = 0


def _pendings(loop) -> list[Pending]:
    """The live list — never a substitute for it.

    This read used to be `getattr(loop, "_background", None) or []`, which looks harmless and
    silently broke the whole feature: an EMPTY list is falsy, so every call on a loop with no
    background work returned a FRESH throwaway list. `start` appended its Pending to that
    throwaway and the next read got another one, so nothing was ever stored and `collect` saw
    an empty list for the rest of the run — the backgrounded call ran, and its result was
    discarded. A defaulting accessor must create the real attribute, not stand in for it.
    """
    if getattr(loop, "_background", None) is None:
        loop._background = []
    return loop._background


def start(loop, action: dict, ctx) -> dict:
    """Run `action` in a thread and return the *started* observation for this turn.

    The thread runs the ORDINARY dispatch path (`executor.dispatch`), not a copy of it: the
    executor is a pure `(action, ctx)` call, so a backgrounded `util` is byte-for-byte the same
    call it would have been synchronously — there is no second code path to keep in step, and no
    class of bug that only appears in the background.
    """
    from .executor import dispatch

    loop._background_seq = getattr(loop, "_background_seq", 0) + 1
    handle = f"bg{loop._background_seq}"
    flagless = {k: v for k, v in action.items() if k != "background"}
    pending = Pending(handle=handle, kind=action["kind"], brief=_brief(action),
                      turn=ctx.turn, thread=None)  # type: ignore[arg-type]

    def work() -> None:
        try:
            pending.result = dispatch(flagless, ctx) or {}
        except Exception as exc:
            # A handler that raised is an observation the run can read and route around — the
            # same wording `actionroute.dispatch_action` gives a synchronous one. A background
            # failure that vanished would be the `failure-visibility` rule broken by design.
            pending.result = {
                "kind": action["kind"], "engine_error": True,
                "error": f"{type(exc).__name__}: {exc} — raised by the handler while running "
                         "in the background. Do not repeat the identical action; work around "
                         "it, and report it if the task depends on it."}

    pending.thread = threading.Thread(target=work, name=f"background-{handle}", daemon=True)
    _pendings(loop).append(pending)
    pending.thread.start()
    # NO transcript event here. This observation is RETURNED, and the loop's `_observe` is the
    # single writer of an action's observation event — writing one here recorded the start
    # twice (measured: two `started` events for one action), which is also how a later reader
    # counting handles sees phantom work. The deferred observation in `collect` below is the
    # one this module does write, because by then no turn is dispatching it.
    return {"kind": action["kind"], "background": True, "handle": handle, "started": True,
            "brief": pending.brief,
            "note": f"STARTED IN THE BACKGROUND as `{handle}` — this turn is yours again. The "
                    f"real observation of this {action['kind']} is appended at a later turn "
                    f"boundary, tagged `{handle}`. Do not re-run it, and do not take an action "
                    f"whose correctness depends on its result until it lands; anything else "
                    f"you can do now, do now."}


def collect(loop) -> None:
    """At a turn boundary: append the real observation of every background call that landed.

    At the boundary rather than mid-turn for the reason every other boundary feed exists: the
    message list is only ever APPENDED to between turns, and a message arriving in the middle of
    a turn's own bookkeeping is how an appended tail gets corrupted.
    """
    from .observations import format_observation

    landed = [p for p in _pendings(loop) if p.done]
    if not landed:
        return
    loop._background = [p for p in _pendings(loop) if not p.done]
    ctx = loop.ctx
    for pending in landed:
        pending.thread.join(timeout=0)
        obs: dict[str, Any] = dict(pending.result or {})
        # `ctx.turn + 1`, not `ctx.turn`: a boundary runs BEFORE the turn it opens is counted,
        # so stamping the current value records the result as arriving on the turn that STARTED
        # it — which is the one thing a reader checking the ordering is looking at. The turn
        # named here is the turn that actually reads the message appended just below.
        ctx.transcript.event("observation", {
            **_recordable(obs), "background": True, "handle": pending.handle,
            "started_turn": pending.turn}, turn=ctx.turn + 1)
        loop.messages.append({"role": "user", "content":
            f"BACKGROUND RESULT — `{pending.handle}` ({pending.kind}: {pending.brief}), "
            f"started on turn {pending.turn}, has finished. This is its real observation:\n"
            + format_observation(obs)})


def settle(loop) -> str:
    """At run end: give calls still in flight a moment to land, record every one of them, and
    return the one line the run's SUMMARY needs about what it never read.

    Two different losses live here and they are not interchangeable:

    * a call that LANDED with no turn left to read it — its result exists and is recorded, so a
      person reading the run afterwards has it even though the model never saw it. This is the
      ordinary end of a short run that backgrounded something, and it is why the record carries
      the real observation under the SAME payload keys `collect` writes: one shape, one reader.
    * a call still RUNNING — its result is gone. The thread is a daemon, so the interpreter
      kills it WITHOUT unwinding its stack: the instrumentation wrapper's `except BaseException`
      never runs and an in-flight model call's `finished`/`failed` record is never written, which
      leaves a permanently-`running` task in the task centre — the leak archival already paid
      for once. We are the party that knows, and we are still alive, so we say so.
    """
    pendings = _pendings(loop)
    if not pendings:
        return ""
    deadline_each = SETTLE_SECONDS / max(len(pendings), 1)
    for pending in pendings:
        pending.thread.join(timeout=deadline_each)
    abandoned = [p for p in pendings if not p.done]
    loop._background = []
    for pending in [p for p in pendings if p.done]:
        loop.ctx.transcript.event("observation", {
            **_recordable(dict(pending.result or {})), "background": True,
            "handle": pending.handle, "started_turn": pending.turn, "unread": True,
            "note": "landed after the run's last turn — recorded here, never read by the run"},
            turn=loop.ctx.turn)
    if not abandoned:
        return ""
    closed = instrument.abandon_open_calls(
        purpose=BACKGROUND_PURPOSE,
        error="the run ended before the background action finished")
    for pending in abandoned:
        loop.ctx.transcript.event("observation", {
            "kind": pending.kind, "background": True, "handle": pending.handle,
            "started_turn": pending.turn, "abandoned": True,
            "llm_calls_abandoned": closed,
            "note": "the run ended before this background action finished; its result is lost "
                    "and nothing in the run read it"}, turn=loop.ctx.turn)
    return _brief_list(abandoned)


def _brief_list(pendings: list[Pending]) -> str:
    """One line per background call — what a run is told it is waiting on, or lost."""
    return "; ".join(f"`{p.handle}` ({p.kind}: {p.brief})" for p in pendings)


def _brief(action: dict) -> str:
    from .actionschema import brief_value

    return str(brief_value(action))[:120]


def _recordable(obs: dict) -> dict:
    """The observation as the TRANSCRIPT keeps it — media bytes stripped, like the loop's own."""
    from . import mediaops

    return mediaops.without_bytes(obs)
