"""BACKGROUNDED ACTIONS — one call running off the turn's hot path (D118).

A slow call dispatched synchronously freezes a conversation: a message sent meanwhile waits for
it to land. A run may mark a call `background: true`: it starts in a thread, the turn returns at
once with a *started* observation naming a handle, and the real observation is appended at a
later turn boundary, tagged with that handle.

Built on `engine/archival.py`'s shape (an engine thread started mid-turn, collected append-only
at the boundary, a settle window at run end, `instrument.abandon_open_calls` for abandoned model
calls), not on `daemon/detached.py`, whose daemon-ticked units would not start until the reply
that needs the observation is gone.

Invariants:

* **Every call-time gate runs on the STARTING turn**, synchronously (F633): a thread has no turn
  to block on, so the flag defers the WORK, never the DECISION. `_runner` picks the dispatch
  from the same routing table the foreground uses.
* **Ordering is preserved.** One action starts per turn and one observation comes back for it.
  A read's only effect is its observation; a MUTATION defers behind `engine/background_order.py`,
  which holds later actions touching its declared target until it lands.
* **`kill handle=…` stops the delivery** and, for `STOPPABLE_KINDS`, the work too (see `cancel`).
* **No new transcript event types**: started, landed, cancelled and abandoned records are all
  `observation` events carrying `background` and `handle`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..endpoints import instrument
from . import background_booking, background_summary
from .run_context import BACKGROUND_THREAD_PREFIX as THREAD_PREFIX

#: The purpose tag an abandoned background call's in-flight model calls are closed under.
BACKGROUND_PURPOSE = "background action"

#: The backgroundable kinds whose WORK a cancel can stop: they run through
#: `utils_run.run_jailed`, which polls a cancel check inside its wait and terminates the
#: command's process group. Every other kind runs inside the interpreter, which has no
#: interruption point, so a cancel there stops only the delivery.
STOPPABLE_KINDS = ("util", "script", "shell")

#: THERE IS NO CONCURRENCY CAP, and that is a decision rather than an omission (D166):
#: unbounded, because the run's own turn budget is already the practical limit. The case for a
#: cap (every pending call is a live thread booking against the run's budgets) was heard and
#: decided against — do not reintroduce one as an obvious improvement; reopening it means
#: reopening D166.

#: How long a FINISHING run waits for a background call still in flight — short, since a
#: conversation's reply renders from the finish. Deliberately the archival window: same trade,
#: same moment, one number.
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
    #: Set by `cancel` to stop the WORK; read by the cancel check `run_jailed` polls. Only
    #: `STOPPABLE_KINDS` are given a context that reads it.
    stop: threading.Event = field(default_factory=threading.Event)

    @property
    def done(self) -> bool:
        return not self.thread.is_alive()

    @property
    def stoppable(self) -> bool:
        """Can a cancel stop this call's WORK, or only its delivery?"""
        return self.kind in STOPPABLE_KINDS


def configure(loop) -> None:
    """Called once at loop construction, like every other per-loop channel."""
    loop._background = []
    loop._background_seq = 0


def _pendings(loop) -> list[Pending]:
    """The live list — never a substitute for it.

    A defaulting accessor must CREATE the real attribute: `getattr(...) or []` returns a fresh
    throwaway for an empty list, so appended Pendings would vanish.
    """
    if getattr(loop, "_background", None) is None:
        loop._background = []
    return loop._background


def _runner(kind: str):
    """The callable the FOREGROUND would have used for `kind` — the one the thread must use.

    `script` is absent from `executor.DISPATCH` on purpose (`actionroute._route` owns it, behind
    its secret gate), so it needs its handler named here; the gate itself already ran on the
    starting turn (`actionroute._gate_for_background`, F633).
    """
    from . import executor

    if kind == "script":
        return executor.do_script
    return executor.dispatch


def start(loop, action: dict, ctx) -> dict:
    """Run `action` in a thread and return the *started* observation for this turn.

    The thread runs the ordinary dispatch path for the kind; every call-time gate has already
    run on this turn, since a thread cannot ask the user anything.
    """
    dispatch = _runner(action["kind"])

    loop._background_seq = getattr(loop, "_background_seq", 0) + 1
    handle = f"bg{loop._background_seq}"
    flagless = {k: v for k, v in action.items() if k != "background"}
    pending = Pending(handle=handle, kind=action["kind"], brief=_brief(action),
                      turn=ctx.turn, thread=None)  # type: ignore[arg-type]

    # A stoppable call's cancel check must answer for its HANDLE, not its starting turn
    # (see `_cancellable_ctx`).
    thread_ctx = _cancellable_ctx(ctx, pending) if pending.stoppable else ctx

    def work() -> None:
        try:
            pending.result = dispatch(flagless, thread_ctx) or {}
        except Exception as exc:
            # A handler that raised becomes an observation the run can route around, worded as
            # `actionroute.dispatch_action` words a synchronous one.
            pending.result = {
                "kind": action["kind"], "engine_error": True,
                "error": f"{type(exc).__name__}: {exc} — raised by the handler while running "
                         "in the background. Do not repeat the identical action; work around "
                         "it, and report it if the task depends on it."}

    # The thread NAME is load-bearing: `run_context.background_parking_key` reads it to PARK this
    # thread's model spend instead of booking it mid-flight (D167).
    pending.thread = threading.Thread(target=work, name=f"{THREAD_PREFIX}{handle}", daemon=True)
    _pendings(loop).append(pending)
    pending.thread.start()
    # NO transcript event here: this observation is RETURNED, and the loop's `_observe` is the
    # single writer of an action's observation event.
    return {"kind": action["kind"], "background": True, "handle": handle, "started": True,
            "brief": pending.brief,
            "note": f"STARTED IN THE BACKGROUND as `{handle}` — this turn is yours again. The "
                    f"real observation of this {action['kind']} is appended at a later turn "
                    f"boundary, tagged `{handle}`. Do not re-run it, and do not take an action "
                    f"whose correctness depends on its result until it lands; anything else "
                    f"you can do now, do now."}


def cancel(loop, handle: str) -> dict:
    """Stop DELIVERING a background call's observation (`kill handle=…`, D176) — and, for
    `STOPPABLE_KINDS`, stop its work too.

    The promise differs by kind and the observation says which: a `util`/`script`/`shell`
    thread is blocked in `run_jailed`, whose cancel check (see `_cancellable_ctx`) terminates
    the process group; any other kind runs inside the interpreter and runs to its own end
    unread.

    Either way: the observation never reaches the run, the Pending leaves the live list at once
    (so `collect` cannot deliver a late landing), and the spend still books (D167) — unbooking
    it would make "background, cancel, read nothing" free spend. The cancel is recorded as its
    own `observation` event, keyed like `settle`'s abandoned record but with `cancelled: true`,
    so a reader can tell a dropped result from one the run ran out of turns for.
    """
    live = _pendings(loop)
    wanted = str(handle or "").strip()
    pending = next((p for p in live if p.handle == wanted), None)
    if pending is None:
        # Like `subruns.kill`'s unknown-number error: name the valid handles. "Already landed"
        # is the common case, so it comes first.
        return {"kind": "kill", "handle": wanted, "error":
                f"no background call `{wanted}` is in flight — it has already landed (its "
                f"observation was appended at a turn boundary, tagged with its handle) or "
                f"the handle is not one this run started."
                + (f" In flight now: {background_summary.brief_list(live)}." if live else
                   " This run has nothing in flight.")}
    loop._background = [p for p in live if p is not pending]
    # Ask the WORK to stop first (stoppable kinds only read it), so the subprocess starts dying
    # while we record.
    pending.stop.set()
    ctx = loop.ctx
    spent = background_booking.book(ctx, pending.handle)
    landed = pending.done
    # A call still in flight has parked no reading yet; owe it so `background_booking.sweep`
    # books it when it arrives — nothing else iterates a Pending off the live list (F722).
    if not landed:
        background_booking.owe(loop, pending.handle, pending.kind)
    # The work was stopped only if it was still running AND its kind is terminable.
    stopped_work = pending.stoppable and not landed
    ctx.transcript.event("observation", {
        "kind": pending.kind, "background": True, "handle": pending.handle,
        "started_turn": pending.turn, "cancelled": True, "had_landed": landed,
        "stopped_work": stopped_work,
        **({"usage_booked": spent} if spent else {}),
        "note": "the run cancelled this background call; its result was not delivered"
                + (" (it had already finished — the work was done, the answer was dropped)"
                   if landed else
                   "; its command's process group was terminated" if stopped_work else
                   " and the work it started runs to its own end unread")},
        turn=ctx.turn)
    return {"kind": "kill", "handle": pending.handle, "cancelled": True,
            "background_kind": pending.kind, "brief": pending.brief,
            "had_landed": landed, "stopped_work": stopped_work,
            "in_flight": [p.handle for p in loop._background],
            "note": f"`{pending.handle}` will NOT deliver its observation; it is off the live "
                    f"list ({len(loop._background)} still in flight). "
                    + ("It had already finished, so its result existed and was dropped."
                       if landed else
                       f"The WORK IS BEING STOPPED: a backgrounded {pending.kind} runs as a "
                       "subprocess, and its whole process group is terminated inside the wait "
                       "the engine was already performing." if stopped_work else
                       f"The WORK IS NOT STOPPED — a backgrounded {pending.kind} runs inside "
                       "the interpreter, which Python gives no way to interrupt, so it runs to "
                       "its own end with nobody reading it.")
                    + " What it spent is booked against this run either way."}


def collect(loop) -> None:
    """At a turn boundary: append the real observation of every background call that landed.

    At the boundary because the message list is only appended to between turns.
    """
    from .observations import live_observation

    # Book any cancelled call's late reading first, whether or not anything landed (F722).
    background_booking.record_sweep(loop, background_booking.sweep(loop))
    landed = [p for p in _pendings(loop) if p.done]
    if not landed:
        return
    loop._background = [p for p in _pendings(loop) if not p.done]
    ctx = loop.ctx
    for pending in landed:
        pending.thread.join(timeout=0)
        spent = background_booking.book(ctx, pending.handle)
        obs: dict[str, Any] = dict(pending.result or {})
        # `ctx.turn + 1`: a boundary runs before the turn it opens is counted, and the stamp
        # must name the turn that reads the message appended below.
        ctx.transcript.event("observation", {
            **_recordable(obs), "background": True, "handle": pending.handle,
            **({"usage_booked": spent} if spent else {}),
            "started_turn": pending.turn}, turn=ctx.turn + 1)
        loop.messages.append({"role": "user", "content":
            f"BACKGROUND RESULT — `{pending.handle}` ({pending.kind}: {pending.brief}), "
            f"started on turn {pending.turn}, has finished. This is its real observation:\n"
            + live_observation(obs, ctx)})


def settle(loop) -> str:
    """At run end: give calls still in flight a moment to land, record every one of them, and
    return the one line the run's SUMMARY needs about what it never read.

    Two distinct losses:

    * a call that LANDED with no turn left — its result is recorded `unread` under the same keys
      `collect` writes, so a reader of the run has it even though the model never did;
    * a call still RUNNING — its result is lost. A daemon thread dies without unwinding, so its
      in-flight model calls are closed here (`instrument.abandon_open_calls`) or they would stay
      `running` forever.

    Both are named in the returned line (F715/D195); the wording is
    `engine/background_summary.py`'s.
    """
    # Last chance to book a cancelled call's late reading — before the early return, since such
    # a run may have no pendings left. Whatever is still owed afterwards parked nothing.
    background_booking.record_sweep(loop, background_booking.sweep(loop))
    background_booking.drop_all(loop)
    pendings = _pendings(loop)
    if not pendings:
        return ""
    deadline_each = SETTLE_SECONDS / max(len(pendings), 1)
    for pending in pendings:
        pending.thread.join(timeout=deadline_each)
    abandoned = [p for p in pendings if not p.done]
    landed = [p for p in pendings if p.done]
    loop._background = []
    for pending in landed:
        spent = background_booking.book(loop.ctx, pending.handle)
        loop.ctx.transcript.event("observation", {
            **_recordable(dict(pending.result or {})), "background": True,
            "handle": pending.handle, "started_turn": pending.turn, "unread": True,
            **({"usage_booked": spent} if spent else {}),
            "note": "landed after the run's last turn — recorded here, never read by the run"},
            turn=loop.ctx.turn)
    closed = instrument.abandon_open_calls(
        purpose=BACKGROUND_PURPOSE,
        error="the run ended before the background action finished") if abandoned else 0
    for pending in abandoned:
        # Its spend books even though its result is lost — the provider was paid either way.
        spent = background_booking.book(loop.ctx, pending.handle)
        loop.ctx.transcript.event("observation", {
            "kind": pending.kind, "background": True, "handle": pending.handle,
            "started_turn": pending.turn, "abandoned": True,
            **({"usage_booked": spent} if spent else {}),
            "llm_calls_abandoned": closed,
            "note": "the run ended before this background action finished; its result is lost "
                    "and nothing in the run read it"}, turn=loop.ctx.turn)
    return background_summary.settle_note(unread=landed, abandoned=abandoned)


def _cancellable_ctx(ctx, pending: Pending):
    """`ctx` with ONE thing changed: the cancel check reads this call's own stop event.

    `engine.control.cancelled_for_turn` keys cancel intent by TURN (so a cancel cannot hit the
    next call), but a background call outlives its starting turn. The proxy carries
    `background_stop`, which `cancelled_for_turn` prefers. A proxy, not a mutated ctx (that would
    cancel the foreground call too), and a `__getattr__` delegate, not a copy (the transcript,
    meter and usage lock must be the SAME objects).
    """

    class _BackgroundCtx:
        background_stop = staticmethod(pending.stop.is_set)

        def __getattr__(self, name):
            return getattr(ctx, name)

        def __setattr__(self, name, value):
            setattr(ctx, name, value)

    return _BackgroundCtx()


def _brief(action: dict) -> str:
    from .actionschema import brief_value

    return str(brief_value(action))[:120]


def _recordable(obs: dict) -> dict:
    """The observation as the TRANSCRIPT keeps it — media bytes stripped, like the loop's own."""
    from . import mediaops

    return mediaops.without_bytes(obs)
