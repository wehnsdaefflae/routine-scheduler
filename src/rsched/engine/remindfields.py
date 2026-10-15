"""The semantic checks for the two reminder side fields — `remind` and `remind_feedback`.

Its own module because it is PURE: it reads the action dict and the grant policy and returns
problem strings, with no loop, no store write and no turn of its own. `engine/actions.py` calls
it inside the schema-retry cycle, so a malformed op is corrected before it becomes a turn —
beside `goalops.field_problems` and `decideaction.field_problems`, which are the same shape.

Split out of `engine/remind.py` in 0.425.0: that module is over the repo's size bar and may only
shrink, and validation that needs no run state was the boundary it already had.
"""

from __future__ import annotations

from .. import reminder_checks as checks
from .. import reminders as store
from ..reminders import LABEL_HELP


def field_problems(action: dict, grants) -> list[str]:
    """Semantic checks for the two side fields, run inside the schema-retry cycle
    (`actions.validate_action`) so a malformed op is corrected and never becomes a turn.

    The capability check lives here rather than in `GrantPolicy.deny`'s kind gate because these
    fields ride EVERY kind, including the always-available ones the kind gate skips.
    """
    problems: list[str] = []
    if (op := action.get("remind")) is not None:
        problems += _remind_problems(op, grants)
    if (fb := action.get("remind_feedback")) is not None:
        # the capability gates the LAYER, not just the write: a run without it has no fires to
        # label, and an accepted label there would answer a channel the docs say is closed
        if grants is not None and (denial := grants.reminder_denial("local")):
            problems.append(denial)
        else:
            problems += _feedback_problems(fb)
    return problems


def _remind_problems(op: object, grants) -> list[str]:
    if not isinstance(op, dict):
        return ['`remind` must be an object: {"op": "add|revise|delete", ...}']
    verb = str(op.get("op") or "")
    if verb not in ("add", "revise", "delete"):
        return ['`remind.op` must be "add", "revise" or "delete"']
    scope = str(op.get("scope") or "local")
    if scope not in store.SCOPES:
        return ['`remind.scope` must be "local" or "global"']
    problems: list[str] = []
    if grants is not None and (denial := grants.reminder_denial(scope)):
        return [denial]
    # the TRIGGER: omitted means `action`, the kind every reminder had before D152-C
    kind = store.DEFAULT_KIND if op.get("kind") is None else op.get("kind")
    if (p := checks.kind_problem(kind)) and verb != "delete":
        return [p]          # every further check depends on which moment this watches
    kind = kind if isinstance(kind, str) and kind in store.KINDS else store.DEFAULT_KIND
    # the TARGET: another routine's slug. Setting a hook on someone else is a different act
    # from leaving one for yourself, so it is checked here and refused outright for the two
    # verbs that cannot mean it (a foreign hook is the TARGET's to revise or delete).
    if (target := op.get("target")) is not None:
        from ..ids import is_slug

        if verb != "add":
            problems.append(f"`remind.target` belongs to `op=add` — a hook another routine "
                            f"holds is ITS to {verb}, which is what its disposition does")
        elif not is_slug(str(target or "")):
            problems.append("`remind.target` must be another routine's kebab-case slug — the "
                            "routine whose runs this hook fires in")
        elif scope == "global":
            problems.append("`remind.target` and `scope=global` are two different reaches: a "
                            "global reminder already reaches every routine it is curated for, "
                            "so a target would mean nothing")
    if verb == "add":
        problems += [p for p in (checks.regex_problem(op.get("regex"), kind),
                                 checks.description_problem(op.get("description"))) if p]
        if scope == "global" and (p := checks.reach_problem(op.get("reach"))):
            problems.append(p)
    else:
        if not str(op.get("id") or "").strip():
            problems.append(f"`remind.op={verb}` needs the `id` of the reminder it changes")
        if op.get("regex") is not None and (p := checks.regex_problem(op["regex"], kind)):
            problems.append(p)
        if op.get("description") is not None and (
                p := checks.description_problem(op["description"])):
            problems.append(p)
        if op.get("reach") is not None and (p := checks.reach_problem(op["reach"])):
            problems.append(p)
        if verb == "revise" and all(op.get(k) is None for k in ("regex", "description",
                                                                  "reach", "kind")):
            problems.append("`remind.op=revise` needs what changes: a new `regex`, "
                            "`description`, `kind` or `reach`")
    if scope == "local" and op.get("reach") is not None:
        problems.append("`remind.reach` belongs to a GLOBAL reminder — a local one reaches only "
                        "this routine")
    return problems


def _feedback_problems(fb: object) -> list[str]:
    if not isinstance(fb, dict):
        return [('`remind_feedback` must be an object: {"id": "<reminder id>", '
                 '"label": "could_not|would_have|did|didnt"}')]
    problems = []
    if not str(fb.get("id") or "").strip():
        problems.append("`remind_feedback.id` must name the reminder whose fire you are "
                        "labelling")
    if str(fb.get("label") or "") not in store.LABELS:
        problems.append(f"`remind_feedback.label` must be one of {list(store.LABELS)} — "
                        f"{LABEL_HELP}")
    # the DISPOSITION rides the same field (D152-C step 2): the moment a foreign hook fires is
    # the moment its usefulness to its target is observable, so the say and the label are one act
    if (disp := fb.get("disposition")) is not None and disp not in store.DISPOSITIONS:
        problems.append(f"`remind_feedback.disposition` must be one of "
                        f"{list(store.DISPOSITIONS)} — keep (leave it live), mute (silence it "
                        "for the rest of this run), remove (delete it, and its owner cannot set "
                        "that pattern on you again). It applies only to a hook ANOTHER routine "
                        "set on you")
    return problems
