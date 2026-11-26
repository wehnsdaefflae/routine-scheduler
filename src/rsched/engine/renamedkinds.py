"""An action kind that RETIRED tells the run what carries it now, instead of being refused blankly.

The JSON-Schema refusal lists every kind without pointing at the replacement, so a run whose
recipe spells a kind that no longer exists repeats the action until it dies. The correction is a
diagnosis, never an alias (D174, D241, D288): an accepted alias keeps the dead word alive, which
is also why a register entry is not kept once nothing on disk spells it.

`RETIRED` is the one register here: a kind whose replacement is a UTIL CAPABILITY, so there is
no action to resend. The RENAME register beside it is gone — its kinds (`task`, `spawn`,
`subtask`, `subruns`) are spelled by no recipe, library pattern or seed, so it corrected a
mistake nobody was making.
"""

from __future__ import annotations

#: Why D288 retired the two model-call kinds — one sentence shared by both entries.
_D288 = ("the operator's resolution of 2026-11-14 was to CONVERT FULLY: reaching a model is a "
         "capability a util carries, like every other thing a run reaches outside itself, so "
         "the engine keeps one way of buying inference instead of two")

#: retired kind → (the CAPABILITY that carries it now, why it was retired). The replacement is
#: not a kind: there is no action to resend, so the correction never promises "the same action
#: with a new name". Deliberately names the capability and never a util or its flags — the
#: standing order (goal b7) is what keeps this correction true after the library changes under
#: it.
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
    return _retired_hint(kind) if kind in RETIRED else ""
