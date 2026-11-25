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


#: Why D288 retired the two model-call kinds — one sentence shared by both entries.
_D288 = ("the operator's resolution of 2026-11-14 was to CONVERT FULLY: reaching a model is a "
         "capability a util carries, like every other thing a run reaches outside itself, so "
         "the engine keeps one way of buying inference instead of two")

#: retired kind → (the CAPABILITY that carries it now, why it was retired). Distinct from
#: RENAMED because the replacement is not a kind: there is no action to resend, so the
#: correction cannot promise "the same action with a new name". Deliberately names the
#: capability and never a util or its flags — the standing order (goal b7) is what keeps this
#: correction true after the library changes under it.
#: What each retired kind DID, named as the capability a util carries now.
_COMPLETION = ("a scoped, stateless model completion — one prompt, one reply, no tools and"
               " no turns")
_TYPED = "a typed question put to a DECISION model, probabilities back rather than prose"

RETIRED: dict[str, tuple[str, str]] = {
    "llm": (_COMPLETION, _D288),
    "decide": (_TYPED, _D288),
}


def _retired_hint(kind: str) -> str:
    """The correction for a kind that retired into a util capability."""
    capability, why = RETIRED[kind]
    return (f"THE KIND IS RETIRED: `{kind}` is no longer an action — {capability} is a UTIL "
            f"CAPABILITY now, and {why}. Your CAPABILITIES section names the util that carries "
            f'it and `util name=list args=["<name>"]` prints its exact call form; what it '
            f"spends still books against this run's own meter, as the action's did. There is no "
            f"action to resend: make a `util` call instead. If your recipe still says "
            f"{kind!r}, it is out of date: say so in your finish summary so it gets corrected.")


def hint(obj: dict) -> str:
    """The correction for an action naming a retired kind — "" when the kind is not one.

    A diagnosis that fires on ordinary mistakes teaches the model to ignore it.
    """
    kind = str(obj.get("kind") or "")
    if kind in RETIRED:
        return _retired_hint(kind)
    entry = RENAMED.get(kind)
    if entry is None:
        return ""
    replacement, why, added = entry
    adding = "".join(f" and {field}={value!r}" for field, value in added.items())
    return (f"THE KIND WAS RENAMED: `{kind}` no longer exists — it is `{replacement}` now, and "
            f"{why}. Send the SAME action again with kind={replacement!r}{adding}; every other "
            f"field of it is unchanged. If your recipe still says {kind!r}, it is out of date: "
            f"say so in your finish summary so it gets corrected.")
