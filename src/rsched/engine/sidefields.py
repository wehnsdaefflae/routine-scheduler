"""The SIDE FIELDS' checks, dispatched together — `note`, `remind`, `remind_feedback`, `role_swap`.

`actions.SIDE_FIELDS` names the fields that ride EVERY kind alongside `say`, each a no-turn side
effect the engine files rather than an argument to the action. Their semantic checks used to be
dispatched one import and one call at a time from `validate_action`, which is the action
contract's single home and is over the repo's size bar — so each new side field cost that file
two lines it did not have, and the fourth (`role_swap`, D185) had nowhere to land.

They belong together for a better reason than size. A side field is defined by WHERE it rides,
not by what it does: the family shares one rule — valid on any kind, never required by any — and
`sayfields` exists precisely because a run that writes one of them as markup inside `say` loses
it silently. One module, one question: is what rode this action well-formed?

`problems` is pure, like the checkers it calls: no I/O, no engine state, problems as strings for
the schema-retry cycle, which hands them back before the action becomes a turn. `land` is the
other half — what the engine DOES with the fields of an action that was accepted.

Two side fields land elsewhere on purpose: `remind` and `remind_feedback` are applied in
`loop._tails` and `loop._finish`, AFTER the interception check, so a reminder a run authors this
turn can never hold the very action it rode on. That ordering is their contract, not an
oversight, and moving it here would break it.
"""

from __future__ import annotations

from . import notes, roleswap
from .remindfields import field_problems as _reminder_problems
from .roleswap import field_problems as _roleswap_problems
from .sayfields import problems as _say_problems


def problems(action: dict, grants=None) -> list[str]:
    """Every side field's problems, in one call.

    `grants` reaches the reminder checks alone — the consequence-reminder layer is a capability,
    and a run whose capability is off may not write one. `note` has no checker: its only
    constraint is a length the engine truncates rather than refuses (`notes.NOTE_MAX_CHARS`),
    because a note is never worth a retry.
    """
    return (_reminder_problems(action, grants)
            + _say_problems(action)        # a side field written inside `say` as markup (F679)
            + _roleswap_problems(action))  # D185: the run's own model swap


def land(loop, action: dict) -> None:
    """The side-field effects of an action that became a turn — called from `loop._land`.

    Both are best-effort by contract: a side field is free, and a failure to file one must never
    fail the turn that carried it (the transcript keeps the raw field on the action event
    either way). `note` is stamped into state/notes.md; `role_swap` re-points the `main` role, so
    the swap lands on the NEXT completion and never on the turn that asked for it.
    """
    notes.capture(loop.ctx, action)
    roleswap.apply(loop, action)
