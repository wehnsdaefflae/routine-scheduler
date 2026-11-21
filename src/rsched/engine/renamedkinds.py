"""An action kind that was RENAMED tells the run its new name, instead of being refused blankly.

The JSON-Schema refusal lists every kind without pointing at the replacement, so a run whose
recipe spells the old name repeats the action until it dies. An entry stays as long as any
recipe on disk might still carry the old spelling.

Deliberately NOT an alias (D174): an accepted alias keeps the ambiguous word alive.
"""

from __future__ import annotations

#: retired kind → (its replacement, why it was renamed). Keep the WHY: it tells the run whether
#: its recipe is stale or it mistyped.
RENAMED: dict[str, tuple[str, str]] = {
    "task": (
        "job",
        ("the standing units of work a routine keeps for itself are JOBS since 0.416.0 (D174) "
         "— `task` was two different things, and the other one, a run's sequential subtasks and "
         "parallel sub-runs, kept the word"),
    ),
}


def hint(obj: dict) -> str:
    """The correction for an action naming a retired kind — "" when the kind is not one.

    A diagnosis that fires on ordinary mistakes teaches the model to ignore it.
    """
    kind = str(obj.get("kind") or "")
    entry = RENAMED.get(kind)
    if entry is None:
        return ""
    replacement, why = entry
    return (f"THE KIND WAS RENAMED: `{kind}` no longer exists — it is `{replacement}` now, and "
            f"{why}. Send the SAME action again with kind={replacement!r}; every other field of "
            f"it is unchanged. If your recipe still says {kind!r}, it is out of date: say so in "
            f"your finish summary so it gets corrected.")
