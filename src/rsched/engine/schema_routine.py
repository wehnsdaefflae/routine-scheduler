"""The `create_routine` fields as schema — the kind whose grammar is widest (D198).

Extracted from `actionschema.py` the way `schema_questions`, `schema_remind` and
`schema_background` were, and for the same reason: that file is over the house size bar, so its
ceiling in `tests/size_ratchet.json` is SHRINK-ONLY and a new field cannot be added to it. D198
needed one — `revises`, the in-place revision path's mode flag — so the seven fields only this
kind uses moved out first and the field went in here. `workflow` stays in `actionschema`: it is
shared with spawn/subtask/detach, and a field two kinds read belongs where both can see it.

The ORDER of this mapping is part of the always-on contract: `kindsurface.schema_for_kinds`
renders `ACTION_SCHEMA` as the model sees it, `docs/prompt-anatomy.md` §5.1 quotes that
rendering byte for byte (`tests/test_prompt_anatomy_example.py`), and these keys are merged at
the exact position they held in the file they came from. A reordering here is a doc change.
"""

from __future__ import annotations

#: The create_routine-only properties, in ACTION_SCHEMA order (merged with `**` at the place
#: `pattern` … `replaces` stood, so the rendered schema is unchanged but for `revises`).
ROUTINE_FIELDS: dict[str, dict] = {
    "pattern": {"type": "string",
                "description": "create_routine: the SETTINGS pattern the new routine "
                               "follows (default: the one that fits its workflow)"},
    "setup": {"type": "array", "items": {"type": "string"}, "maxItems": 12,
              "description": "create_routine: the user's answers to the settings "
                             "pattern's questions — which folders, which mailbox and "
                             "senders, which cadence — one per entry, in their words. They "
                             "become the new routine's proposed settings, which the user "
                             "accepts on its page; they never go into the instruction"},
    "done_when": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                  "description": "create_routine: what ONE finished run of the new routine "
                                 "leaves behind, in the USER's own words, one per entry "
                                 '("the digest is published and the link works"): lines of '
                                 "its `## Done when`, accounted for at every finish. Carry "
                                 "their answer verbatim; never invent one"},
    "finish_line": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                    "description": "create_routine: when the ROUTINE is done for good, one "
                                   "outcome per entry led by its judge — `run: …` (a run "
                                   "proves it), `you: …` (only the user decides), "
                                   "`YYYY-MM-DD: …` — plus `until YYYY-MM-DD`. Most "
                                   "routines never end (a monitor, a digest): omit it then"},
    "never": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
              "description": "create_routine: what a run of the new routine must NEVER "
                             'do, in the USER\'s words, one per entry ("never mail the '
                             'funder"): lines of its `## Never`, and its settings leave off '
                             "what would allow it. Omit it when they named none"},
    "produces": {"type": "array", "items": {"type": "string"}, "maxItems": 7,
                 "description": "create_routine (required): what the routine exists to "
                                "PRODUCE, in the user's words — `principal: <who>` once, "
                                "then per measure `<id> · <count|level> · <higher|lower> — "
                                "<what it counts>`; its own scripts/outcome.py counts them"},
    "replaces": {"type": "string",
                 "description": "create_routine: an EXISTING routine's slug — create a "
                                "REPLACEMENT CANDIDATE at once (routine-rebuild), unscheduled "
                                "and without its grants, plus ONE swap decision for the user"},
    "revises": {"type": "boolean",
                "description": "create_routine: REVISE the EXISTING routine `target` names "
                               "instead of creating one (recipe-authoring) — `prompt` is the "
                               "revision, one line per change. Its own `revise_confirm` dial "
                               "decides: one decision for the operator, or armed at once. "
                               "Either way ITS next run makes the edit, never you"},
}
