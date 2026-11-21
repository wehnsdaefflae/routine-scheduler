"""The run SUMMARY's account of background calls nobody read (F715, authorized by D195).

A run may background a read or a fetch and get its turn back at once. Two of those calls never
reach the model: one still RUNNING when the run ends, whose result is lost, and one that LANDED
after the last turn boundary, whose result exists and sits in the transcript unread.
`engine/background.settle` records both as observation events; this module turns them into the
line `engine/loopend.finish_run` appends to the summary — the one surface the operator and the
next run actually read.

**The landed case speaks too**: the run concluded without evidence it was never shown, and a
run cannot flag what it never saw (D195), so the engine has to.

**Two sentences, never one.** The abandoned call's answer is GONE (re-run to have it); the
landed one's EXISTS in a transcript row and can simply be read. One line would erase the
difference a reader acts on.
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

    The single source for this phrasing (the `kill` refusal and the run-end summary): a handle
    written two ways reads as two different calls.
    """
    return "; ".join(f"`{c.handle}` ({c.kind}: {c.brief})" for c in calls)


def settle_note(*, unread: Sequence[SettledCall],
                abandoned: Sequence[SettledCall]) -> str:
    """The summary's account of what this run never read — "" when it read everything.

    Empty is the ordinary case and must stay empty — a bracket on every summary is one readers
    learn to skip. Both halves may appear; the LANDED set goes first, being actionable without a
    re-run.
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
