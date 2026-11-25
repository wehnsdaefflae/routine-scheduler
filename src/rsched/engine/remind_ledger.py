"""The reminder layer's two per-scope ledgers, read back from a transcript on a resumed leg.

`engine/remind.py` keeps them live: `reminder_owed` (holds of each reminder still awaiting their
ONE label) and `reminder_replayed` (finish payloads already applied — a re-emitted finish is
applied once). Rebuilt beside the hold ledger (engine/guardscope.py), so a label for a hold from
before a restart still lands.

Nothing extra is recorded: a hold is its observation and a label rides its action. The rebuild
replays the live ORDER: a non-finish action's label lands after its own observation (a hold of
that action counts first), a finish's once per payload before the gate, and an action the
reserved finish turn refused ran no side fields. `reminder_pending` is the leg's own, empty.
"""

from __future__ import annotations

from .remind import replay_key


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed both ledgers from the events inside the guard scope (engine/guardscope.py)."""
    from .chain import RESERVED_REFUSAL

    loop.reminder_replayed, loop.reminder_owed = set(), {}
    pending: tuple[object, dict] | None = None    # (turn, non-finish action) until observed
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "assistant_action":
            pending = None
            if payload.get("kind") != "finish":
                pending = (ev.get("turn"), payload)
            elif fields := [f for f in ("remind_feedback", "remind") if payload.get(f)
                            and replay_key(f, payload[f]) not in loop.reminder_replayed]:
                # field by field, as `remind.apply_ops` applied them: a re-driven finish
                # carrying the same label again counts it once
                loop.reminder_replayed.update(replay_key(f, payload[f]) for f in fields)
                if "remind_feedback" in fields:
                    _label(loop, payload)
        elif ev.get("type") == "observation" and pending and ev.get("turn") == pending[0]:
            if payload.get("kind") == "reminder_hold":
                for hit in payload.get("reminders") or []:
                    rid = str(hit.get("id") or "")
                    loop.reminder_owed[rid] = loop.reminder_owed.get(rid, 0) + 1
            if payload.get("reason") != RESERVED_REFUSAL:
                _label(loop, pending[1])
            pending = None
    loop.reminder_owed = {rid: n for rid, n in loop.reminder_owed.items() if n > 0}


def _label(loop, action: dict) -> None:
    """One label, settled the way `remind._apply_feedback` settled it: against a hold still
    owed, or not at all.
    """
    fb = action.get("remind_feedback")
    from .remindfields import feedback_entries  # one definition of "one entry, or several"

    for entry in feedback_entries(fb):
        rid = str(entry.get("id") or "") if isinstance(entry, dict) else ""
        if loop.reminder_owed.get(rid, 0) >= 1:
            loop.reminder_owed[rid] -= 1
