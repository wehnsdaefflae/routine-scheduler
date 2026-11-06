"""The outcome label for a RULE ASSIST's hold — `remind_feedback` routed by the shape of its id.

One side field carries both layers' labels, and that is deliberate: the two are the authorship
faces of one relevance-trigger layer (`engine/hold.py`), a label rides any action at no turn
cost, and a second field would mean teaching the model which of two spellings to use for the
same act. So the ROUTING is a property of the id, not of a new field: a reminder id cannot
contain `/` (`reminders.ID_RE`) and an assist key always does — it is `<rule>/<assist id>`
(`assists.Assist.key`). An id of neither shape keeps the reminder layer's refusal, which names
the ids the hold offered.

Before this, `remind._apply_feedback` answered an assist key with "no reminder … is live for
this run": the label had nowhere to go, and the one `hold`-payload assist in the library had
bought 72 turns across three routines with no way to tell a useful hold from a false positive
(D239).

The reminder layer's two rules are mirrored here rather than reinvented, because they are
about EVIDENCE and not about reminders:

- **a label answers one hold, once.** `loop.assist_owed` counts the holds of each assist this
  run has not yet labelled, the way `loop.reminder_owed` does — a label with no hold behind it
  is evidence about nothing (one run once labelled a single reminder 75 times against 5 fires).
- **the tally is disk-owned.** `assist_stats.record` is the one writer, off disk, so a fire
  counted at the hold and a label recorded two turns later cannot overwrite each other.

The ledger is this layer's own run state, carried across a resume beside the hold ledger it
pairs with (`engine/assist.py` seeds it, its `rebuild` re-seeds it from the holds the
transcript recorded) — a label for a hold from before a restart lands.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import assist_stats
from ..reminders import LABELS


def is_assist_key(rid: object) -> bool:
    """Is this id an ASSIST key rather than a reminder id? The shape decides: `<rule>/<id>`."""
    return isinstance(rid, str) and "/" in rid


def rebuild_owed(loop, events: list[dict]) -> None:
    """Re-seed `loop.assist_owed` from the events inside the guard scope (engine/guardscope.py).

    Nothing extra is recorded for either half: a hold IS its observation (which names the
    assists it spent) and a label rides its action. An assist fires at most once per run
    (`assist._fire`), so one key is owed at most one label and the live turn's inner order —
    the hold seam, then `remind.apply_ops` for the same action — cannot change the count the
    way it can for a reminder, whose holds repeat (engine/remind_ledger).
    """
    loop.assist_owed = {}
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "assistant_action":
            fb = payload.get("remind_feedback")
            rid = str(fb.get("id") or "") if isinstance(fb, dict) else ""
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
