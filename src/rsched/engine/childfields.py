"""The `child` kind's own fields: the scheduling `mode`, and what each mode may carry
(D241 — one name for a child run).

ONE KIND, because `engine/child.py` defines ONE concept: a child run, with its own directory,
budget slice, fresh context and hand-back, scheduled parallel or sequential. Two kinds would make
the scheduling look like a difference in what the thing is; `mode` is where that choice lives.

The per-mode rules live here rather than in `engine/actions.py`'s field table, which could only
demand a field of both modes — `turns` is a sequential child's budget, and a parallel call
carrying it deserves a sentence rather than silence.
"""

from __future__ import annotations

#: The scheduling modes, in the vocabulary `engine/child.py` owns. `parallel` is the default
#: because it needs no coordination from the caller.
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

    Two checks, each for a mistake that would otherwise pass silently: an unknown mode (named
    with the two values rather than left to a schema error), and `turns` on a parallel child,
    which takes half the parent's remaining turns whatever `turns` says.
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
MODE_FIELD_SHARED = {
    "type": "string",
    "enum": [*MODES, "blocking", "deferred"],
    "description": str(MODE_FIELD["description"])
    + " · ask_user: wait for the answer vs file it and continue (default deferred)",
}
