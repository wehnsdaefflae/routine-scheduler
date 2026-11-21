"""The SEVERAL-questions-in-one-action fields as schema — `questions` and `blocks_work`.

Its own module because `engine/actionschema.py` is over the size bar (D212).

`questions` is SHARED (decide + ask_user), so its description is clause-led — one
`kind: …` clause per accepting kind, as `kindsurface._project_description` needs. The ITEM
schema is the union of both kinds' sub-fields, because `additionalProperties: False` strips an
undeclared sub-field SILENTLY; each kind's own grammar check refuses what does not belong to it.

A batch is N records sharing a batch id in one action — one turn instead of N. It is NOT a
waiting mode: `mode: "blocking"` keeps its meaning and a batch is filed and carried on from.
"""

from __future__ import annotations

#: Several questions in ONE action — `decide`'s batch over one body of evidence, and
#: `ask_user`'s batch of decisions for the user.
QUESTIONS_FIELD = {
    "type": "array", "maxItems": 16,
    "items": {"type": "object", "additionalProperties": False, "properties": {
        "name": {"type": "string",
                 "description": "decide ONLY: snake_case id the answer is filed under"},
        "question": {"type": "string", "description": "what to decide"},
        "answer_type": {"type": "string", "enum": ["yes_no", "choice", "score"],
                        "description": "decide ONLY: how it is answered"},
        "options": {"type": "array", "items": {"type": "string"}, "maxItems": 26},
        # ask_user's per-question fields mean what the same-named top-level field means;
        # declared here only so they are not stripped.
        "default": {"type": "string"},
        "request": {"type": "string"},
        "config_patch": {"type": "object"},
        "blocks_work": {"type": "boolean"}}},
    "description": "decide: SEVERAL independent questions over the same evidence, in "
                   "one call (instead of question/answer_type/options) — one evidence, "
                   "many questions is what these models are fastest and cheapest at · "
                   "ask_user: SEVERAL questions in ONE action, each entry carrying for "
                   "itself what a single ask carries — one decision record each, sharing "
                   "a batch id. One turn, not five. Not a waiting mode: a batch is filed "
                   "and you carry on",
}

def batch_brief(batch: list) -> str:
    """A `questions` batch's one-line brief (`actionschema.brief_value`).

    A batch has no top-level `question`, so the first question plus the count stands in.
    """
    first = batch[0] if batch and isinstance(batch[0], dict) else {}
    return f"{first.get('question') or ''} (+{len(batch) - 1} more)"


#: The CLASS claim — the run's own override of what the subject already decided.
BLOCKS_WORK_FIELD = {
    "type": "boolean",
    "description": "ask_user: OPTIONAL — this question is BLOCKING: without this answer the "
                   "run cannot do the work it was fired to do. Every record carries that "
                   "class, set by its subject (an access `request` blocking, a plain question "
                   "closing — you finish without it and the answer shapes your next run); "
                   "this overrides. NOT `mode`, which is the separate thing that makes your "
                   "process WAIT — marking a question blocking suspends nothing. On a "
                   "`questions` batch, set it per question",
}
