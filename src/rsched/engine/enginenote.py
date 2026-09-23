"""ENGINE NOTES — the one seam that puts an engine-authored message into a live run.

The engine speaks to a run in exactly one voice: a user-role message opening `ENGINE NOTE:`.
Nine seams used to build that message by hand — a model switch, a deliberation switch, a live
config change, a rule addition or drop, a rule assist, the pre-eviction warning, the archive
announcement, the boot setup-gap list — each appending the full prose to `loop.messages` and
recording a SHORT STUB beside it (`[engine] compaction imminent`, `[engine] setup gaps at boot`).

The stub is what broke on resume. `history.replay_messages` had no notion of an engine note at
all and rendered every `user_injection` as "USER MESSAGE (injected mid-run)", so a resumed
conversation read ~380 phantom user messages across 21 days — each one both MISLABELLED (the
harness contract tells the model an injected message is the user talking) and LOSSY (the stub
replaced the gap list, the compaction warning, the archive pointer the model had actually read).
A second resume then replayed the stub where the previous leg carried the prose, so the prompt
prefix differed between legs and the provider cache was rewritten from the message list on.

So: one function writes both halves from ONE string, and replay renders it back verbatim.
"""

from __future__ import annotations


def append(loop, note: str, *, replay: bool = True) -> None:
    """Append one ENGINE NOTE to the live prompt and record it verbatim in the transcript.

    `replay=False` marks a note the resume path RE-AUTHORS for itself — the "this run was
    interrupted and is now resumed" and "the user continued the conversation" notes, which
    `boot` writes afresh on every leg. Replaying them too would stack one copy per leg.
    """
    loop.ctx.transcript.event("user_injection", {
        "text": note, "source": "engine", **({} if replay else {"replay": False})})
    loop.messages.append({"role": "user", "content": message(note)})


def message(note: str) -> str:
    """The live message body for a note — the one form a replay has to reproduce."""
    return f"ENGINE NOTE: {note}"
