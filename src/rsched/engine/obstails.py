"""The TAILS an observation carried, kept ON the observation — so a resumed leg reads what the
live leg read (D245).

`EngineLoop._tails` appends what rides an observation message AFTER the result (reminder note,
rule-assist line, history pointer, once-grant notice, repeat warning, budget remainder —
docs/prompt-anatomy.md §3b). Without recording them, a resumed leg's replayed prompt would differ
from what the model read: the cache misses from there on, and cautions it was shown are gone.

The RENDERED text is recorded, never the facts behind it: re-rendering would recompute the budget
against the new leg and re-apply reminder ops; verbatim is the only byte-identical form, and one
string keeps the tails' ORDER true by construction. A payload extension, never a new event type.
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
