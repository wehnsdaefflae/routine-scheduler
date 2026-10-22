"""The per-kind PROMPT surface: which action kinds a run may actually use, and the
projection of ACTION_SCHEMA onto them.

`actions.py` stays the single source of truth for what a turn may do — this module only
NARROWS what the model is shown to what the engine would accept anyway. A run whose
workflow `tools:` allowlist and capabilities permit 8 of the 34 kinds was previously sent
all 34 in the schema (8k chars, ~36% of the fixed prompt) plus a prose bullet each: the
model read, every turn, the full description of channels the validator would reject. The
projection is derived from `actions.KIND_FIELDS` — the same map `validate_action` builds
its allowed-field set from — so the shown schema and the enforced contract cannot drift.

Two consumers, one derivation: the composed prompt (composer.build_system_prompt) and the
endpoint's constrained-decoding schema (completion.next_action). Sending the narrowed
schema to the transport also makes a disallowed kind UNGENERATABLE rather than generated
and then rejected — a saved turn, not just saved tokens. Validation keeps the FULL schema
(control.run_user_command, the retry cycle): rejecting a well-formed action the run wasn't
allowed to take must stay a precise, teaching denial, not a schema parse error.

The BULLETS the same projection selects are `engine/kindprose.py` — one responsibility each,
split when `do_nothing` (D186) needed room in a file sitting at its size-ratchet ceiling.
"""

from __future__ import annotations

import copy

from . import thenscript
from .actions import ALWAYS_KINDS, KIND_FIELDS
from .actionschema import ACTION_SCHEMA, KINDS

# Fields every kind carries (actions.validate_action allows `note` on any kind, like `say`).
_UNIVERSAL_FIELDS = ("say", "note", "kind")
# …and the two that ride every kind only while the reminders capability is on. They are
# projected like a KIND rather than described-then-refused: a run whose capability is off
# cannot generate them at all (kindsurface's whole premise).
_REMINDER_FIELDS = ("remind", "remind_feedback")

_CLAUSE_SEP = " · "


def effective_kinds(allowed_kinds: set[str] | None = None, grants=None) -> list[str]:
    """The kinds this run may actually emit: workflow `tools:` ∩ (base ∪ capabilities),
    plus ALWAYS_KINDS. In KINDS order, so every surface lists them the same way.

    The one owner of this computation — capabilities.py, the composer's prose bullets and
    the schema projection all read it here, so the CAPABILITIES section can never advertise
    a kind the schema omits (or vice versa).
    """
    return [k for k in KINDS
            if (allowed_kinds is None or k in allowed_kinds or k in ALWAYS_KINDS)
            and (grants is None or grants.allows_kind(k))]


def _clause_kinds(clause: str) -> set[str]:
    """The action kinds a description clause is about, from its `kind1/kind2: …` lead.

    Tolerant by design: a lead may carry extra words ("write_util edit mode: …"), so each
    slash-separated part contributes its FIRST token. An unrecognized lead yields the empty
    set, which `_project_description` treats as "keep" — trimming never drops prose it did
    not positively understand.
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
    # All clauses were kind-specific and none matched: the property is being kept for a
    # reason the lead didn't express — keep the text rather than ship an empty description.
    return _CLAUSE_SEP.join(kept) if kept else description


def schema_for_kinds(kinds: list[str] | set[str] | None, *,
                     reminders: bool = False) -> dict:
    """ACTION_SCHEMA narrowed to `kinds`: the `kind` enum, the properties those kinds use
    (per `KIND_FIELDS`), and each surviving description trimmed to its relevant clauses.
    `reminders` keeps the two consequence-reminder side fields, which are capability-gated
    and therefore not universal the way `say`/`note` are.

    `None` (or the full set) WITH reminders on returns the schema unchanged — a run with
    everything enabled pays nothing for the machinery, and the prompt-caching contract sees
    the same bytes.
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
    # filter the DEEPCOPY's specs, never the original's: taking them from ACTION_SCHEMA
    # here handed the description-trimming loop below references into the shared global,
    # so every projection permanently trimmed the full schema for the whole process
    # (cross-run contamination in the daemon; surfaced by test_projection_is_materially_
    # smaller going order-dependent).
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
