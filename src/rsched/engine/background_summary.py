"""The run SUMMARY's account of background calls nobody read (F715, authorized by D195).

A run may background a read or a fetch and get its turn back at once. Two of those calls never
reach the model: one still RUNNING when the run ends, whose result is lost, and one that LANDED
after the last turn boundary, whose result exists and sits in the transcript unread.
`engine/background.settle` records both as observation events; this module turns them into the
line `engine/loopend.finish_run` appends to the summary — the one surface the operator and the
next run actually read.

**Why the landed case speaks now.** It used to be silent: `settle` returned a line only for the
abandoned set, on the argument that a call landing a moment too late is "the ordinary end of a
short run that backgrounded something". That argument weighs the engine's tidiness, not the
reader's position: the run finished having based its conclusions on evidence it was never shown,
and the only place anyone looks said nothing about it. D195 (arXiv 2609.36139) is the
authorization to change it — an explicit honesty instruction lifts a model's flaw-flagging by
33–55 pp WHERE THE MODEL CAN SEE THE FLAW, and the paper's single non-improving scenario is the
hidden pending tool call, which is exactly this one. A run cannot flag what it was never shown,
so the engine has to; the fix is mechanical, not a prompt.

**Two sentences, never one.** The losses differ in the only way a reader acts on: the abandoned
call's answer is GONE, so re-running is the only way to have it, while the landed one's answer
EXISTS in a transcript row and can simply be read. Folding them into one line would delete that
difference and send a reader to repeat work the engine already holds.

Its own module, and that is the size ratchet rather than taste: `engine/background.py` sits at
its ceiling in `tests/size_ratchet.json`, and a file over the house bar may only shrink. The
wording is the part with no state and no threads, so the wording is the part that moves out.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class SettledCall(Protocol):
    """What rendering needs of a background call — `engine/background.Pending` satisfies it."""

    handle: str
    kind: str
    brief: str


def brief_list(calls: Sequence[SettledCall]) -> str:
    """One phrase per background call — the handle, its kind and what it was doing.

    The single source for this phrasing: a run meets it while a call is in flight (the `kill`
    refusal naming the live handles), and again at run end in the summary. Two renderings would
    drift, and a handle written two ways reads as two different calls.
    """
    return "; ".join(f"`{c.handle}` ({c.kind}: {c.brief})" for c in calls)


def settle_note(*, unread: Sequence[SettledCall],
                abandoned: Sequence[SettledCall]) -> str:
    """The summary's account of what this run never read — "" when it read everything.

    Empty is the ordinary case and has to stay empty: a bracket on every run's summary is a
    bracket every reader learns to skip, and then the one run that lost something says it in a
    place nobody looks any more.

    Both halves can be true of the same run, and then both lines appear. The LANDED set goes
    first because it is the one a reader can act on without re-running anything.
    """
    lines: list[str] = []
    if unread:
        lines.append(
            f"[{len(unread)} background action(s) LANDED after this run's last turn and were "
            f"never read: {brief_list(unread)}. Their results are NOT lost — each is recorded "
            "in the transcript as an `unread` observation — but no turn of this run saw them, "
            "so anything concluded above was concluded without them.]")
    if abandoned:
        lines.append(
            f"[{len(abandoned)} background action(s) still running at run end: "
            f"{brief_list(abandoned)}. Their results are lost — nothing in this run read them, "
            "and nothing recorded them.]")
    return "\n".join(lines)
