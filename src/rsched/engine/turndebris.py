"""THIS turn's debris — the messages that exist only to elicit the turn's action.

A schema retry's rejected reply and its correction, a refused finish and the note that
re-drives the turn, a failover notice: each earns its keep eliciting THIS reply and would
otherwise be re-read on every remaining turn, so `completion.next_action` drops them once the
turn's action is accepted (the transcript's error events keep the record).

They are remembered BY IDENTITY. The cleanup used to cut the list at a length taken at the
top of the turn — but a model switch mid-turn re-fits the prompt to the new model's window
(`completion._adopt_model`, and the oversize net in `overflow`), and a compaction there
rebuilds the list. The length then named nothing: debris that survived into the compacted
tail rode along for the rest of the run, and an ENGINE NOTE that compaction appended past the
old length (the eviction warning) was cut from the live prompt while the transcript — and so
every resume — kept it. Holding the debris itself also keeps each id unique while it is
compared: a message object cannot be freed and its id reused while this list refers to it.
"""

from __future__ import annotations


def new_turn(loop) -> None:
    """Start the turn about to be asked for with nothing to drop."""
    loop._turn_debris = []


def append(loop, *messages: dict) -> None:
    """Append messages to the live prompt that the turn's accepted action makes moot."""
    loop.messages.extend(messages)
    loop._turn_debris.extend(messages)


def drop(loop) -> None:
    """Remove this turn's debris from the live prompt, wherever compaction has left it."""
    if loop._turn_debris:
        gone = {id(m) for m in loop._turn_debris}
        loop.messages[:] = [m for m in loop.messages if id(m) not in gone]
    loop._turn_debris = []
