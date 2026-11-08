"""The `wait` action — block until a child run exits OR a backgrounded call lands.

Moved out of `engine/subruns.py` (which sits at the 350-line bar) when `wait` learned the
run's second kind of in-flight work. A run has TWO things it can be waiting for, and before
this `wait` saw only one of them: child runs (`spawn`/`subtask`) and backgrounded calls
(`background: true`, `engine/background.py`). With no child it answered "no sub-workflows have
been spawned" at once, so a run waiting on a backgrounded test gate asked again and again
(scheduler-builder used it 8 times on one background call); with children it never woke on a
landing, so a gate verdict that arrived after two minutes sat unread for the rest of a 20-minute
timeout ("The wait burned 20 minutes"). A guard whose fix is mechanical applies it (P3): the
engine knows what is in flight, so `wait` waits for it.

The rule, one per mode — and every mode still returns at once when what it waits for has
already happened, because a result that landed while the model was composing this very action
must not cost a timeout:

- `n` — that child alone (a background call has no child number; `handle` stays `kill`'s).
- `all` — every child AND every backgrounded call.
- any (the default) — the next unreported child exit or background landing; and once NOTHING
  is in flight any longer, no future event can arrive, so it returns rather than burn the
  timeout.

A landed background call's observation is not delivered HERE: `background.collect` appends it
at the very next turn boundary, the single delivery path every landing already takes, so the
wait names the handle and says the result follows. Waking is the existing 2 s poll of the
child-exit event — a background thread signals nothing, and adding a second wake channel for a
2 s saving would be a second notification path to keep honest.

There is NO concurrency cap on background calls (D166) and this adds none.
"""

from __future__ import annotations

import time

from . import inbox, subsalvage

#: How long a `wait` blocks when the action names no `timeout_s`. NAMED because it is served:
#: `/api/status`'s `action_limits` (`web/actionlimits.py`) publishes the engine's own default
#: operation limits so the console measures against them instead of its own copy (D252). The
#: other three defaults already had names; this one was a literal inside the deadline.
WAIT_DEFAULT_TIMEOUT_S = 600


def _background(loop) -> list:
    """The run's live background calls — landed-but-uncollected ones included (a READ of the
    list `background` owns, never a replacement for it).
    """
    return list(getattr(loop, "_background", None) or [])


def wait(subs, action: dict, *, poll_s: float, aborted) -> dict:
    """Block per the module docstring; the observation names what finished and what runs on."""
    loop = subs.parent
    n = action.get("n")
    want_all = bool(action.get("all"))
    deadline = time.monotonic() + float(action.get("timeout_s") or WAIT_DEFAULT_TIMEOUT_S)
    if n is not None and int(n) not in subs.subruns:
        return {"kind": "wait", "error": subsalvage.no_such_child(loop.ctx.run_dir, int(n))}
    if n is None and not subs.subruns and not _background(loop):
        return {"kind": "wait", "error": "nothing to wait for: no child runs have been spawned "
                                         "and no background call is in flight"}

    def bg_landed() -> list[str]:
        return [p.handle for p in _background(loop) if p.done]

    def bg_running() -> list[str]:
        return [p.handle for p in _background(loop) if not p.done]

    def satisfied() -> bool:
        children = subs.subruns.values()
        if n is not None:
            return subs.subruns[int(n)].done.is_set()
        if want_all:
            return all(s.done.is_set() for s in children) and not bg_running()
        return (any(s.done.is_set() and not s.announced for s in children) or bool(bg_landed())
                or (all(s.done.is_set() for s in children) and not bg_running()))

    def still_running() -> list[int]:
        return [s.n for s in subs.subruns.values() if not s.done.is_set()]

    def answer(**extra) -> dict:
        out = {"kind": "wait", **extra, "finished": subs.finished_rows(
            subs.take_finished_unannounced()), "still_running": still_running()}
        if landed := bg_landed():
            out["background_landed"] = landed
        if running := bg_running():
            out["background_running"] = running
        return out

    while time.monotonic() < deadline:
        if aborted():
            break
        # RESPONSIVE: a user message arriving mid-wait must not be starved. Yield control
        # back to the turn loop (which drains it and lets the parent reply) instead of
        # freezing the conversation — what is in flight keeps running and is announced when it
        # lands. Root runs only (children don't drain the routine inbox).
        if (loop.ctx.depth == 0
                and inbox.has_pending_messages(loop.ctx.routine.dir,
                                               vias=inbox.LIVE_MESSAGE_VIAS)):
            return answer(interrupted_by_user=True, timed_out=False)
        subs.exit_event.clear()
        # re-check after clear: an exit between check and wait must not be lost
        if satisfied():
            break
        subs.exit_event.wait(timeout=min(poll_s, max(0.0, deadline - time.monotonic())))
    return answer(timed_out=not satisfied())  # before collection flips `announced`
