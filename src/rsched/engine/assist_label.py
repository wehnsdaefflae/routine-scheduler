"""The outcome label for a RULE ASSIST's hold — `remind_feedback` routed by the shape of its id.

One side field carries both layers' labels (D239) — they are two faces of one relevance-trigger
layer (`engine/hold.py`), and a second field would mean two spellings for the same act. So the
ROUTING is a property of the id: a reminder id cannot contain `/` (`reminders.ID_RE`) and an
assist key always does (`<rule>/<assist id>`, `assists.Assist.key`). An id of neither shape
keeps the reminder layer's refusal.

The reminder layer's two rules about EVIDENCE are mirrored here:

- **a label answers one hold, once.** `loop.assist_owed` counts the holds not yet labelled, as
  `loop.reminder_owed` does — a label with no hold behind it is evidence about nothing.
- **the tally is disk-owned.** `assist_stats.record` is the one writer, off disk, so a fire and
  a later label cannot overwrite each other.

The ledger is carried across a resume beside the hold ledger it pairs with (`rebuild_owed`),
so a label for a hold from before a restart lands.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import assist_stats
from ..reminders import LABELS
from .remindfields import feedback_entries


def is_assist_key(rid: object) -> bool:
    """Is this id an ASSIST key rather than a reminder id? The shape decides: `<rule>/<id>`."""
    return isinstance(rid, str) and "/" in rid


def rebuild_owed(loop, events: list[dict]) -> None:
    """Re-seed `loop.assist_owed` from the events inside the guard scope (engine/guardscope.py).

    A hold IS its observation (naming the assists it spent) and a label rides its action. An
    assist fires at most once per run, so — unlike a reminder (engine/remind_ledger) — the live
    turn's inner order cannot change the count.
    """
    loop.assist_owed = {}
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "assistant_action":
            fb = payload.get("remind_feedback")
            for entry in feedback_entries(fb):                  # one or several (F925)
                rid = str(entry.get("id") or "") if isinstance(entry, dict) else ""
                if loop.assist_owed.get(rid, 0) >= 1:
                    loop.assist_owed[rid] -= 1
        elif ev.get("type") == "observation" and payload.get("kind") == "assist_hold":
            for key in payload.get("assists") or []:
                loop.assist_owed[str(key)] = loop.assist_owed.get(str(key), 0) + 1
    loop.assist_owed = {key: n for key, n in loop.assist_owed.items() if n > 0}


def apply(loop, fb: dict, reminder_path: Callable[[object, dict], str]) -> str:
    """Record one label — against a hold of this assist, in this run, not labelled yet.

    `reminder_path` is what an id of the OTHER shape is handed to, unchanged: every label a
    reminder has ever recorded goes through it, and this routing may not alter that path.
    """
    rid = str(fb.get("id") or "")
    if not is_assist_key(rid):
        return reminder_path(loop, fb)
    label = str(fb.get("label") or "")
    owed = getattr(loop, "assist_owed", None)
    if label not in LABELS:
        return (f"{label!r} is not one of {list(LABELS)}, so nothing was recorded for {rid} — "
                "the labels are in the hold you are answering")
    if not owed or owed.get(rid, 0) < 1:
        return (f"no hold of {rid} in this run is waiting for a label, so the {label!r} label "
                "was not recorded — a label answers one hold, once")
    owed[rid] -= 1
    stats = assist_stats.record(loop.ctx.routine.dir, rid, label)
    return f"{rid} labelled {label} ({assist_stats.summary(stats)})"
