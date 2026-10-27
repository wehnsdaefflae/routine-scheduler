"""The assist layer's `action` RUNG — a rule that DOES the thing its moment calls for.

`engine/assist.py` owns the four moments and their deliveries (a held action, an observation
tail, an engine note, a finish deferral); this owns the one payload that is not prose. Split out
rather than added to it for the repo's usual reason — that file is the moment seam and was at the
350-line bar — and for a sharper one: everything here is about an action the RUN DID NOT CHOOSE,
which needs its own vocabulary of reporting and its own refusal renderer.

The operator asked for this rung twice. *"Any action. This includes utils and therefore
scripts"* (2026-10-26) said what a hook may run, and *"The hook system also should allow to
extend the implementation of parts of the harness^2 features we already started"* said why it
matters: a rule that can only SAY something at the right moment leaves the acting to a model turn
that may not come. `assists.py` recorded the rung as unbuilt "because each needs a helper channel
that is not built" — `hooks/perform.py` is that channel.

Three properties hold here, none of them this module's own invention:

- **It performs and REPORTS.** The assist's line is appended whatever the action does, and the
  outcome beside it. A failure changes nothing about what the run then does — his second ruling
  the same day: *"No veto ever — a hook performs and reports; only a hold (prose to the model)
  can stop an action."* There is no branch on success here, and there must never be one.
- **It can never exceed the firing routine.** `hooks/perform` runs the action through the model's
  own gates (schema → `validate_action` against the run's workflow tools ∩ capabilities →
  `actionroute.dispatch_action`), so a rule held by a routine without the capability performs
  nothing and the refusal is what the run reads.
- **It cannot fail the turn.** `perform` absorbs every failure into the observation it returns,
  so the worst case is a note saying so.
"""

from __future__ import annotations

from ..assists import Assist
from . import enginenote
from .observations import is_failure


def act(loop, assist: Assist) -> None:
    """Perform an `action`-payload assist's action, and tell the run what came of it."""
    if assist.payload != "action" or not assist.action:
        return
    from ..hooks import perform as perform_mod
    obs = perform_mod.perform(loop, dict(assist.action), point="turn-boundary",
                              source=f"rule:{assist.key}")
    enginenote.append(loop, outcome(assist, obs), assists=[assist.key])


def outcome(assist: Assist, obs: dict) -> str:
    """What the run reads about an action a rule performed on its behalf.

    It names the rule, the action and the result in one line, because the run did not choose
    this action and has to be able to tell it from its own: an unexplained observation in a
    transcript is indistinguishable from a turn the model forgot taking.

    Failure is `observations.is_failure`, never a check of `error` alone — that is the engine's
    ONE classifier for "did this observation report a failure", and the only thing that reads
    every spelling: a refusal's `declined`/`reason`, a non-zero `exit`, a failed gate flag, a
    `paths` batch where one file of eight is unreadable. The first version tested `error` and
    told the run a DECLINED `write_util` "completed" — which also found the classifier's own
    gap (`header_ok`, fixed in `observations.is_failure` the same day).
    """
    kind = str((assist.action or {}).get("kind") or "?")
    if is_failure(obs) or obs.get("error"):
        return (f"[RULE {assist.rule} — its `{kind}` action did NOT run] {_why_not(obs)} "
                f"Nothing about your own next action changes; this is reported, not enforced.")
    return (f"[RULE {assist.rule} — it ran a `{kind}` action for you, costing you no turn] "
            f"{_result_line(obs)} (the full rule: read_rule name={assist.rule})")


def _why_not(obs: dict) -> str:
    """Why a performed action did not run, read off the observation a REFUSAL carries.

    Separate from `_result_line` deliberately: that one renders a RESULT and ends in "it
    completed", which is the exact sentence a refusal must never produce. The first version fell
    back to it, so a `write_util` the doc-standard gate refused was reported as "its action did
    NOT run — it completed". Failure is spelled per kind (`observations._FAILURE_KEYS`), so the
    keys are read in the order a reader wants them: the engine's own message, the refusal's
    reason, the gate's problem list, then the flag itself.
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

    Deliberately short and deliberately not the whole observation: the full record is in the
    transcript, which `perform` wrote, and a boundary note is read by a model about to do
    something else.
    """
    for key in ("stdout", "text", "result", "summary"):
        value = str(obs.get(key) or "").strip()
        if value:
            return value[:400]
    return f"it completed ({obs.get('kind') or 'no result text'})."
