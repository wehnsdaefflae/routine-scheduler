"""An action kind that was RENAMED tells the run its new name, instead of being refused blankly.

The JSON-Schema refusal lists every kind without pointing at the replacement, so a run whose
recipe spells the old name repeats the action until it dies. An entry stays as long as any
recipe on disk might still carry the old spelling.

Deliberately NOT an alias (D174, D241): an accepted alias keeps the ambiguous word alive.
Where the old NAME implied a field (`subtask` was a sequential child), the correction names it.
"""

from __future__ import annotations

#: Why D241 retired the three child-run kinds — one sentence shared by all three entries.
_D241 = ("a child run is ONE concept since 0.566.0 (D241): `child` starts one in either MODE "
         "(parallel, the default, or sequential) and `children` lists them — `spawn`, "
         "`subtask` and `subruns` were three names for one thing")

#: retired kind → (its replacement, why it was renamed, fields the replacement needs ADDED).
#: Keep the WHY: a run reading only the correction has no other way to know whether its recipe
#: is stale or it mistyped. The added fields are what the old NAME implied and the new kind
#: must be told — `subtask` meant a sequential child, and `child` defaults to parallel.
RENAMED: dict[str, tuple[str, str, dict[str, str]]] = {
    "task": (
        "job",
        ("the standing units of work a routine keeps for itself are JOBS since 0.416.0 (D174) "
         "— `task` was two different things, and the other one, a run's child runs, kept the "
         "word"),
        {},
    ),
    "spawn": ("child", _D241, {}),
    "subtask": ("child", _D241, {"mode": "sequential"}),
    "subruns": ("children", _D241, {}),
}


def hint(obj: dict) -> str:
    """The correction for an action naming a retired kind — "" when the kind is not one.

    A diagnosis that fires on ordinary mistakes teaches the model to ignore it.
    """
    kind = str(obj.get("kind") or "")
    entry = RENAMED.get(kind)
    if entry is None:
        return ""
    replacement, why, added = entry
    adding = "".join(f" and {field}={value!r}" for field, value in added.items())
    return (f"THE KIND WAS RENAMED: `{kind}` no longer exists — it is `{replacement}` now, and "
            f"{why}. Send the SAME action again with kind={replacement!r}{adding}; every other "
            f"field of it is unchanged. If your recipe still says {kind!r}, it is out of date: "
            f"say so in your finish summary so it gets corrected.")
