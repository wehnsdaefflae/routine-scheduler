"""The rejection this RUN has already earned — so the correction teaches and the retry is not
the identical ask (F736).

A rejected reply and its correction are dropped as debris once the turn recovers
(`engine/turndebris.py`), so the same mistake recurs ACROSS turns against a prompt that no
longer mentions it — which `completion.next_action`'s in-turn `prev_raw` check cannot see.

The refusal here is of the ASK, never of the run: ending a run at a second identical rejection
would discard work that recovers on the next attempt. Two things change, inside the existing
attempt budget:

1. The correction NAMES THE FIELD: `field_help` quotes the schema's own description, so it
   cannot drift from the contract it corrects against.
2. On a repeat the correction says AT WHICH TURN it was first earned, and the caller drops the
   provider grammar for that retry (a constrained decoder minting the same invalid object twice
   is its likeliest author). No extra attempt, no model switch, no effort change.

THE MEMO IS PER RUN AND IN MEMORY, keyed on the rejection MESSAGE rather than the raw reply
(different replies earn one identical message; a model that fixes one field and breaks another
has learned something). A resumed leg starts empty, deliberately: its prompt no longer shows the
old mistakes. This module reads the action contract and composes prose; its only write is its
own memo dict.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .actionschema import ACTION_SCHEMA

#: How much of a field's own description the correction quotes: its first sentence, capped, so
#: a paragraph-long description does not bury the problem lines.
HELP_CHARS = 260

#: At most this many fields are explained in one correction; listing every shifted field
#: teaches nothing.
MAX_FIELDS = 2

#: The shapes a problem line names a FIELD in (the validator's and the per-kind checker's).
#: Deliberately NOT a scan for any property-name word: a `kind: … is not one of [...]` line
#: carries the whole KINDS enum, several of which are also field names.
_FIELD_PATTERNS = (
    re.compile(r"'([a-z_][a-z0-9_]*)' is a required property"),
    re.compile(r"^([a-z_][a-z0-9_]*)(?:\.\d+)*:"),
    re.compile(r"'([a-z_][a-z0-9_]*)'(?: field)? is valid only together with"),
    re.compile(r"requires a non-empty '([a-z_][a-z0-9_]*)'"),
)

#: The rejection an UNPARSEABLE reply earns. It names no field, so the correction teaches the
#: contract's required fields instead.
_UNPARSEABLE = "reply did not contain a parseable JSON object"


def _described(field: str) -> str:
    """The contract's OWN first sentence about one field, or "" when it carries none.

    Read out of `ACTION_SCHEMA` at call time rather than copied. `kind` has none (an enum).
    """
    prop = ACTION_SCHEMA.get("properties", {}).get(field)
    text = str((prop or {}).get("description") or "").strip()
    if not text:
        return ""
    head = text.split(". ")[0].rstrip(".")
    return head if len(head) <= HELP_CHARS else head[: HELP_CHARS - 1].rstrip() + "…"


def fields_named(problems: Sequence[str]) -> list[str]:
    """The fields these problem lines are ABOUT, in the order they first appear."""
    found: list[str] = []
    for problem in problems:
        line = str(problem).strip()
        for pattern in _FIELD_PATTERNS:
            if (hit := pattern.search(line)) and hit.group(1) not in found:
                if hit.group(1) in ACTION_SCHEMA.get("properties", {}):
                    found.append(hit.group(1))
                break
    return found


def field_help(problems: Sequence[str]) -> str:
    """What the named fields ARE, in the contract's own words — "" when nothing can be said."""
    if any(_UNPARSEABLE in str(p) for p in problems):
        required = [f for f in ACTION_SCHEMA.get("required", []) if _described(f)]
        if not required:
            return ""
        lines = "\n".join(f"- `{f}`: {_described(f)}" for f in required)
        return ("An action is ONE JSON object. Every action needs these fields, whatever its "
                f"kind:\n{lines}")
    named = fields_named(problems)[:MAX_FIELDS]
    helps = [(f, _described(f)) for f in named]
    helps = [(f, d) for f, d in helps if d]
    if not helps:
        return ""
    lines = "\n".join(f"- `{f}`: {d}" for f, d in helps)
    plural = len(helps) > 1
    head = f"What the field{'s' if plural else ''} above {'are' if plural else 'is'} for:"
    return f"{head}\n{lines}"


def _memo(loop) -> dict[str, int]:
    """This run's rejection memo — message → the turn it was FIRST earned at (lazily held)."""
    memo: dict[str, int] | None = getattr(loop, "_schema_memo", None)
    if memo is None:
        memo = {}
        loop._schema_memo = memo
    return memo


def recall(loop, problems: Sequence[str], *, turn: int) -> str:
    """Record this rejection and return what to tell the model if the run already earned it.

    "" the first time a message is seen. On a repeat the sentence names the EARLIER turn, which
    the model can check against its own context. Mutates the memo, so call it ONCE per
    rejection. Never raises: a correction that cannot be composed must not cost the turn.
    """
    try:
        key = "; ".join(str(p).strip() for p in problems)[:1000]
        if not key:
            return ""
        memo = _memo(loop)
        first = memo.get(key)
        if first is None:
            memo[key] = turn
            return ""
        # Cite only what the correction actually carries: `field_help` is empty for a field
        # with no description (`kind`).
        basis = "the field list above" if field_help(problems) else "the problem lines above"
        return (f"You have ALREADY EARNED THIS IDENTICAL REJECTION in this run, at turn {first}"
                f"{'' if first == turn else f' (this is turn {turn})'} — the same problem "
                "lines, word for word. The correction you were given then did not work, so "
                "repeating that reply with a small edit will not work either: build the action "
                f"again from {basis}, and if you cannot satisfy it, choose a "
                "DIFFERENT action you can satisfy (and say why in `say`) rather than sending a "
                "third attempt at this one. The schema constraint has been dropped for your "
                "next reply, so emit the whole object yourself.")
    except Exception:
        return ""
