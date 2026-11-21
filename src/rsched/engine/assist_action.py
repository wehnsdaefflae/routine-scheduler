"""The assist layer's `action` RUNG — a rule that DOES the thing its moment calls for.

`engine/assist.py` owns the moments and their prose deliveries; this owns the one payload that
is not prose — an action the RUN DID NOT CHOOSE, with its own reporting and refusal renderer.
`hooks/perform.py` is the channel that performs it.

Three properties hold:

- **It performs and REPORTS.** The assist's line is appended whatever the action does, with the
  outcome beside it. No veto ever: only a hold can stop an action, so there is no branch on
  success here and there must never be one.
- **It can never exceed the firing routine.** `hooks/perform` runs the action through the model's
  own gates (schema → `validate_action` → `actionroute.dispatch_action`), so a routine without
  the capability performs nothing and the refusal is what the run reads.
- **It cannot fail the turn.** `perform` absorbs every failure into the observation it returns.
"""

from __future__ import annotations

from ..assists import Assist
from . import enginenote
from .observations import is_failure


def act(loop, assist: Assist, *, point: str = "turn-boundary") -> None:
    """Perform an `action`-payload assist's action, and tell the run what came of it.

    `point` (`turn-boundary` or `child-exit`) is passed through, not assumed: `perform` validates
    the payload against that point's own table, so a moment cannot silently acquire the rung.
    """
    if assist.payload != "action" or not assist.action:
        return
    from ..hooks import perform as perform_mod
    obs = perform_mod.perform(loop, dict(assist.action), point=point,
                              source=f"rule:{assist.key}")
    enginenote.append(loop, outcome(assist, obs), assists=[assist.key])


def outcome(assist: Assist, obs: dict) -> str:
    """What the run reads about an action a rule performed on its behalf.

    It names the rule, the action and the result in one line, so the run can tell an action it
    did not choose from its own. Failure is `observations.is_failure` — the engine's ONE
    classifier, reading every spelling — never a check of `error` alone.
    """
    kind = str((assist.action or {}).get("kind") or "?")
    if is_failure(obs) or obs.get("error"):
        return (f"[RULE {assist.rule} — its `{kind}` action did NOT run] {_why_not(obs)} "
                f"Nothing about your own next action changes; this is reported, not enforced.")
    return (f"[RULE {assist.rule} — it ran a `{kind}` action for you, costing you no turn] "
            f"{_result_line(obs)} (the full rule: read_rule name={assist.rule})")


def _why_not(obs: dict) -> str:
    """Why a performed action did not run, read off the observation a REFUSAL carries.

    Separate from `_result_line`, which ends in "it completed" — a sentence a refusal must never
    produce. Keys in reader order: the engine's message, the refusal's reason, the gate's
    problem list, then the flag itself.
    """
    for key in ("error", "reason", "output"):
        if value := str(obs.get(key) or "").strip():
            return value[:400]
    problems = obs.get("problems")
    if isinstance(problems, list) and problems:
        return "; ".join(str(p) for p in problems)[:400]
    flags = [k for k in ("header_ok", "lint_ok", "selftest_ok") if obs.get(k) is False]
    named = [k for k in ("missing", "rejected", "declined", "edit_failed") if obs.get(k)]
    if flags or named:
        return f"the engine refused it ({', '.join(flags + named)})."
    return f"it reported a failure ({obs.get('kind') or 'no detail'})."


def _result_line(obs: dict) -> str:
    """The one line of a performed action's observation the note carries.

    Deliberately short: the full record is in the transcript, which `perform` wrote.
    """
    for key in ("stdout", "text", "result", "summary"):
        value = str(obs.get(key) or "").strip()
        if value:
            return value[:400]
    return f"it completed ({obs.get('kind') or 'no result text'})."
