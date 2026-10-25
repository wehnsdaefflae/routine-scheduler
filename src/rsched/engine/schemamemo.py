"""The rejection this RUN has already earned — so the correction teaches and the retry is not
the identical ask (F736).

`completion.next_action` detects a repeat with `prev_raw`, a local that dies with the turn. So a
repeat is only ever seen INSIDE one turn — and the real phenomenon is ACROSS turns, because the
rejected reply and its correction are dropped as debris the moment the turn recovers
(`engine/turndebris.py`), leaving the next turn to make the same mistake against a prompt that
no longer mentions it.

MEASURED on this instance's health stream (76 `wrong_turn_detected` rows, 2026-10-19..24):

* `repeat_invalid` — the engine's only repeat detector — has fired ZERO times.
* TEN runs earned a BYTE-IDENTICAL rejection message on two or more SEPARATE turns; 25 turns
  in all. `llmsectest-weekday:20261023-040002` was told `(root): 'say' is a required property`
  at turns 236, 287, 315, 331, 349, 483, 592, 613, 647, 662 and 683.
* NINE of 37 runs earn their FIRST rejection at turn 1, where no earlier turn exists to learn
  from, so the first correction has to teach on its own.

WHAT "REFUSES A SECOND IDENTICAL REJECTION" MUST NOT MEAN. Those eleven turns each RECOVERED on
the next attempt. A rule that ended the run at the second identical rejection would have killed
that run at turn 287 of 683 and discarded 396 turns of finished work — strictly worse than the
retries it saves, and the measured outcome it would have produced is the one specimen this item
was raised about (a child run that died on schema rejections and left no finish at all). So the
refusal here is of the ASK, never of the run: the retry stops being a byte-identical re-send.

TWO THINGS CHANGE, both inside the attempt budget that already existed:

1. The correction NAMES THE FIELD. `(root): 'say' is a required property` is the validator's
   sentence about a property NAME; the contract carries a description of what that field IS and
   the model never saw it. `field_help` quotes the schema's own words — one source, so the
   correction cannot drift from the contract it is correcting against.
2. The correction says the rejection was already earned, AT WHICH TURN — and the caller drops
   the provider grammar for that retry. A constrained decoder that has now minted the same
   invalid object twice is the likeliest author of it (an empty-string debris field and a
   missing required key are its signature), and dropping the grammar is a lever the engine
   already trusts; it merely spent it on the last attempt of a turn rather than on the evidence
   that the ask does not work. No extra attempt, no model switch, no effort change.

THE MEMO IS PER RUN AND IN MEMORY, keyed on the rejection MESSAGE rather than the raw reply.
That is the unit the measurement found: eleven different replies earned one identical message,
and a model that fixes `say` and breaks `path` has learned something, so keying on the reply
would have called none of those a repeat. A resumed leg is a new process and starts empty,
deliberately: the first rejection of a leg teaches via `field_help` anyway, and a latch
persisted to disk would make a resume answer for mistakes its own prompt no longer shows.

This module resolves nothing and decides nothing about what a turn RUNS at — it reads the
action contract and composes prose. The two writes it makes are its own memo dict.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .actionschema import ACTION_SCHEMA

#: How much of a field's own description the correction quotes: its first sentence, capped.
#: Some descriptions in the contract run to a paragraph (`background`), and a correction that
#: pastes a paragraph per field buries the problem lines it is there to explain.
HELP_CHARS = 260

#: At most this many fields are explained in one correction. A field-shifted object produces a
#: problem line per constrained field, and naming six of them is the symptom-listing
#: `schema_guard.retry_message`'s own docstring records as teaching nothing.
MAX_FIELDS = 2

#: The four shapes a problem line names a FIELD in — the jsonschema validator's two (a missing
#: required property, and a `path: …`-prefixed line) and the per-kind checker's two (a
#: together-with pairing, and a non-empty requirement). Count them off the tuple below, not off
#: this sentence. Deliberately NOT a scan for any word that happens to be a property
#: name: `kind: 'websearch' is not one of ['util', 'delete', 'move', …]` carries the whole KINDS
#: enum, and several of those words (`delete`, `script`, `report`) are also field names, so a
#: loose scan would explain a field the model never wrote.
_FIELD_PATTERNS = (
    re.compile(r"'([a-z_][a-z0-9_]*)' is a required property"),
    re.compile(r"^([a-z_][a-z0-9_]*)(?:\.\d+)*:"),
    re.compile(r"'([a-z_][a-z0-9_]*)'(?: field)? is valid only together with"),
    re.compile(r"requires a non-empty '([a-z_][a-z0-9_]*)'"),
)

#: What an UNPARSEABLE reply is told. It names no field — there is no object to name one in —
#: and it is the rejection 9 of 37 runs earn at turn 1, so this is the one case where the first
#: correction has nothing else to teach with. The required fields come from the contract.
_UNPARSEABLE = "reply did not contain a parseable JSON object"


def _described(field: str) -> str:
    """The contract's OWN first sentence about one field, or "" when it carries none.

    Read out of `ACTION_SCHEMA` at call time rather than copied: a description edited in the
    contract reaches this correction with it. `kind` deliberately has none (it is an enum), and
    a field without a description is skipped rather than announced.
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
    """What the named fields ARE, in the contract's own words — "" when nothing can be said.

    The half of F736's done clause that makes a refusal useful rather than merely terminal:
    "the retry either NAMES THE SPECIFIC FIELD (not just the validator message) or refuses a
    second identical rejection". A model told `(root): 'say' is a required property` eleven
    times was never once told what `say` is for.
    """
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
    """This run's rejection memo — message → the turn it was FIRST earned at.

    Created on first use rather than in the loop's constructor: `engine/loop.py` sits over the
    size bar, and a lazily-held dict is the idiom its neighbours already use for per-run state
    (`loop._schema_off`).
    """
    memo: dict[str, int] | None = getattr(loop, "_schema_memo", None)
    if memo is None:
        memo = {}
        loop._schema_memo = memo
    return memo


def recall(loop, problems: Sequence[str], *, turn: int) -> str:
    """Record this rejection and return what to tell the model if the run already earned it.

    "" the first time a message is seen (including the first time in a resumed leg) — so an
    ordinary mistake is corrected exactly as before. On a repeat the sentence names the EARLIER
    turn, because "you did this at turn 236" is checkable by the model against its own context
    while "you are repeating yourself" is not.

    Mutates the memo, so it is called ONCE per rejection. Never raises: a correction that
    cannot be composed must not cost the turn it was going to help.
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
        # What the model is told to rebuild FROM has to be something the correction actually
        # carries. `field_help` says nothing when the named field has no description in the
        # contract — `kind` is the live case (it is an enum, and `kind: 'websearch' is not one
        # of [...]` is one of the two specimens this item was raised on) — and a correction
        # that cites a "field list above" which is not above teaches the model to distrust the
        # rest of the sentence.
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
