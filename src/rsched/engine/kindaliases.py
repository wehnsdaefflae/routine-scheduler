"""An action kind that IS a util call — the spelling callers already reach for.

D290, answered by the operator 2026-11-14: *"Make `websearch` and `code-search` action kinds,
matching what callers already attempt"*, chosen over three options that left the schema alone.

MEASURED FIRST, which is what makes the design narrow. `kind: websearch` and
`kind: code-search` are 23 rejected turns in 30 days across 12 routines and 3 conversations —
the third-largest action-schema rejection class, written by `claude-sonnet-5` and
`claude-opus-5` alike. And every one recovered INSIDE its own turn: `global-utils-review:
20261113-031130` turns 77/89/90 say *"Correcting the call shape — websearch is a util, not a
kind"*, with `kind="util", name="websearch", args=[query]` succeeding immediately. So what the
rejected shape MEANT is not a guess: a util call whose payload is the query.

SO AN ALIAS IS A SPELLING, REWRITTEN BEFORE ANY GATE — not a second front door. `rewrite` runs
inside `actions.normalize_action`, ahead of the schema validator, the capability layer and the
secret gate, and what the run executes, what the transcript records and what telemetry counts is
the ordinary util action. Three things follow that a new executor entry would have broken:

* ONE implementation — no second place for the util's arguments, timeout or failure shape to
  drift from the one every hand-written call uses.
* ONE permission — the util kind's secret gate, reservations and grants judge the rewritten
  action exactly as they judge a call the run typed itself. A kind of its own would route around
  all three, which is how `background` once relocated a security decision (F633).
* ONE telemetry row — `shiftedfields.util_rejection_outcome` and `RunContext.count_util` see
  `name="websearch"`, so the Stats tab keeps its existing row instead of growing a phantom one.

`code_search` IS THE KIND; `code-search` IS ACCEPTED ANYWAY. No action kind may contain a hyphen
— `tests/test_wrong_kind_diagnosis.py` pins that property because it is the discriminator that
diagnoses every OTHER util-name-as-kind mistake, and a hyphenated kind would make those
diagnoses wrong. Since the rewrite precedes the validator, the hyphenated spelling callers
actually send (11 of the 23) never has to be a kind in order to work.

AND IT DECLINES RATHER THAN GUESS. An alias action with no derivable payload is returned
UNCHANGED, so the validator refuses it inside the schema-retry cycle — a correction that costs no
turn — instead of running `gu websearch` with an empty query, which costs a real one. That is
also why the payload tolerance below is safe: every spelling it accepts carries a query, and
anything else is left for the retry to name.
"""

from __future__ import annotations

#: alias kind → the util it is a spelling of. The kind is the KEY: it is what the enum carries,
#: what a wire grammar is built from, and what a prose bullet is written for.
ALIAS_KINDS: dict[str, str] = {
    "websearch": "websearch",
    "code_search": "code-search",
}

#: every spelling the rewrite answers to → the util. The kinds, plus the hyphenated form the
#: measurement found callers writing. A spelling that is NOT a kind reaches this map only
#: through `normalize_action`, which runs before the enum is enforced — so admitting one costs
#: the schema nothing and saves the turn the enum would have refused.
SPELLINGS: dict[str, str] = {**ALIAS_KINDS, "code-search": "code-search"}

#: Fields a model puts the payload in. `args` is the real one (the util kind's own field);
#: the rest are what a model writing a single-field action reaches for, and they are
#: UNDECLARED by ACTION_SCHEMA — which is exactly why they must be read HERE. After
#: normalisation an undeclared field is gone (`additionalProperties: false` strips it), so a
#: tolerated spelling read any later would be a util call with an empty query.
_PAYLOAD_FIELDS = ("args", "query", "pattern", "prompt", "command", "name")


def _payload(obj: dict) -> list[str] | None:
    """The util arguments this alias action carries, or None when it carries none.

    None is a DECISION, not a failure: see the module docstring — an empty call costs a turn,
    a refused one costs none.
    """
    for field in _PAYLOAD_FIELDS:
        val = obj.get(field)
        if isinstance(val, str) and val.strip():
            return [val]
        if isinstance(val, list):
            items = [str(v) for v in val if str(v).strip()]
            if items:
                return items
    return None


def rewrite(obj: dict) -> dict | None:
    """`obj` as the util action it spells, or None when it is not an alias (or carries nothing).

    Returning None for every ordinary action is the signature `field_shift_diagnosis` and
    `unkind_diagnosis` both insist on: a rewrite that fires on actions that were already right
    would teach the model to distrust the contract.
    """
    util = SPELLINGS.get(str(obj.get("kind") or ""))
    if util is None:
        return None
    args = _payload(obj)
    if args is None:
        return None
    out = {k: v for k, v in obj.items() if k not in _PAYLOAD_FIELDS}
    out["kind"] = "util"
    out["name"] = util
    out["args"] = args
    return out
