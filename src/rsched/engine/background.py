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

**Every call-time gate runs on the STARTING turn, synchronously, before anything is deferred**
(F633). `util` and `script` are the two backgroundable kinds whose dispatch `engine/actionroute`
owns rather than `executor.DISPATCH`, because D39 decided secret exposure at CALL time and that
decision is answered by the user while the run waits — which a thread cannot do, having no turn to
block on. Backgrounding one used to walk straight past that: a `script` raised `KeyError: 'script'`
(DISPATCH has no entry for it, on purpose) and a `util` RAN with its exposure gate never asked.
So the flag now defers the WORK and never the DECISION, and `_runner` reads the dispatch choice off
the same routing table the foreground consults.

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
from .run_context import BACKGROUND_THREAD_PREFIX as THREAD_PREFIX

#: The purpose tag an abandoned background call's in-flight model calls are closed under.
BACKGROUND_PURPOSE = "background action"

#: How many background calls ONE run may have in flight at once (D118 phase 4, decided D166:
#: "a small fixed cap, e.g. 3: the flagged action is REFUSED (with its reason, in the
#: schema-retry cycle) while the cap is full, so the run decides what to drop").
#:
#: Why a cap exists at all, and why it refuses rather than queues: each pending call is a live
#: thread holding whatever its handler holds — a subprocess, a socket, a model call — and every
#: one of them books against the SAME run's wall-clock and token budgets (D167). Unbounded, a
#: loop that flags every action turns one run into an unbounded fan-out whose results it has no
#: turns left to read, and whose spend arrives after the budget check that would have stopped it.
#:
#: A QUEUE was the alternative and is worse here: it defers the work silently, so the run cannot
#: tell a started call from a parked one, and the parked call still lands its observation at a
#: boundary the run did not plan for. A refusal keeps the choice where the decision put it — with
#: the run, which knows which of its reads matters and can take this one in the foreground, wait
#: for a handle to land, or drop it.
MAX_CONCURRENT = 3

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


def _runner(kind: str):
    """The callable the FOREGROUND would have used for `kind` — the one the thread must use.

    F633: this used to be `executor.dispatch` for every kind, on the premise that "the executor
    is a pure (action, ctx) call, so a backgrounded util is byte-for-byte the same call". The
    premise is false for exactly the two kinds `engine/actionroute._route` owns rather than
    `executor.DISPATCH`: `script` is absent from DISPATCH on purpose (its call-time secret gate
    is in front of it in `_route`), so a backgrounded one raised `KeyError: 'script'` every
    single time — reported from a live run. `util` IS in DISPATCH, so it ran, with its D39 gate
    skipped. The gate now runs on the starting turn (`actionroute._gate_for_background`) and the
    dispatch choice is made here, from the same reading of the routing table.
    """
    from . import executor

    if kind == "script":
        return executor.do_script
    return executor.dispatch


def refuse_at_capacity(loop, action: dict) -> dict | None:
    """The cap (D166), as THIS turn's observation — or None when there is room.

    Shaped like the D39 secret gate's refusal and for the same reason: it is a decision made on
    the STARTING turn, so it must reach the turn that asked for the call. It is not a *started*
    observation and nothing is backgrounded, which is what lets the schema-retry cycle treat it
    as a correctable action rather than as a result.

    It names every live handle, because "the run decides what to drop" is only a real choice if
    the run can see what it is choosing between — a bare "cap reached" leaves it guessing which
    of its own calls to wait for.
    """
    live = _pendings(loop)
    if len(live) < MAX_CONCURRENT:
        return None
    # `rejected` + `reason` is the ONE shape the engine words for an action it did not execute
    # (`observations._not_executed`), and using it is not a style choice: every per-kind
    # renderer below that branch reads the fields of a dispatch RESULT — a `util` refusal
    # carrying `error` instead reaches the util branch and raises `KeyError: 'name'`, which
    # kills the turn AND every later resume, since a resume re-renders each stored
    # observation. That is the exact failure fifteen renderers had already been fixed for;
    # measured again here on the first run of this test.
    return {"kind": action.get("kind"), "background": True, "rejected": True,
            "at_capacity": True, "limit": MAX_CONCURRENT,
            "in_flight": [p.handle for p in live],
            "reason": f"REFUSED, nothing was started: this run already has {len(live)} "
                      f"background calls in flight and {MAX_CONCURRENT} is the cap — "
                      f"{_brief_list(live)}. Choose: take this {action.get('kind')} in the "
                      f"FOREGROUND (drop `background`), or let one of the handles above land "
                      f"first and flag it then. Do not repeat the identical flagged action — "
                      f"it will be refused again until a slot frees."}


def start(loop, action: dict, ctx) -> dict:
    """Run `action` in a thread and return the *started* observation for this turn.

    The thread runs the ORDINARY dispatch path for the action's kind — `_runner` reads it off
    the same routing table the foreground consults, so a backgrounded call is the same call it
    would have been synchronously. What a background thread CANNOT do is ask the user anything,
    so every call-time gate has already run on this turn, before `start` was reached.
    """
    dispatch = _runner(action["kind"])

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

    # The NAME is load-bearing, not a label: `run_context.background_parking_key` reads it to
    # decide that this thread's model spend must be PARKED rather than booked mid-flight
    # (D167), and `_book_usage` reconstructs the same key from the handle. One constant, so a
    # rename cannot quietly turn every background booking back into a racing mid-turn fold.
    pending.thread = threading.Thread(target=work, name=f"{THREAD_PREFIX}{handle}", daemon=True)
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
        spent = _book_usage(ctx, pending)
        obs: dict[str, Any] = dict(pending.result or {})
        # `ctx.turn + 1`, not `ctx.turn`: a boundary runs BEFORE the turn it opens is counted,
        # so stamping the current value records the result as arriving on the turn that STARTED
        # it — which is the one thing a reader checking the ordering is looking at. The turn
        # named here is the turn that actually reads the message appended just below.
        ctx.transcript.event("observation", {
            **_recordable(obs), "background": True, "handle": pending.handle,
            **({"usage_booked": spent} if spent else {}),
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
        spent = _book_usage(loop.ctx, pending)
        loop.ctx.transcript.event("observation", {
            **_recordable(dict(pending.result or {})), "background": True,
            "handle": pending.handle, "started_turn": pending.turn, "unread": True,
            **({"usage_booked": spent} if spent else {}),
            "note": "landed after the run's last turn — recorded here, never read by the run"},
            turn=loop.ctx.turn)
    if not abandoned:
        return ""
    closed = instrument.abandon_open_calls(
        purpose=BACKGROUND_PURPOSE,
        error="the run ended before the background action finished")
    for pending in abandoned:
        # Its spend books even though its RESULT is lost: the tokens were spent at the provider
        # whether or not this run ever read the answer, and charging only the calls that landed
        # would make "background it and finish" the cheapest way to spend money the budget
        # never sees. A thread killed mid-call has parked nothing yet, so this is often empty.
        spent = _book_usage(loop.ctx, pending)
        loop.ctx.transcript.event("observation", {
            "kind": pending.kind, "background": True, "handle": pending.handle,
            "started_turn": pending.turn, "abandoned": True,
            **({"usage_booked": spent} if spent else {}),
            "llm_calls_abandoned": closed,
            "note": "the run ended before this background action finished; its result is lost "
                    "and nothing in the run read it"}, turn=loop.ctx.turn)
    return _brief_list(abandoned)


def _book_usage(ctx, pending: Pending) -> dict:
    """Book what this background call PARKED, at the moment its result is collected (D167).

    The decision's words are "book tokens on collection", and the reason is that a background
    thread folding into `ctx.usage` mid-turn makes the run's own budget check depend on thread
    timing: the number a turn is judged against would differ by milliseconds. Parking moves the
    booking to the boundary, where the result lands anyway — so the spend and the observation
    that caused it arrive together, and a reader of the transcript can tell which call cost what.

    Booked for an ABANDONED call too (`settle`), and that is not an oversight: the tokens were
    spent at the provider whether or not this run ever read the answer. Charging only the calls
    that landed would make a run that backgrounds and finishes the cheapest way to spend money
    the budget never sees.

    Returns the reading it booked, so the caller can record it beside the call.
    """
    spent = ctx.take_deferred_usage(f"{THREAD_PREFIX}{pending.handle}")
    if spent:
        ctx.add_usage(spent)
    return spent


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
