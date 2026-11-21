"""The per-kind PROMPT surface: which action kinds a run may actually use, and the
projection of ACTION_SCHEMA onto them.

`actions.py` stays the source of truth; this module only NARROWS what the model is shown to
what the engine would accept anyway (a run permitted 8 of the 35 kinds was once sent
all 35 in the schema). Derived from `actions.KIND_FIELDS` — the map `validate_action` uses — so
the shown schema and the enforced contract cannot drift.

Two consumers: the composed prompt and the constrained-decoding schema
(completion.next_action), where a disallowed kind becomes UNGENERATABLE. Validation keeps the
FULL schema, so a disallowed action gets a teaching denial, not a parse error. The matching
prose bullets are `engine/kindprose.py`.
"""

from __future__ import annotations

import copy

from . import thenscript
from .actions import ALWAYS_KINDS, KIND_FIELDS
from .actionschema import ACTION_SCHEMA, KINDS
from .kindaliases import ALIAS_KINDS

# Fields every kind carries (actions.validate_action allows `note` on any kind, like `say`).
_UNIVERSAL_FIELDS = ("say", "note", "kind", "role_swap")
# …and the two that ride every kind only while the reminders capability is on (projected out,
# not described-then-refused).
_REMINDER_FIELDS = ("remind", "remind_feedback")

_CLAUSE_SEP = " · "


def effective_kinds(allowed_kinds: set[str] | None = None, grants=None) -> list[str]:
    """The kinds this run may actually emit: workflow `tools:` ∩ (base ∪ capabilities),
    plus ALWAYS_KINDS. In KINDS order, so every surface lists them the same way.

    The one owner: CAPABILITIES, the prose bullets and the schema all read it here.
    """
    return [k for k in KINDS
            if (allowed_kinds is None or _permitted(k) & (allowed_kinds | set(ALWAYS_KINDS)))
            and (grants is None or grants.allows_kind(k))]


def _permitted(kind: str) -> set[str]:
    """The allowlist entries that make `kind` available — itself, or what it is a SPELLING of.

    D290's alias kinds are rewritten into a util call before any gate, so permitting `util`
    permits them; it never fails open — without `util` no alias is reachable.
    """
    return {kind, "util"} if kind in ALIAS_KINDS else {kind}


def _clause_kinds(clause: str) -> set[str]:
    """The action kinds a description clause is about, from its `kind1/kind2: …` lead.

    Each slash-separated part contributes its FIRST token; an unrecognized lead yields the
    empty set, which means "keep" — trimming never drops prose it did not understand.
    """
    head, sep, _ = clause.partition(": ")
    if not sep:
        return set()
    return {tok for part in head.split("/")
            if (tok := part.strip().split(" ")[0]) in KINDS}


def _project_description(description: str, kinds: set[str]) -> str:
    """Drop the ` · `-separated clauses that speak only to kinds this run cannot use."""
    clauses = description.split(_CLAUSE_SEP)
    kept = [c for c in clauses if not (found := _clause_kinds(c)) or (found & kinds)]
    # nothing matched: keep the text rather than ship an empty description
    return _CLAUSE_SEP.join(kept) if kept else description


def schema_for_kinds(kinds: list[str] | set[str] | None, *,
                     reminders: bool = False) -> dict:
    """ACTION_SCHEMA narrowed to `kinds`: the `kind` enum, the properties those kinds use
    (per `KIND_FIELDS`), and each surviving description trimmed to its relevant clauses.
    `reminders` keeps the two capability-gated reminder side fields. The full set WITH
    reminders returns ACTION_SCHEMA itself (same bytes for the prompt cache).
    """
    keep = (set(KINDS) if kinds is None
            else {k for k in kinds if k in KIND_FIELDS} | set(ALWAYS_KINDS))
    if keep >= set(KINDS) and reminders:
        return ACTION_SCHEMA
    fields = set(_UNIVERSAL_FIELDS) | (set(_REMINDER_FIELDS) if reminders else set())
    for kind in keep:
        required, optional = KIND_FIELDS[kind]
        fields.update(required)
        fields.update(optional)
    if "script" not in keep:
        # the one field that names ANOTHER kind's call: a write may carry a script only where
        # the run may call scripts at all (engine/thenscript.py)
        fields.discard(thenscript.FIELD)
    out = copy.deepcopy(ACTION_SCHEMA)
    # filter the DEEPCOPY's specs, never the original's: the trimming below would otherwise
    # mutate the shared global for the whole process
    out["properties"] = {
        name: spec for name, spec in out["properties"].items() if name in fields
    }
    for name, spec in out["properties"].items():
        if (name not in _UNIVERSAL_FIELDS and name not in _REMINDER_FIELDS
                and isinstance(spec.get("description"), str)):
            spec["description"] = _project_description(spec["description"], keep)
    out["properties"]["kind"] = {**ACTION_SCHEMA["properties"]["kind"],
                                 "enum": [k for k in KINDS if k in keep]}
    return out
