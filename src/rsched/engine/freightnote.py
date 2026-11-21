"""Freight a LIVE run may not consume, NAMED to it at every turn boundary (F529).

A run drains only `LIVE_MESSAGE_VIAS` once it is going (`engine/channels`); everything else (a
decision answer, a sibling's report, a queued message, a trigger text) belongs to the routine's
NEXT FRESH run. The boot digest names what a leg may not take, but freight keeps arriving — and
a run never told that an answer landed asks the operator again.

So at every turn boundary such freight is NAMED, never delivered: nothing is consumed or marked
read. The note's point is that an answer sitting in `inbox/` is ANSWERED.

Once per FILE. `seed` counts what boot already named (a fresh run drained everything; a resumed
leg's digest named the rest), so the two clocks never repeat each other and the boot seed is
this layer's rebuild (`engine/guardscope.py`).
"""

from __future__ import annotations

from . import enginenote, inbox

#: The lead line — phrased as an arrival, not as an instruction to drain anything.
LEAD = ("freight arrived in this routine's inbox while this run was working — it is addressed "
        "to this routine's NEXT FRESH RUN, so this run may not consume it")

#: What the run must do with the knowledge; the first clause is the point of the note.
TAIL = ("An answer sitting in inbox/ is ANSWERED — do not record it as still open or awaiting "
        "him, and do not ask him again. read_file the named file in inbox/ before describing "
        "any of it, and leave it there: the next fresh run consumes it.")


def named(loop) -> set[str]:
    """The freight files already named to this run — this layer's whole run state.

    Created ON FIRST USE rather than in `loopsetup.configure`, so a loop stand-in lacking it
    misses a note instead of raising (and `configure` is at ruff's statement ceiling).
    """
    state = getattr(loop, "freight_named", None)
    if state is None:
        state = loop.freight_named = set()
    return state


def seed(loop) -> None:
    """Count whatever is ALREADY queued as named — `boot` calls this after the drain and
    before the digest, which names exactly this set.

    A fresh run drained everything, so it seeds nothing; a resumed leg seeds what its digest
    names — one statement, not two.
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    named(loop).update(
        f["file"] for f in inbox.queued_freight(ctx.routine.dir,
                                                exclude_vias=inbox.LIVE_MESSAGE_VIAS))


def line(item: dict) -> str:
    """One waiting file, named the way the boot digest names it."""
    if item.get("report"):
        return (f"- [report {item['report']} from {item['from'] or 'a sibling routine'} · "
                f"{item['file']}] {item['text']}")
    return f"- [{item['via'] or 'queued'} · {item['ts'][:16]} · {item['file']}] {item['text']}"


def note(items: list[dict]) -> str:
    """The note's full text — the lead, one line per waiting file, and what to do about it."""
    return f"{LEAD}:\n" + "\n".join(line(i) for i in items) + f"\n{TAIL}"


def at_boundary(loop) -> None:
    """Name freight that has arrived and that this run may not consume, once per file.

    A CHILD run has no routine inbox (the same depth rule `drain_injections` applies).
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    already = named(loop)
    fresh = [f for f in inbox.queued_freight(ctx.routine.dir,
                                             exclude_vias=inbox.LIVE_MESSAGE_VIAS)
             if f["file"] not in already]
    if not fresh:
        return
    already.update(f["file"] for f in fresh)
    enginenote.append(loop, note(fresh), freight_named=[f["file"] for f in fresh])
