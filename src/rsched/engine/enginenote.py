"""ENGINE NOTES — the one seam that puts an engine-authored message into a live run.

The engine speaks to a run in exactly one voice: a user-role message opening `ENGINE NOTE:`.
One function writes both halves — the live message and the transcript record — from ONE string,
and replay renders it back verbatim: a note must never replay as the user speaking, nor as a
shorter stub, or a resumed prefix differs from the live one and the provider cache is rewritten.
"""

from __future__ import annotations


def append(loop, note: str, *, replay: bool = True, before: int | None = None,
           **marks) -> None:
    """Append one ENGINE NOTE to the live prompt and record it verbatim in the transcript.

    `before` inserts at that index instead of appending — for the one seam that must not become
    the last message: a note posted inside the schema-retry loop, where the correction is what
    the next reply answers (`engine/turndebris.head_index`). The transcript record is unchanged;
    only the live prompt's order is.

    `replay=False` marks a note `boot` RE-AUTHORS on every leg (the resume framings); replaying
    it too would stack one copy per leg.

    `marks` ride the RECORD only: payload keys naming the once-only guard a note spent (a rule
    assist's `assists`, the eviction warning's `evict_warning`), which a resumed leg rebuilds
    that guard from (engine/guardscope.py). The note's text is the model's, and never parsed.
    """
    loop.ctx.transcript.event("user_injection", {
        "text": note, "source": "engine", **({} if replay else {"replay": False}), **marks})
    body = {"role": "user", "content": message(note)}
    if before is None:
        loop.messages.append(body)
    else:
        loop.messages.insert(before, body)


def message(note: str) -> str:
    """The live message body for a note — the one form a replay has to reproduce."""
    return f"ENGINE NOTE: {note}"
