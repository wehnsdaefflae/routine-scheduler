"""An action kind that was RENAMED tells the run its new name, instead of being refused blankly.

A kind leaving the schema is rare and the refusal it produces is accurate but useless: the
JSON-Schema message is `kind: 'task' is not one of [...]` followed by every current kind, with
the replacement sitting somewhere in the middle of the list. A run whose RECIPE still spells the
old name reads that, cannot see which of them it wanted, and emits the same action again — and
three rejections of one correctable mistake is what killed a run in F637.

So the mapping is explicit and the correction says the one thing that fixes it. The entry stays
as long as any recipe on disk might still carry the old spelling; recipes are the operator's,
rewritten at his pace, not in the release that renames the kind.

Deliberately NOT an alias. D174 chose "rename both senses, zero residual ambiguity" (`task` meant
the run's decomposition tree in one place and a routine's own standing work in another), and an
accepted alias would keep the ambiguous word alive in the surface it was removed from. The run is
told the new name once and uses it. D241 (`spawn`/`subtask`/`subruns` → `child`/`children`)
is the same decision and needs the same entries — 0.566.0 removed the kinds without them, so a
recipe the boot migration had not reached got exactly the blank refusal this module prevents.
Where the old NAME carried meaning the new kind takes as a FIELD (`subtask` was a sequential
child; `child` defaults to parallel), the correction names that field too, or "every other
field is unchanged" would silently turn a sequential child into a parallel one.
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

    Returns "" for everything else, because a diagnosis that fires on ordinary mistakes teaches
    the model to ignore it (`actions.field_shift_diagnosis` says the same of its own signature).
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
