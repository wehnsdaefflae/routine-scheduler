"""The SIDE-FIELD set — the one statement of which fields ride EVERY action kind beside `say`.

A side field is a no-turn side effect the engine files rather than an argument to the action:
valid on any kind, required by none (`engine/sidefields.py` checks and lands them). Every reader
derives from the tuples here — the validator's allowed set (`actions`), the schema projection
(`kindsurface`), the prompt's field index (`schemaindex`), the narration-blind comparison of two
actions (`replyactions`) and the markup check on `say` (`sayfields`) — so a new side field is
one line here rather than one per reader. Import-free on purpose: `sayfields` is pure and must
not import the action contract.
"""

from __future__ import annotations

#: The two that ride every kind only while the `reminders` capability is on.
REMINDER_FIELDS = ("remind", "remind_feedback")
#: The side fields the engine FILES — a note, a reminder, a label on one. Each is ABOUT the
#: action, so two actions differing only in these are one action written twice.
FILED_FIELDS = ("note", *REMINDER_FIELDS)
#: …plus `role_swap`, the one that STEERS: which model answers the next turn (D185).
SIDE_FIELDS = (*FILED_FIELDS, "role_swap")
