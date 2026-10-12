"""An action kind that was RENAMED tells the run its new name, instead of being refused blankly.

A kind leaving the schema is rare and the refusal it produces is accurate but useless: the
JSON-Schema message is `kind: 'task' is not one of [...]` followed by all 33 current kinds, with
the replacement sitting somewhere in the middle of the list. A run whose RECIPE still spells the
old name reads that, cannot see which of the 33 it wanted, and emits the same action again — and
three rejections of one correctable mistake is what killed a run in F637.

So the mapping is explicit and the correction says the one thing that fixes it. The entry stays
as long as any recipe on disk might still carry the old spelling; recipes are the operator's,
rewritten at his pace, not in the release that renames the kind.

Deliberately NOT an alias. D174 chose "rename both senses, zero residual ambiguity" (`task` meant
the run's decomposition tree in one place and a routine's own standing work in another), and an
accepted alias would keep the ambiguous word alive in the surface it was removed from. The run is
told the new name once and uses it.
"""

from __future__ import annotations

#: retired kind → (its replacement, why it was renamed). Keep the WHY: a run reading only the
#: correction has no other way to know whether its recipe is stale or it mistyped.
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

    Returns "" for everything else, because a diagnosis that fires on ordinary mistakes teaches
    the model to ignore it (`actions.field_shift_diagnosis` says the same of its own signature).
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
