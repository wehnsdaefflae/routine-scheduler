"""The SIDE FIELDS' checks, dispatched together — `note`, `remind`, `remind_feedback`, `role_swap`.

`actions.SIDE_FIELDS` names the fields that ride EVERY kind alongside `say`, each a no-turn side
effect the engine files rather than an argument to the action — valid on any kind, required by
none. `problems` is pure (no I/O, problems as strings for the schema-retry cycle); `land` is what
the engine DOES with an accepted action's fields.

`remind` and `remind_feedback` land elsewhere on purpose: in `loop._tails` / `loop._finish`,
AFTER the interception check, so a reminder authored this turn never holds the action it rode on.
"""

from __future__ import annotations

from . import autoswap, notes, roleswap
from .remindfields import field_problems as _reminder_problems
from .roleswap import field_problems as _roleswap_problems
from .sayfields import problems as _say_problems


def problems(action: dict, grants=None) -> list[str]:
    """Every side field's problems, in one call.

    `grants` reaches the reminder checks alone (a capability). `note` has no checker: its
    length is truncated, never refused (`notes.NOTE_MAX_CHARS`).
    """
    return (_reminder_problems(action, grants)
            + _say_problems(action)        # a side field written inside `say` as markup (F679)
            + _roleswap_problems(action))  # D185: the run's own model swap


def land(loop, action: dict) -> None:
    """The side-field effects of an action that became a turn — called from `loop._land`.

    Best-effort: a failure to file a side field never fails the turn (the transcript keeps the
    raw field). `note` is stamped into state/notes.md; `role_swap` re-points the `main` role for
    the NEXT completion. `autoswap` (the engine's own half of that slot) runs AFTER it and stands
    down for the rest of the run once an action carries `role_swap`.
    """
    notes.capture(loop.ctx, action)
    roleswap.apply(loop, action)
    autoswap.at_landing(loop, action)   # F785: a REPEAT POLL is served cheap, unasked
