"""The filing check's VALUE half — a `config_patch` field whose value the apply would refuse.

`config_bridge.py` judges a patch's KEYS: which surface it is for, which fields that surface
has, which inner keys each mapping field takes. This module judges the VALUES of the three
fields whose value can be judged without the web layer — and that is a different question, with
a different failure mode: a patch whose keys are all real reaches the Decisions page wearing an
apply button, and the operator finds out it cannot work by pressing it.

Split out of `config_bridge` when adding the second and third check pushed that file to 355
lines, over the house bar (`tests/test_file_size.py`: "a file at or under the bar may never
cross it. An addition belongs in a new file, which is what 'split rather than grow' means").

The standing limit on every check here: **never stricter than the apply.** A filing check that
refuses a proposal the operator's click would have taken is worse than the dead button it
prevents — it loses a change nobody can see was lost. So a value needing server state the
engine cannot reach (a model's context-window fit, `web/model_fit.model_window_problem`) stays
with the click, and `null` keeps meaning "clear it" exactly as `api_routine_patch` reads it.
"""

from __future__ import annotations

#: What a model trial looks like, said in the refusal so the next filing gets it right.
TRIAL_SHAPE = ('{"trial": {"id": "t-<yyyymmdd>-<model>", "models": {"main": "<catalog model '
               'name>"}, "efforts": {"main": "<low|medium|high|xhigh|max>"} (optional), '
               '"runs": <1-20>, "reason": "<what the trial should show>"}}')


def _why(exc) -> str:
    """A pydantic ValidationError as one operator-readable clause per bad field."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'value'}: "
                     f"{e['msg'].removeprefix('Value error, ')}" for e in exc.errors())


def trial_value(server, patch: dict | None) -> str:
    """Refuse a `trial` VALUE the apply would refuse — the first value judged at filing,
    because a trial is a proposal config-optimizer files for routines it does not run, and a
    malformed one or a model the catalog lacks is a 422/400 on the operator's click.
    The shape is `config/trialconf.TrialConfig`, the catalog is `rsched.trials`; the window fit
    the PATCH also checks lives in the web layer and is left to the click. `null` (clear the
    trial) always passes.
    """
    if not patch or patch.get("trial") is None:
        return ""
    from pydantic import ValidationError

    from ..config.trialconf import TrialConfig
    from ..trials import catalog_problem

    try:
        trial = TrialConfig.model_validate(patch["trial"])
    except ValidationError as exc:
        return (f"config_patch trial: {_why(exc)}. A trial is {TRIAL_SHAPE}; "
                '{"trial": null} clears one.')
    if problem := catalog_problem(server, trial):
        return f"config_patch {problem}. `list_models` names the catalog."
    return ""


def outcome_value(patch: dict | None) -> str:
    """Refuse an `outcome` VALUE the apply would refuse — the outcome contract, judged at
    filing like the trial, because its shape is config (`config/outcome.py`) and needs no
    server state at all.

    `config_bridge.patch_shape` passes `outcome` (it is a real `RoutinePatch` field) and
    `patch_nested` does not look inside it, so the whole contract reached the Decisions page
    unjudged. Measured 2026-11-06: a proposal carrying SEVEN measures sat on the Questions page
    and the operator pressed *approve & apply* five times between 02:25 and 02:34, each click
    answering `measures: List should have at most 6 items after validation, not 7`
    (`.ui-traces/20261106.jsonl`, `view=questions`). The cap is `outcome.MAX_MEASURES` — "more
    measures than a person reads at a glance is a dashboard, not an outcome" — so the seventh
    measure was never the thing to argue about at the click: the proposal was unfileable.

    `null` (clear the contract) always passes: `api_routine_patch` reads it as the one spelling
    of "clear it", exactly as for the trial.
    """
    if not patch or patch.get("outcome") is None:
        return ""
    from pydantic import ValidationError

    from ..config.outcome import MAX_MEASURES, OutcomeConfig

    try:
        OutcomeConfig.model_validate(patch["outcome"])
    except ValidationError as exc:
        return (f"config_patch outcome: {_why(exc)}. An outcome contract is "
                '{"outcome": {"principal": "<who the results are for>", "measures": '
                '[{"id": "<key scripts/outcome.py prints>", "label": "<what a person reads>", '
                '"kind": "count|level", "better": "higher|lower"}, …]}} with at most '
                f'{MAX_MEASURES} measures; {{"outcome": null}} clears one.')
    return ""


def role_value(server, patch: dict | None) -> str:
    """Refuse a `models` role VALUE this instance cannot serve — the same dead apply button as
    the trial's catalog check, one layer in.

    `config_bridge.patch_nested` judges the role NAMES against `ROUTINE_MODEL_ROLES` and stops
    there, so a model name the catalog does not hold reached the operator intact. Measured
    2026-11-06: a proposal binding `tool_call` to a model this instance does not serve drew
    four *approve & apply* clicks between 02:33 and 02:34, each answering `models.tool_call:
    must be a catalog model name` (`.ui-traces/20261106.jsonl`). With the `outcome` cap above,
    nine wasted clicks on one page in nine minutes.

    Mirrors `web/config_fields.validate_models` and is deliberately no stricter:

    - the two DECISION roles name a DECISION model (`server.decision_models`), not a chat one,
      so they are judged against that catalog. This is why `trials.roles_problem` is NOT reused
      here despite asking the same question of a routine's STANDING roles: it checks every role
      against the chat catalog, which would refuse a valid `decision` proposal.
    - blanking a role (`""`/`null`) is how the apply CLEARS it back to the instance default, so
      an empty value passes.
    - the model's WINDOW fit stays with the click (`web/model_fit.model_window_problem`).
    """
    if not patch:
        return ""
    roles = patch.get("models")
    if not isinstance(roles, dict):
        return ""
    from ..config.base import DECISION_ROLES

    for role, name in sorted(roles.items()):
        if name in ("", None):
            continue
        decision = role in DECISION_ROLES
        catalog = server.decision_models if decision else server.models
        if isinstance(name, str) and name in catalog:
            continue
        which = "decision model (Settings → Decision endpoints)" if decision else "catalog model"
        known = ", ".join(sorted(catalog)) or "none configured"
        return (f"config_patch models.{role}: {name!r} is not a {which} this instance serves — "
                f"the apply would refuse the whole patch and the user would be left with a "
                f"decision he can read and not take. `list_models` names the catalog; here: "
                f"{known}. A blank value clears the role to the instance default.")
    return ""
