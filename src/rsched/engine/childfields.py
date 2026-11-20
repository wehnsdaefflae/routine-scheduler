"""The `child` kind's own fields: the scheduling `mode`, and what each mode may carry
(D241 — "one name for a child run", operator decision 2026-10-17).

WHY THE KIND IS ONE KIND. `engine/child.py` has always defined ONE concept with several
scheduling modes; the vocabulary around it never followed. The same thing was called a child run
in the engine and the docs, a `subrun` in the budget keys and the console, a `subtask` when
scheduled sequentially, and a `spawn` when scheduled in parallel — and `static/`'s tree used
"subtasks" for sequential children and "subruns" for parallel ones, so one word meant both "any
child" and "a parallel child". The operator, reading a routine's `max_child_runs` beside a recipe's
`subtask`, reasonably read two mechanisms. There is one: a child run, scheduled parallel or
sequential, and `mode` is where that choice lives.

WHY A FLAT FIELD RATHER THAN TWO KINDS. Two kinds made the scheduling look like a difference in
KIND — in what the thing is — when every other property is identical: its own directory, its own
budget sliced from the parent's remainder, its own fresh context, its hand-back. A field says
what is actually true, which is that one call starts a child and chooses when it runs.

WHY THE FIELDS LIVE HERE. `engine/actionschema.py` is at its size ceiling (shrink-only), and
`engine/actions.py`'s field table is a table — a per-mode rule belongs with the mode, not spread
across either. `turns` is the clearest case: it is a sequential child's budget, so demanding it
in the required/optional table could only ever demand it of both modes, and a parallel call
carrying it is a misunderstanding worth a sentence rather than silence.
"""

from __future__ import annotations

#: The scheduling modes, in the vocabulary `engine/child.py` owns. `parallel` is the default
#: because it is the mode that needs no coordination from the caller: it keeps working while the
#: child runs. A sequential child is the one that implies an order to keep.
MODES = ("parallel", "sequential")
DEFAULT_MODE = "parallel"

MODE_FIELD = {
    "type": "string",
    "enum": list(MODES),
    # ONE kind-led clause, with no `·` inside it: the projection splits on `·` and keeps any
    # clause that names no kind for EVERY kind taking the field (kindsurface), so a `·` here
    # would ship ask_user the child's scheduling and vice versa.
    "description": (
        "child: WHEN this child run is scheduled — `parallel` (the default) runs it beside "
        "you, so you keep working and are notified when it exits; `sequential` runs it as an "
        "ordered step, for decomposing a large task: it does not block you either, so to keep "
        "the order `wait` for it before starting the next one and fold its result into that "
        "brief"),
}


def mode_of(action: dict) -> str:
    """The mode this call asks for, defaulted. An unknown value is left as it is for
    `mode_problems` to report — this function never silently corrects a caller.
    """
    raw = str(action.get("mode") or "").strip()
    return raw or DEFAULT_MODE


def mode_problems(action: dict) -> list[str]:
    """What is wrong with this `child` call's mode and its mode-specific fields, as sentences
    that teach the correct call — never a bare rejection.

    Two checks, each for a mistake that would otherwise pass silently:
    - an unknown mode, which the schema enum also catches, but which is worth naming here with
      the two values rather than leaving to a schema error a run has to decode;
    - `turns` on a parallel child. A parallel child takes half the parent's remaining turns and
      nothing chooses otherwise, so a `turns` the engine ignores would read to the caller as a
      budget it set. Saying so is what turns a silent misunderstanding into one correction.
    """
    problems = []
    raw = str(action.get("mode") or "").strip()
    if raw and raw not in MODES:
        problems.append(
            f"mode {raw!r} is not a child run's scheduling — it is one of "
            f"{', '.join(MODES)} (default {DEFAULT_MODE}): `parallel` runs the child beside "
            "you, `sequential` runs it as an ordered step you wait for.")
    if action.get("turns") is not None and mode_of(action) == "parallel":
        problems.append(
            "`turns` is a SEQUENTIAL child's budget: a parallel child takes half your "
            "remaining turns. Set mode 'sequential' if you meant to budget this child, or "
            "drop `turns`.")
    return problems


#: The schema property BOTH kinds that carry a `mode` share — a child run's scheduling and
#: ask_user's blocking/deferred. One JSON-schema property cannot hold two enums, so the union
#: lives here (beside the kind whose vocabulary it mostly is) and each kind's own validator
#: refuses the other's values: `mode_problems` above, `askfields.field_problems` for the ask.
#: Composed here rather than in `engine/actionschema.py` because that file is at its size
#: ceiling and may only shrink.
MODE_FIELD_SHARED = {
    "type": "string",
    "enum": [*MODES, "blocking", "deferred"],
    "description": str(MODE_FIELD["description"])
    + " · ask_user: wait for the answer vs file it and continue (default deferred)",
}
