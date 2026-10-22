"""The `job` action's GRAMMAR — the shape a job turn must have, nothing about the store.

Split out of `actions.py` the way `goalops.field_problems`, `decideaction.field_problems`
and `remindfields.field_problems` already are: the flat checker in `validate_action` is the
action contract's single home, and a kind whose grammar needs thirty lines of its own keeps
them beside it rather than inside it. Whether the job EXISTS, is open or is due is
`engine/jobops.py`'s to judge against the store.

`actions.py` was at its size-ratchet ceiling when `do_nothing` (D186) needed room; this is the
responsibility boundary it already had.
"""

from __future__ import annotations

from datetime import date

from ..ids import is_slug

#: The `job` action's verbs (engine/jobops.py) — the shared `verb` property's enum carries
#: manage_lane's too, so the per-kind check is what holds a task turn to its own.
JOB_VERBS = ("list", "create", "update", "open", "checkpoint", "delete")


def field_problems(obj: dict) -> list[str]:
    """The shape a `job` action must have. Whether the task EXISTS, is open or is due is the
    handler's to judge against the store (engine/jobops.py) — this is only the grammar, so a
    malformed call is corrected inside the schema-retry cycle and never costs a turn.
    """
    verb = str(obj.get("verb") or "")
    if verb not in JOB_VERBS:
        return [f"kind=job requires 'verb' to be one of {list(JOB_VERBS)}"]
    out = []
    tid = str(obj.get("id") or "")
    if verb in ("create", "update", "checkpoint", "delete") and not tid:
        out.append(f"kind=job verb={verb} requires 'id' (the task's kebab-case id)")
    elif tid and not is_slug(tid):
        out.append(f"kind=job: 'id' must be a kebab-case task id, got {tid!r}")
    if verb == "create":
        out += [f"kind=job verb=create requires a non-empty {f!r}"
                for f in ("title", "brief") if not str(obj.get(f) or "").strip()]
    if verb == "checkpoint":
        if not obj.get("outcome"):
            out.append("kind=job verb=checkpoint requires 'outcome' (advanced, no-work, "
                       "blocked or deferred)")
        if not str(obj.get("summary") or "").strip():
            out.append("kind=job verb=checkpoint requires 'summary' — what this run did for "
                       "the task and what the next run picks up")
    if (wake := obj.get("wake")) not in (None, ""):
        try:
            date.fromisoformat(str(wake))
        except ValueError:
            out.append(f"kind=job: 'wake' is a date YYYY-MM-DD (or \"\" to clear it), got "
                       f"{wake!r}")
    stray = {"outcome", "summary", "accounting"} & set(obj) if verb != "checkpoint" else set()
    if stray:
        out.append(f"kind=job: {sorted(stray)} belong to verb=checkpoint only")
    return out
