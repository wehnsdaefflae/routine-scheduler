"""What a background call SPENT, booked whatever the run then felt about the answer (D167).

Three places book: `collect` (the call landed and is delivered), `settle` (it landed too late
to read, or the run ended first) — and `cancel`, which is why this module exists.

A cancelled `llm`/`decide` call parks its reading only once the provider answers — after its
Pending has left the live list `collect` and `settle` iterate. This ledger remembers the parking
key is owed, and both turn boundaries sweep it, so the spend lands at a boundary like every
other background booking. Not booked at request time: that would charge an estimate.

A swept key is removed (a cancelled call books at most once); a thread killed with the
interpreter parks nothing and its key is dropped at run end. The ledger holds handles, not
threads.
"""

from __future__ import annotations

from typing import Any

from .run_context import BACKGROUND_THREAD_PREFIX


def parking_key(handle: str) -> str:
    """The parking slot one background call's thread books into.

    One definition of the key's shape, matching `run_context.background_parking_key`.
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

    Defaults the attribute into existence rather than returning a throwaway (an EMPTY dict is
    falsy, and a throwaway would silently discard every write).
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

    Returns one row per booking, for the transcript. A key with nothing parked yet stays owed —
    the provider may still answer.
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
    """At run end: give up on every handle still owing. Returns them, for the record."""
    owed = _owed(loop)
    left = sorted(owed)
    owed.clear()
    return left
