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


def capability_value(ctx, patch: dict | None, target: str = "") -> str:
    """Refuse a `capabilities` VALUE the apply would silently FLOOR AWAY — the one dead-button
    shape where the button is not dead: it works, reports success, and does not do what the
    patch said.

    `capabilities` is a real `RoutinePatch` field, so `patch_shape` passes it and
    `patch_nested` does not look inside. The apply routes it to the permissions surface
    (`api_routine_patch._apply_permissions_fields` → `write_permission_layers`), which RAISES
    the mapping to cover every held doc's requires and FLOORS it back to them (D8): a gated
    action survives only while some HELD permission's `requires:` names it. A patch naming
    only `capabilities` keeps the routine's current docs, so a gated action with no permission
    behind it cannot land — and nothing says so.

    Measured 2026-11-06: a proposal carrying `{"capabilities": {"actions": ["write_recipe"]}}`
    for `deficit-coach` (which holds `util-authoring` alone) was applied at 03:25:57 and
    recorded `config_patch_applied: true`. The written file came out
    `actions: [write_util, revise_util]` — `write_recipe` floored away. The asking routine
    reported to the target that the capability had been granted; the target's next run tried
    `edit_file main.md`, was refused for want of it, and filed a BLOCKING question (qid
    q-20261106-230001-55) asking for a grant it had already been told it had.

    No stricter than the apply, by construction: the floor this compares against IS the apply's
    own (`grants.floor_capabilities` over `grants.capabilities_for`), run against the same doc
    set the apply would use. A patch that also carries the covering `permissions` passes, which
    is the correct filing and what the refusal asks for.
    """
    if not patch or not isinstance(patch.get("capabilities"), dict):
        return ""
    from pathlib import Path

    from ..config.routine import load_routine
    from ..grants import _DEFAULT_KIND_SOURCE, capabilities_for, floor_capabilities
    from ..readmodels import library_reads

    home = Path(ctx.server.routines_home)
    rdir = home / target if target else ctx.root_routine_dir
    cfg, _ = load_routine(rdir)
    if cfg is None:
        return ""
    want_docs = patch.get("permissions")
    if not isinstance(want_docs, list):
        want_docs = list(cfg.permissions or [])
    available = set(library_reads.doc_slugs(ctx.server.permissions_home))
    active = [p for p in want_docs if isinstance(p, str) and p in available]
    lib = library_reads.requires(ctx.server.permissions_home)
    asked = patch["capabilities"]
    kept = floor_capabilities(active, lib, capabilities_for(active, lib, dict(asked)))
    for key in ("actions", "utils"):
        survived = set(kept.get(key) or [])
        for name in asked.get(key) or []:
            if not isinstance(name, str) or name in survived:
                continue
            carriers = sorted(s for s, req in lib.items() if name in (req.get(key) or []))
            doc = _DEFAULT_KIND_SOURCE.get(name) if key == "actions" else None
            if doc and doc not in carriers:
                carriers.append(doc)
            which = " or ".join(repr(c) for c in carriers) or "no library permission"
            return (f"config_patch capabilities.{key}: {name!r} would be floored away — a "
                    f"gated capability survives only while a HELD permission requires it, and "
                    f"{(target or rdir.name)!r} holds {active or 'none'}. The apply would "
                    f"report success and write the mapping without it. Carry the covering "
                    "permission in the same patch: "
                    f'{{"permissions": {[*active, carriers[0]] if carriers else active}, '
                    f'"capabilities": …}} — {which} requires {name!r}.')
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
