"""The TAILS an observation carried, kept ON the observation — so a resumed leg reads what the
live leg read (D245).

`EngineLoop._tails` appends everything that rides an observation message AFTER the result
itself: the reminder note, the rule-assist line, the archived-history pointer, the spent
once-grant notice, the repeat warning, the budget remainder (docs/prompt-anatomy.md §3b). The
observation EVENT was written before any of them existed, so `history.replay_messages` rebuilt
the message WITHOUT them: from the first such turn a resumed leg's prompt differed from what the
model had actually read. Two costs, both already counted in this codebase: the provider cache
misses from that message on — re-WRITTEN at 1.25x instead of re-read at 0.1x, which is exactly
what `engine/enginenote.py` was written for — and cautions the model was shown are simply gone
from the leg that continues its work.

The RENDERED text is what is recorded, never the facts behind it. Re-rendering on a resume would
recompute the budget remainder against the new leg's window and re-apply reminder ops that
already ran; verbatim is the only thing that can be byte-identical. One string also keeps the
ORDER of several tails on one message true by construction — there is no second renderer that
could disagree with `_tails` about it.

A payload EXTENSION on the event that carried them, never a new event type — the rule
`engine/guardscope.py` states for the once-only guards, and the one `assist.recorded`
(`assists`, `undo_point`) and `engine/replyactions.py` (`not_executed`) already follow.
"""

from __future__ import annotations

#: The observation payload key. `assists`, `undo_point` and `not_executed` are its siblings.
KEY = "tails"


def record(payload: dict, tails: str) -> dict:
    """The observation as the transcript keeps it, with the tails that rode its message.

    The payload itself when nothing rode it, so an observation with no tail is recorded in
    exactly the shape every existing reader already knows.
    """
    return {**payload, KEY: tails} if tails else payload


def of(payload: dict) -> str:
    """The tails recorded on an observation payload — "" for one that carried none, and for
    every observation recorded before this key existed (a run resumed across the change).
    """
    value = payload.get(KEY)
    return value if isinstance(value, str) else ""
