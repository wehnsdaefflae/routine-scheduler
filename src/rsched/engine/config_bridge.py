"""The CONFIG BRIDGE's filing check — what a `config_patch` riding a decision record is FOR, and
whether the apply that will carry it out would take it.

A run cannot write routine.yaml: it proposes the change on an `ask_user` and the Decisions page
applies it with one click (engine/revise.py routes a config-shaped request there). Both checks
run where the durable record is WRITTEN (`interact.handle_ask`), never at apply time, because a
record must never reach the operator wearing an apply button that cannot work.

Split out of `interact.py`, whose one responsibility is the ask itself — filing a decision record
and turning the reply back into an answer. Whether one optional field of that record is
well-formed is a different question, and it is this module's.
"""

from __future__ import annotations

from pathlib import Path

from . import runkind


def resolve(ctx, cpatch: dict | None) -> tuple[str, str]:
    """`(target, error)` for the `config_patch` an ask carries: the target first — it POPS the
    routing key — then the body, judged in the vocabulary of the surface that target means.
    `target` is "" for the asker itself; a non-empty `error` refuses the ask before any record
    exists.
    """
    conversation = runkind.lands_in_conversation(ctx)
    target, error = patch_target(ctx, cpatch, conversation=conversation)
    return target, (error or patch_shape(cpatch, target, conversation=conversation)
                    or trial_value(ctx.server, cpatch))


#: What a model trial looks like, said in the refusal so the next filing gets it right.
TRIAL_SHAPE = ('{"trial": {"id": "t-<yyyymmdd>-<model>", "models": {"main": "<catalog model '
               'name>"}, "runs": <1-20>, "reason": "<what the trial should show>"}}')


def trial_value(server, patch: dict | None) -> str:
    """Refuse a `trial` VALUE the apply would refuse — the one key whose value is judged at
    filing, because a trial is a proposal config-optimizer files for routines it does not
    run, and a malformed one or a model the catalog lacks is a 422/400 on the operator's click.
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
        why = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'trial'}: "
                        f"{e['msg'].removeprefix('Value error, ')}" for e in exc.errors())
        return (f"config_patch trial: {why}. A trial is {TRIAL_SHAPE}; "
                '{"trial": null} clears one.')
    if problem := catalog_problem(server, trial):
        return f"config_patch {problem}. `list_models` names the catalog."
    return ""


def patch_shape(patch: dict | None, target: str = "", *, conversation: bool = False) -> str:
    """Refuse a `config_patch` whose KEYS the apply route would reject, at the moment it is
    filed rather than when the operator clicks.

    The target is checked first (`patch_target`); the BODY was not, so a run could invent a
    shape and the record reached the Decisions page wearing an apply button that 422s.
    Measured 2026-09-23: a proposed filesystem grant arrived as
    `{"filesystem": {"read": [...]}}`, the apply answered `filesystem: Extra inputs are not
    permitted`, and the operator had a decision he could read and not take — the one state a
    decision surface must never be in.

    The keys are judged in the vocabulary of the surface the apply will PATCH: `RoutinePatch`
    for a routine, `ConversationPatch` for a conversation's proposal about itself
    (`conversation`, from `runkind.lands_in_conversation`). Judged against their union — the
    classification table — a routine proposal naming `title` passed here, as did a
    conversation's naming `schedule`; each was a 422 on the click. `configflow` spells both
    sets out and `tests/test_configflow.py` pins them to the models, so this can never drift
    behind the endpoint it stands in for. Routing keys are not fields; an empty patch is left
    to the caller.
    """
    if not patch:
        return ""
    from ..configflow import CONVERSATION_PATCH_FIELDS, ROUTINE_PATCH_FIELDS
    # `patch_target` has already run and POPPED the routing key, so the surface is read from
    # the target it resolved, never from the body — which no longer says
    if conversation and not target:
        # the asker's own conversation; a resolved routine target is judged as a routine
        known, surface = set(CONVERSATION_PATCH_FIELDS), "conversation config"
    else:
        known, surface = set(ROUTINE_PATCH_FIELDS) | {"routine"}, "routine config"
    stray = sorted(k for k in patch if k not in known)
    if not stray:
        return ""
    return (f"config_patch key(s) {', '.join(repr(k) for k in stray)} are not {surface} — the "
            f"apply would refuse them and the user would be left with a decision he can read "
            f"and not take. The body IS the PATCH body, so filesystem roots are "
            f"`fs_read_roots` and `fs_write_roots` (flat lists of paths), not a nested object. "
            f"Valid keys here: {', '.join(sorted(known))}.")


def patch_target(ctx, cpatch: dict | None, *, conversation: bool = False) -> tuple[str, str]:
    """What a `config_patch` is FOR — its own asker unless the patch names another routine.

    Returns `(target, error)`. `target` is `""` when the patch is for the asker itself.

    D123/F458. A run proposes a config change it cannot make itself; the Decisions page applies
    it. Before 0.326.0 that apply was hardwired to the ASKING routine, so config-optimizer —
    whose whole job is per-routine config — could not land a single change on a target: its
    suedlink-wlf budget proposal rewrote config-optimizer's own budgets and reported success
    (R1343, R1407). The slug rides INSIDE the patch as `routine`, which keeps `config_patch`
    one object in the action schema, and is popped here so the remaining keys stay a clean
    PATCH body the endpoint's `extra="forbid"` accepts.

    Resolution happens at this seam, not at apply time, because an unresolvable target must
    never reach the user wearing a working button. A name matching no installed routine is
    refused on the turn that asked. Falling back to the asker is the defect itself and is never
    done.

    The target resolves against the ROUTINES home whoever asks, because that is where routines
    live. Resolved under the asker's own home — a conversation's is the conversations home —
    every routine a conversation named was refused; a slug that matched another conversation
    reached the Decisions page, which posted the patch to `/api/routines/<the asking
    conversation>`: a 404. A `routine` naming the asker means the asker itself only when the
    asker IS a routine (`conversation`: the record lands in a conversation,
    `runkind.lands_in_conversation`).
    """
    if not cpatch:
        return "", ""
    want = str(cpatch.pop("routine", "") or "").strip()
    if not want or (want == ctx.root_routine_dir.name and not conversation):
        return "", ""
    home = Path(ctx.server.routines_home)
    if not (home / want / "routine.yaml").exists():
        return "", (f"config_patch routine {want!r}: no such routine under {home} — the "
                    "target must name an installed routine by its slug, as the Routines "
                    "page lists it. Omit `routine` to propose the change for yourself.")
    return want, ""
