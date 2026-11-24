"""What RIDES an observation message, in the order the model reads it (prompt-anatomy §3b).

Split out of `engine/loop.py`, which is over the house size bar and may only shrink: composing
the tail is a responsibility of its own — every layer that wants to reach the model without
spending a turn appends here, and the ORDER is prompt surface. Keeping it beside the turn state
machine meant each new layer grew the loop by a line, which is exactly how the loop got over
the bar.

Each tail is FREE: it rides a message the run was getting anyway, so it costs no turn. That is
also the constraint on adding one — the tail is scarce attention shared with the budget
remainder and the rule assists, and a layer that fires on every turn spends what the others
need. A new tail belongs here only if it can say when it is NOT worth firing.
"""

from __future__ import annotations

from typing import Any

from ..assists import Assist
from ..playground import runtime as playground_runtime
from . import assist, loopshape, recall, remind, requests, turnwindow

#: Identical consecutive actions before the warning tail appears, and before the run fails.
#: Mirrored from `loop` to keep this module importable on its own.
REPEAT_WARN = 3
REPEAT_FAIL = 5

#: Turns between repeats of the history pointer once an archive exists.
HISTORY_NOTE_EVERY = 10


def compose(loop: Any, action: dict, obs: dict, fired: list[Assist], *,
            held: bool, streak: int, poll_s: float) -> str:
    """Everything that rides THIS observation. "" when nothing applies."""
    # `remind` side fields, applied AFTER the hold check so a reminder authored this turn
    # can never hold the action it rode on.
    text = remind.apply_ops(loop, action, poll_s=poll_s)
    # Reminders on non-action triggers (`result`, `prose`, `turn:*`) cannot hold; they ride.
    text += remind.at_observation(loop, action, obs)
    text += assist.tail(fired)   # observation-moment assists, asked in `_observe`
    text += recall.at_observation(loop, action, obs)   # an overlapping archived topic
    # The PLAYGROUND's experimental helper layer — and only for a conversation that carries
    # `playground: true`. It returns "" for every production run, so this line cannot change
    # what a routine is shown, and it never raises (its own guard).
    text += playground_runtime.at_observation(loop, action, obs)
    # D65: an `allow once` grant is spent by USE, not attempt — a HELD action never reached
    # the executor, and re-emitting it is the confirmation to proceed.
    if not held and (spent := requests.consume_once_grants(loop, action, obs)):
        text += requests.spent_notice(spent, action)
    if REPEAT_WARN <= streak < REPEAT_FAIL:
        text += repeat_warning(loop, streak)
    text += loopshape.after(loop, action, obs)   # a cycle, varied failures, a same result
    if warning := loop.ctx.budget_warning():
        text += turnwindow.converge(warning)
    # D227: what is LEFT, as a fact — here, since the contract is composed once.
    text += turnwindow.budget_tail(loop.ctx)
    if loop._history_active:
        loop._hist_note_countdown -= 1
        if loop._hist_note_countdown <= 0:
            text += loop._history_note
            loop._hist_note_countdown = HISTORY_NOTE_EVERY
    return text


def repeat_warning(loop: Any, streak: int) -> str:
    """The repeat-streak tail, and its escape hatch: the next reply runs schema-free; after
    two sheds the provider schema stays off for the rest of the run.
    """
    loop._shed_schema_turns = 1   # re-arms on every further repeat
    loop._sheds += 1
    if loop._sheds >= 2 and not loop._schema_off:
        loop._schema_off = True
        loop.ctx.transcript.event("error", {
            "where": "schema", "attempt": 0,
            "message": "provider response-format disabled for the rest of the "
                       "run: repeat-streak shedding rescued it twice — the "
                       "grammar is suppressing fields for this model"})
    return (f"\n[ENGINE WARNING: this exact action has now run "
            f"{streak} times in a row — {REPEAT_FAIL} identical "
            "actions fail the run. Change course. The structured-output "
            "constraint is lifted for your next reply: emit ONE JSON object "
            "and include every field the action needs (args, content, …).]")
