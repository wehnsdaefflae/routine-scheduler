"""The SEVERAL-questions-in-one-action fields as schema — `questions` and `blocks_work`.

Their own module for the reason `engine/schema_remind.py` and `engine/schema_background.py`
give: `engine/actionschema.py` is over the repo's size bar and may only shrink, so a field
whose description has to GROW moves out rather than pushing that file up. `questions` had to
grow when `ask_user` gained the same batch shape `decide` already had (D212), and the class
claim `blocks_work` is new prose beside it.

`questions` is now a SHARED field (decide + ask_user), so its description is clause-led —
`kind1/kind2: …`, one clause per accepting kind, each carrying its own lead, which
`kindsurface._project_description` needs to ship a run only the clauses of the kinds it may
emit (tests/test_kindsurface.py pins both halves). The ITEM schema is the union of what the
two kinds put in a question, because `additionalProperties: False` means a sub-field this
object does not declare is stripped SILENTLY — no error, no log line — and each kind's own
grammar check (`decideaction.field_problems`, `askfields.field_problems`) is what refuses the
sub-fields that do not belong to it.

What the batch is FOR, measured over the 282 question records the instance had filed before it
existed: 37% of runs (60 of 163) already asked more than one question, up to 6 in one run, and
51% of the operator's answering sittings disposed of several questions at once. The asking side
did not match that — five questions cost five turns of a run's context and five unrelated
cards. One action, one dispatch, N records sharing a batch id is the same information at a
fifth of the cost. What a batch is NOT is a new waiting mode: `mode: "blocking"` keeps its own
meaning and a batch is always filed and carried on from (0 of those 282 asks used blocking at
all, and 0% of answers arrived within the hour).
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
        # ask_user's per-question fields: each means for ITS question exactly what the
        # same-named top-level field means for a single ask, so they are not re-described
        # here (the way decide's `options`/`answer_type` are not) — the field's own
        # description is one object up, and this list is what keeps them from being stripped.
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

    A batch carries no top-level `question`, so the brief every turn record, compaction digest
    and reminder match reads would be EMPTY. The first question plus the count identifies the
    action the way one question's text does.
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
