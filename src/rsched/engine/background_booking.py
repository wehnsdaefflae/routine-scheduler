"""What a background call SPENT, booked whatever the run then felt about the answer (D167).

Three places book: `collect` (the call landed and is delivered), `settle` (it landed too late
to read, or the run ended first) — and `cancel`, which is why this module exists.

**The hole F722 measured.** `cancel` drops the Pending from the live list, sets its stop event
and then books. For a `util`/`script`/`shell` that is honest: the subprocess is being
terminated, and its spend is not model usage anyway. For an `llm` or a `decide` it books
NOTHING, because a model call parks its reading only after the provider answers
(`llmaction` → `ctx.add_usage` → the parking area, keyed by the thread's name) and the
provider has not answered yet. The reading then arrives in a parking slot whose Pending has
already left the live list — and `collect` and `settle` both iterate that list, so nobody ever
pops it. Silent under-accounting, repeatable every turn, against the promise stated in the
module docstring, in `cancel`'s own observation note and in the `handle` field's schema
description.

**Why a ledger and not a park-at-request-time.** Booking at request time would charge an
estimate: the real reading is the provider's, it is known only on the answer, and a run's
budget check would then be judged against a number the provider never confirmed. So the
cancelled call keeps exactly what it had — a parking key — and this ledger remembers that the
key is owed. Both turn boundaries sweep it, so the spend lands at a boundary like every other
background booking (`add_usage`'s own contract: a thread folding mid-turn would make the
budget depend on thread timing).

**Why the ledger cannot leak.** A swept key is removed; a cancelled call books at most once.
A thread killed with the interpreter parks nothing, so its key is simply dropped at run end
without a reading — the same silence `settle`'s abandoned branch already accepts. The ledger
holds handles, not threads: nothing here keeps a thread or a result alive.
"""

from __future__ import annotations

from typing import Any

from .run_context import BACKGROUND_THREAD_PREFIX


def parking_key(handle: str) -> str:
    """The parking slot one background call's thread books into.

    One definition of the key's shape, reused: `add_usage` parks under the thread's own name
    (`run_context.background_parking_key`), so a sweep spelling the prefix itself would book
    nothing the day either side changed.
    """
    return f"{BACKGROUND_THREAD_PREFIX}{handle}"


def book(ctx: Any, handle: str) -> dict:
    """Pop and book whatever `handle`'s thread has parked. Empty for a call that spent no
    tokens (a `util`, a `read_file`), which is the common case and books nothing.
    """
    spent = ctx.take_deferred_usage(parking_key(handle))
    if spent:
        ctx.add_usage(spent)
    return spent


def _owed(loop) -> dict[str, str]:
    """Handle → kind for every cancelled call whose reading may still arrive.

    Defaults the attribute into existence rather than standing in for it: an EMPTY dict is
    falsy, and a defaulting accessor that returns a throwaway is how `_pendings` once silently
    discarded every backgrounded call (see its docstring).
    """
    if getattr(loop, "_background_cancelled", None) is None:
        loop._background_cancelled = {}
    return loop._background_cancelled


def owe(loop, handle: str, kind: str) -> None:
    """Remember that a cancelled call may still park a reading. Called by `cancel` AFTER its
    own booking attempt, so a call that had already landed (and therefore already parked) is
    not re-swept.
    """
    _owed(loop)[handle] = kind


def forget(loop, handle: str) -> None:
    """Drop a handle from the ledger — it has been swept, or it never owed anything."""
    _owed(loop).pop(handle, None)


def sweep(loop) -> list[dict]:
    """Book every cancelled call that has parked its reading since the last boundary.

    Returns one row per booking, for the transcript: a spend with no observation beside it is
    how a reader afterwards concludes the engine lost something. A key with nothing parked yet
    stays owed — the provider may still answer, and the next boundary looks again.
    """
    ctx = loop.ctx
    booked: list[dict] = []
    for handle, kind in list(_owed(loop).items()):
        spent = book(ctx, handle)
        if not spent:
            continue
        forget(loop, handle)
        booked.append({"handle": handle, "kind": kind, "usage_booked": spent})
    return booked


def record_sweep(loop, booked: list[dict]) -> None:
    """Write one `observation` event per late booking, under the same keys `collect` writes."""
    ctx = loop.ctx
    for row in booked:
        ctx.transcript.event("observation", {
            "kind": row["kind"], "background": True, "handle": row["handle"],
            "cancelled": True, "late_booking": True,
            "usage_booked": row["usage_booked"],
            "note": "this call was cancelled before the provider answered; the answer arrived "
                    "afterwards and was never delivered, and this is its spend — booked at the "
                    "first boundary after it landed, because the tokens were spent at the "
                    "provider whichever way the run then felt about the answer"},
            turn=ctx.turn)


def drop_all(loop) -> list[str]:
    """At run end: give up on every handle still owing. Returns them, for the record — a
    thread killed with the interpreter parked nothing, so there is no reading to book and the
    silence is the same one `settle`'s abandoned branch already accepts.
    """
    owed = _owed(loop)
    left = sorted(owed)
    owed.clear()
    return left
