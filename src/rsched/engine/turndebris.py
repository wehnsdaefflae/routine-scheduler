"""THIS turn's debris — the messages that exist only to elicit the turn's action.

A schema retry's rejected reply and its correction, a refused finish and the note that
re-drives the turn, a failover notice: each earns its keep eliciting THIS reply and would
otherwise be re-read on every remaining turn, so `completion.next_action` drops them once the
turn's action is accepted (the transcript's error events keep the record).

They are remembered BY IDENTITY, never by a list length: a mid-turn model switch can compact
and rebuild the list. Holding the debris itself also keeps each id unique while compared.
"""

from __future__ import annotations


def new_turn(loop) -> None:
    """Start the turn about to be asked for with nothing to drop."""
    loop._turn_debris = []


def append(loop, *messages: dict) -> None:
    """Append messages to the live prompt that the turn's accepted action makes moot."""
    loop.messages.extend(messages)
    loop._turn_debris.extend(messages)


def head_index(loop) -> int | None:
    """Where THIS turn's debris begins in the live prompt, or None while there is none.

    An engine note the retry loop posts must land BEFORE the debris: `schemamemo` reads the
    correction as the LAST user message. Found by identity, like `drop`.
    """
    debris = getattr(loop, "_turn_debris", None) or []
    for position, message in enumerate(loop.messages):
        if any(message is held for held in debris):
            return position
    return None


def drop(loop) -> None:
    """Remove this turn's debris from the live prompt, wherever compaction has left it."""
    if loop._turn_debris:
        gone = {id(m) for m in loop._turn_debris}
        loop.messages[:] = [m for m in loop.messages if id(m) not in gone]
    loop._turn_debris = []
