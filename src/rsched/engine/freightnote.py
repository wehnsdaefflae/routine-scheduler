"""Freight a LIVE run may not consume, NAMED to it at every turn boundary (F529).

THE RULE THIS SERVES, which does not change. A run drains `LIVE_MESSAGE_VIAS` once it is
going — the user talking to THIS run, plus the results of work it started. Everything else
(an audit decision answer on `web-audit`, a sibling's report, a routine-page queued message,
a trigger text) is addressed to the routine's NEXT FRESH run and is consumed only by that
run's boot: a follow-up leg draining it wholesale silently ate decision answers meant for
that night's run (D92/D93), and mid-run injection into a running run is the live run view's
channel, by design (`engine/channels.LIVE_MESSAGE_VIAS`).

WHAT WAS MISSING WAS THE CLOCK. The read half (`inbox.queued_freight`) and the first clock
shipped together: a leg's BOOT digest names what that leg may not take
(`composer.state_digest`). Boot is one moment, and the incident is about every moment after
it. On 2026-10-24 the operator answered three decisions between 02:34 and 02:58 into a run
whose digest had been composed at 00:15; nothing in that run's context ever said an answer
had landed, so it went on calling those decisions open and the report it wrote at 02:00
recorded D159 as `open` with an empty resolution — which would have re-surfaced it on the
Decisions page and asked him a THIRD time. His words: *"i answered your questions. for the
second time btw. did my answers get lost the first time?!"*

So the same rule gets the clock it was missing: at every turn boundary, freight this run may
not consume is NAMED. Named, never delivered — nothing is consumed, nothing is marked read,
and the next fresh run still gets all of it. The note says the one thing that stops the
double ask: an answer that is sitting in `inbox/` is ANSWERED, whatever the run's own earlier
work list recorded.

ONCE PER FILE, not once per run: freight keeps arriving, and a run that is told about the
first answer and not the second is the same defect one message later.

AND NEVER WHAT BOOT ALREADY NAMED. `boot` seeds the named set with whatever is queued when the
leg starts (`seed`), because that is exactly the set this leg's digest lists. A fresh run
drains everything before the digest is composed, so it seeds nothing and starts with a clean
set; a RESUMED leg seeds what it may not take, which its digest has just named — so the two
clocks never say the same thing twice, and a crash mid-run costs no duplicate either. That is
also why this layer needs no transcript rebuild of its own (`engine/guardscope.py`): the boot
seed is the rebuild.
"""

from __future__ import annotations

from . import enginenote, inbox

#: The lead line. A run reads this as "something landed that I may not open myself", so it is
#: phrased as the arrival and not as an instruction to drain anything.
LEAD = ("freight arrived in this routine's inbox while this run was working — it is addressed "
        "to this routine's NEXT FRESH RUN, so this run may not consume it")

#: What the run must do with the knowledge. The first clause is the whole point of the note:
#: the double ask happened because a run recorded a decision as awaiting the operator at a
#: moment when his answer was already on disk.
TAIL = ("An answer sitting in inbox/ is ANSWERED — do not record it as still open or awaiting "
        "him, and do not ask him again. read_file the named file in inbox/ before describing "
        "any of it, and leave it there: the next fresh run consumes it.")


def named(loop) -> set[str]:
    """The freight files already named to this run — this layer's whole run state.

    Created ON FIRST USE rather than in `loopsetup.configure`, for two reasons that point the
    same way. A loop STAND-IN in a test carries only the attributes the mechanism under test
    needs (the reason `switches.person_asked` reads its own ts through `getattr`), and a
    required attribute here turns an unrelated test into an AttributeError instead of a missed
    note — `tests/test_patch_receipt.py` calls `control.drain_injections` with a
    SimpleNamespace and did exactly that. And `configure` is at ruff's statement ceiling
    (PLR0915, 75), so one more line there reds the commit gate on the whole repo.
    """
    state = getattr(loop, "freight_named", None)
    if state is None:
        state = loop.freight_named = set()
    return state


def seed(loop) -> None:
    """Count whatever is ALREADY queued as named — `boot` calls this after the drain and
    before the digest, which names exactly this set.

    A fresh run drains every channel, so there is nothing to seed and the first arrival after
    boot is announced. A resumed leg drains only the LIVE vias, so the freight it may not take
    is seeded here and named by its digest instead — one statement, not two.
    """
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    named(loop).update(
        f["file"] for f in inbox.queued_freight(ctx.routine.dir,
                                                exclude_vias=inbox.LIVE_MESSAGE_VIAS))


def line(item: dict) -> str:
    """One waiting file, named the way the boot digest names it — same shape, same altitude:
    enough to recognise, not enough to act on without opening the file.
    """
    if item.get("report"):
        return (f"- [report {item['report']} from {item['from'] or 'a sibling routine'} · "
                f"{item['file']}] {item['text']}")
    return f"- [{item['via'] or 'queued'} · {item['ts'][:16]} · {item['file']}] {item['text']}"


def note(items: list[dict]) -> str:
    """The note's full text — the lead, one line per waiting file, and what to do about it."""
    return f"{LEAD}:\n" + "\n".join(line(i) for i in items) + f"\n{TAIL}"


def at_boundary(loop) -> None:
    """Name freight that has arrived and that this run may not consume, once per file.

    A CHILD run has no routine inbox of its own (its hooks are `runtree`'s), and the
    operator's channels are the root run's — the same depth rule `drain_injections` applies.
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
