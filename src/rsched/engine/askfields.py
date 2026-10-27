"""The `ask_user` action's GRAMMAR — one question, or a batch of them (D212).

Moved out of `engine/actions.py` the way `engine/decideaction.field_problems` and
`engine/jobfields` were: that file is the action contract's single home, it is over the repo's
size bar and may only shrink, so a kind whose grammar is a block of its own lives in its own
module. This one grew a batch to check.

An `ask_user` carries EITHER one question (`question` + its `options` / `default` / `request` /
`config_patch` / `blocks_work`) OR a `questions` list, each entry carrying the same fields for
itself. Never both: two shapes in one action say nothing about which the user is being asked,
and the applier would have to pick one silently.

The `questions` ITEM schema is shared with `decide` (engine/schema_questions.py), because
`ACTION_SCHEMA` is flat by design — so `name` and `answer_type`, which are decide's, reach an
ask_user batch as valid JSON and are refused HERE, by name, rather than ignored. A sub-field
nobody refuses and nobody reads is the shape that makes a model repeat it.

A batch never blocks the process. `mode: "blocking"` is the separate, orthogonal thing — the
run's own process sitting out the ask timeout — and over the 282 question records measured
before this existed it was used 0 times, with 0% of answers arriving within the hour. Asking
five questions and waiting on the first would hold the run for all five.
"""

from __future__ import annotations

#: ask_user's pick-one list, per question. The shared schema field allows decide's longer
#: lists, so this cap is held here rather than by the schema.
ASK_OPTIONS_MAX = 5
#: Questions one action may ask at once — the schema's `maxItems`, restated where it is
#: enforced. 6 was the most any real run ever asked across the 282 records measured.
ASK_QUESTIONS_MAX = 16
#: The single-question fields a batch moves INTO each question.
_PER_QUESTION = ("options", "default", "request", "config_patch", "blocks_work")
#: `questions` entries' fields, and the two that belong to `decide` alone.
_ITEM_FIELDS = frozenset({"question", *_PER_QUESTION})
_DECIDE_ONLY = ("name", "answer_type")


def _item_problems(spec: object, where: str) -> list[str]:
    """One question of a batch, checked as the whole action would be checked."""
    if not isinstance(spec, dict):
        return [f"{where} must be an object with its own 'question'"]
    problems: list[str] = []
    if not str(spec.get("question") or "").strip():
        problems.append(f"{where} needs a non-empty 'question' — what you are asking, "
                        "self-contained")
    options = spec.get("options")
    if options is not None and (not isinstance(options, list)
                                or not all(isinstance(o, str) and o.strip() for o in options)):
        problems.append(f"{where}: 'options' must be a list of non-empty pick-one choices")
    elif options is not None and len(options) > ASK_OPTIONS_MAX:
        problems.append(f"{where}: at most {ASK_OPTIONS_MAX} 'options' — a longer list "
                        "is a question to rephrase, not a menu")
    if (patch := spec.get("config_patch")) is not None and not isinstance(patch, dict):
        problems.append(f"{where}: 'config_patch' is the PATCH body as an object")
    if stray := [f for f in _DECIDE_ONLY if f in spec]:
        problems.append(f"{where}: {stray} belong to kind=decide, not to a question you are "
                        "asking the user — drop them")
    if unknown := sorted(set(spec) - _ITEM_FIELDS - set(_DECIDE_ONLY)):
        problems.append(f"{where}: unknown field(s) {unknown} — a question carries "
                        f"'question' and {list(_PER_QUESTION)}")
    return problems


def field_problems(obj: dict) -> list[str]:
    """Every problem with this ask_user's shape — the single form, or the batch."""
    batch = obj.get("questions")
    single = str(obj.get("question") or "").strip()
    if batch is None:
        problems = []
        if not single:
            problems.append("kind=ask_user requires a non-empty 'question' field, or "
                            "'questions' (several questions in one action, each with its own)")
        if len(obj.get("options") or []) > ASK_OPTIONS_MAX:
            problems.append(f"kind=ask_user: at most {ASK_OPTIONS_MAX} 'options' — a longer "
                            "list is a question to rephrase, not a menu")
        return problems
    problems = []
    if single:
        problems.append("kind=ask_user takes 'question' (one question) or 'questions' "
                        "(several in one action), not both — put the single one in the list")
    # Truthiness, not presence: a constrained decoder emits other kinds' fields as empty
    # strings / false / null (see `actions.normalize_action`), and refusing a batch over
    # `blocks_work: false` would reject an action that asked for nothing of the sort.
    if riding := [f for f in _PER_QUESTION if obj.get(f)]:
        problems.append(f"kind=ask_user: {riding} belong to EACH question when you pass "
                        "'questions' — move them into the entries they are about")
    if not isinstance(batch, list) or not 1 <= len(batch) <= ASK_QUESTIONS_MAX:
        problems.append(f"kind=ask_user: 'questions' must list 1-{ASK_QUESTIONS_MAX} questions")
        return problems
    if str(obj.get("mode") or "") == "blocking":
        problems.append("kind=ask_user: a 'questions' batch is always filed and you carry on — "
                        "mode 'blocking' parks the run on ONE answer, which cannot mean "
                        "anything for several. Drop 'mode', and mark the questions that stop "
                        "your work with 'blocks_work' instead")
    for i, spec in enumerate(batch):
        problems += _item_problems(spec, f"kind=ask_user questions[{i}]")
    return problems
