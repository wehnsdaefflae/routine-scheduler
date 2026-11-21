"""The filing check's VALUE half — a `config_patch` field whose value the apply would refuse.

`config_bridge.py` judges a patch's KEYS: which surface it is for, which fields that surface
has, which inner keys each mapping field takes. This module judges the VALUES of the fields
whose value can be judged without the web layer: a patch whose keys are all real otherwise
reaches the Decisions page with an apply button that cannot work.

The standing limit on every check here: **never stricter than the apply** — refusing a proposal
the click would have taken loses a change invisibly. So a value needing server state the engine
cannot reach (a model's context-window fit, `web/model_fit.model_window_problem`) stays with the
click, and `null` keeps meaning "clear it" exactly as `api_routine_patch` reads it.
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
    """Refuse a `trial` VALUE the apply would refuse (bad shape, or a model the catalog lacks).

    The shape is `config/trialconf.TrialConfig`, the catalog is `rsched.trials`; the window fit
    is left to the click. `null` (clear the trial) always passes.
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
    """Refuse an `outcome` VALUE the apply would refuse (shape per `config/outcome.py`).

    `config_bridge.patch_nested` does not look inside `outcome`, so without this an unfileable
    contract (e.g. over `outcome.MAX_MEASURES`) reaches the click. `null` (clear) always passes.
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
    """Refuse a `capabilities` VALUE the apply would silently FLOOR AWAY — the apply reports
    success and writes the mapping without it.

    The apply (`api_routine_patch._apply_permissions_fields` → `write_permission_layers`)
    floors the mapping to the held docs' requires (D8): a gated action survives only while some
    HELD permission's `requires:` names it, so a patch naming only `capabilities` cannot grant
    one the routine's docs do not cover.

    No stricter than the apply, by construction: the floor compared against IS the apply's own
    (`grants.floor_capabilities` over `grants.capabilities_for`) over the same doc set. A patch
    that also carries the covering `permissions` passes.
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
    """Refuse a `models` role VALUE this instance cannot serve (`patch_nested` judges only
    the role NAMES).

    Mirrors `web/config_fields.validate_models` and is deliberately no stricter:

    - the DECISION roles are judged against `server.decision_models`, not the chat catalog —
      which is why `trials.roles_problem` (chat catalog for every role) is not reused.
    - blanking a role (`""`/`null`) clears it to the instance default, so it passes.
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
