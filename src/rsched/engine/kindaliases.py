"""An action kind that IS a util call — the spelling callers already reach for.

`kind: websearch` / `kind: code-search` mean a util call whose payload is the query (D290).

AN ALIAS IS A SPELLING, REWRITTEN BEFORE ANY GATE — not a second front door. `rewrite` runs
inside `actions.normalize_action`, ahead of the schema validator, the capability layer and the
secret gate, so what runs, is recorded and is counted is the ordinary util action: ONE
implementation, ONE permission path, ONE telemetry row.

THE SPELLING IS STILL MEASURABLE. The landed `assistant_action` event carries `spelled: <alias
kind>` beside `turn` and `phase` — an EVENT key, never a payload key, so the action recorded,
replayed and sent back to the model is the util call alone — which is how `rsched census` counts
an alias kind it could otherwise never see. `completion.judge` wraps the normalized action in
`Spelled`; the mark travels WITH that one action to `loop._land`, so each element of a chain
carries its own.

`code_search` IS THE KIND; `code-search` IS ACCEPTED ANYWAY. No action kind may contain a hyphen
(`tests/test_wrong_kind_diagnosis.py` — it is the discriminator for util-name-as-kind
diagnoses); since the rewrite precedes the validator, the hyphenated spelling still works.

IT DECLINES RATHER THAN GUESS: an alias with no derivable payload is returned UNCHANGED, so the
validator refuses it within the free schema retry instead of running an empty query.
"""

from __future__ import annotations

from typing import Any

#: alias kind → the util it is a spelling of. The kind is the KEY: it is what the enum carries,
#: what a wire grammar is built from, and what a prose bullet is written for.
ALIAS_KINDS: dict[str, str] = {
    "websearch": "websearch",
    "code_search": "code-search",
}

#: every spelling the rewrite answers to → the util: the kinds plus the hyphenated form, which
#: reaches this map before the enum is enforced.
SPELLINGS: dict[str, str] = {**ALIAS_KINDS, "code-search": "code-search"}
#: util → the alias kind spelling it, so the hyphenated form is counted under its kind.
_KIND_OF: dict[str, str] = {util: kind for kind, util in ALIAS_KINDS.items()}

#: Fields a model puts the payload in. `args` is the real one; the rest are UNDECLARED by
#: ACTION_SCHEMA and stripped by normalisation, so they must be read HERE.
_PAYLOAD_FIELDS = ("args", "query", "pattern", "prompt", "command", "name")


def _payload(obj: dict) -> list[str] | None:
    """The util arguments this alias action carries, or None when it carries none.

    None is a DECISION: an empty call costs a turn, a refused one costs none.
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

    Must return None for every ordinary action, like `field_shift_diagnosis`.
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


def spelled_kind(obj: dict) -> str | None:
    """The alias KIND `obj` was written as when `rewrite` turns it into a util call, else None."""
    return _KIND_OF[SPELLINGS[str(obj["kind"])]] if rewrite(obj) is not None else None


class Spelled(dict[str, Any]):
    """A util action the model wrote as an alias kind — a plain dict to every reader, plus the
    `spelled` attribute `loop._land` records as the event's mark. An attribute, not a key: the
    payload stays the util call's own.
    """

    def __init__(self, action: dict, spelled: str) -> None:
        super().__init__(action)
        self.spelled = spelled


def event_mark(action: dict) -> dict[str, str]:
    """The `assistant_action` event keys naming how `action` was spelled — `{}` for most."""
    return {"spelled": action.spelled} if isinstance(action, Spelled) else {}
