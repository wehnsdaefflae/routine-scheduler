"""The `ask_user` action's GRAMMAR — one question, or a batch of them (D212).

Its own module because `engine/actions.py` may only shrink. An `ask_user` carries EITHER one
question (`question` + its `options` / `default` / `request` / `config_patch` / `blocks_work` /
`proof`) OR a `questions` list, each entry carrying those fields for itself — never both, or the
applier would have to pick one silently. A `proof` is checked by `engine/askproof.py`.

The `questions` item schema is shared with `decide` (the schema is flat), so decide's `name` and
`answer_type` are refused HERE by name rather than silently ignored.

A batch never blocks the process: `mode: "blocking"` parks the run on ONE answer, which means
nothing for several; `blocks_work` marks the questions that stop the work instead.
"""

from __future__ import annotations

from . import askproof

#: ask_user's pick-one list, per question. The shared schema field allows decide's longer
#: lists, so this cap is held here rather than by the schema.
ASK_OPTIONS_MAX = 5
#: Questions one action may ask at once — the schema's `maxItems`, restated where enforced.
ASK_QUESTIONS_MAX = 16
#: The single-question fields a batch moves INTO each question.
_PER_QUESTION = ("options", "default", "request", "config_patch", "blocks_work", "proof")
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
    problems += askproof.problems(spec.get("proof"), options, where=where,
                                  has_patch=isinstance(spec.get("config_patch"), dict))
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
        return problems + askproof.problems(
            obj.get("proof"), obj.get("options"), where="kind=ask_user",
            has_patch=isinstance(obj.get("config_patch"), dict))
    problems = []
    if single:
        problems.append("kind=ask_user takes 'question' (one question) or 'questions' "
                        "(several in one action), not both — put the single one in the list")
    # Truthiness, not presence: a constrained decoder emits other kinds' fields as empty
    # strings / false / null (see `actions.normalize_action`).
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
