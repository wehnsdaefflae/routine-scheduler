"""The reminder layer's two per-scope ledgers, read back from a transcript on a resumed leg.

`engine/remind.py` keeps them live: `reminder_owed` (how many holds of each reminder still wait
for their ONE label — a label answers one hold, once) and `reminder_replayed` (the finish
payloads already applied — a finish the gate hands back is re-emitted with the same side
fields, and is applied once). Both follow the hold ledger they pair with across a resume
(engine/guardscope.py): rebuilt beside it, a label for a hold from before a restart lands,
where an emptied ledger refused it for want of a hold "in this run" — while the hold itself,
already confirmed, is not repeated to make up for it.

Nothing extra is recorded for either: a hold is its observation and a label rides its action.
So the rebuild replays the order the live turn applied them in, which is the whole of this
module's care: a non-finish action's label lands after its own observation (a hold of that very
action counts first — `EngineLoop._observe` asks the hold seam, then `remind.apply_ops`), a
finish's lands once per payload, before the gate, and an action the reserved finish turn
refused ran no side fields at all. What the label nudge still owes (`reminder_pending`) is the
leg's own and starts empty.
"""

from __future__ import annotations

from .remind import replay_key


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed both ledgers from the events inside the guard scope (engine/guardscope.py)."""
    from .loop import RESERVED_REFUSAL

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
    rid = str(fb.get("id") or "") if isinstance(fb, dict) else ""
    if loop.reminder_owed.get(rid, 0) >= 1:
        loop.reminder_owed[rid] -= 1
