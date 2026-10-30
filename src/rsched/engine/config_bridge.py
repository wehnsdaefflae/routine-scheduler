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
                    or patch_nested(cpatch) or trial_value(ctx.server, cpatch))


def live_fields(ctx, cpatch: dict | None, target: str = "") -> dict:
    """What a LIVE blocking question must carry so the surface the operator is READING can
    offer the patch's one apply control — or `{}` when the ask carries no patch.

    F807/R3060: the durable record holds the patch (`inbox.file_question`), but everything
    rendered while the run WAITS is built from the smaller copy `interact.handle_ask` publishes
    into status.json: the conversation's pinned panel and the run page's panel
    (`static/components/answerform.js` `questionPanel`, fed by the state stream), and the
    Decisions page's own row for a live blocking question — `web/decisions_read` builds that
    from `active.question` and skips the durable pending file by qid. A patch missing here
    therefore reached NO surface at all while the run blocked: the operator read a blocking
    question where the run was waiting for him and had no way to apply what it proposed.

    The fields are the ones `static/components/configproposal.js` routes on: the PATCH body,
    the resolved target (absent = the asker itself, D123/F458) and the home that decides which
    endpoint applies it. Absent for an ordinary ask, so no reader learns to expect them and no
    card grows a control over a proposal that is not there.
    """
    if not cpatch:
        return {}
    return {"config_patch": cpatch, "routine": ctx.routine.slug,
            **({"config_target": target} if target else {}),
            **({"conversation": True} if runkind.lands_in_conversation(ctx) else {})}


#: What a model trial looks like, said in the refusal so the next filing gets it right.
TRIAL_SHAPE = ('{"trial": {"id": "t-<yyyymmdd>-<model>", "models": {"main": "<catalog model '
               'name>"}, "efforts": {"main": "<low|medium|high|xhigh|max>"} (optional), '
               '"runs": <1-20>, "reason": "<what the trial should show>"}}')


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
    from ..configflow import CONVERSATION_PATCH_FIELDS, ROUTINE_PATCH_FIELDS, SERVER_KEY
    if SERVER_KEY in patch:
        return server_shape(patch, target)
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


def patch_nested(patch: dict | None) -> str:
    """Refuse a mapping field whose INNER keys the apply would reject — the same dead button
    one level down.

    `patch_shape` judges the patch's own keys, so `{"budgets": {...}}` passed it whatever the
    mapping held. Measured 2026-10-20: `llmsectest-weekday` filed
    `{"budgets": {"max_turns": 600, "max_minutes": 900}}`, the apply answered
    `budgets: Value error, unknown budget max_minutes`, and the operator pressed
    `approve & apply` FOUR times before typing the toast text back to the run by hand
    (`.ui-traces/20261020.jsonl`, 04:39:18-04:39:39). `max_minutes` is not a key the repo has
    ever had — the run invented it, and nothing between the invention and his click said so.

    Judged against the closed vocabularies in `config/base.py`: the budget names the loader
    keeps (`DEFAULT_BUDGETS`, the same list `web/config_fields._check_budgets` reads), the model
    roles (`ROUTINE_MODEL_ROLES`), the chat roles `efforts` takes (`MODEL_KINDS`) and its levels
    (`EFFORT_LEVELS`). Both patch surfaces spell these three fields the same way, so there is
    one answer for a routine and a conversation alike. A VALUE needing server state — a catalog
    model name, a window fit — stays with the click, as `trial_value` says for the trial: the
    engine may not import the web layer, and a filing check stricter than the apply would refuse
    a proposal that works.
    """
    if not patch:
        return ""
    from ..config.base import (
        DEFAULT_BUDGETS,
        EFFORT_LEVELS,
        MODEL_KINDS,
        ROUTINE_MODEL_ROLES,
    )

    for field, known in (("budgets", tuple(DEFAULT_BUDGETS)),
                         ("models", ROUTINE_MODEL_ROLES),
                         ("efforts", MODEL_KINDS)):
        value = patch.get(field)
        if not isinstance(value, dict):
            continue
        if stray := sorted(k for k in value if k not in known):
            return (f"config_patch {field}: {', '.join(repr(k) for k in stray)} "
                    f"{'is' if len(stray) == 1 else 'are'} not a {field[:-1]} this instance has "
                    f"— expected one of {', '.join(known)}. The apply would refuse the whole "
                    f"patch and the user would be left with a decision he can read and not "
                    f"take.")
    levels = patch.get("efforts")
    if isinstance(levels, dict) and (bad := sorted(
            f"{role}={level!r}" for role, level in levels.items()
            if level not in ("", None) and level not in EFFORT_LEVELS)):
        return (f"config_patch efforts: {', '.join(bad)} — a reasoning effort is one of "
                f"{', '.join(EFFORT_LEVELS)}, or null for the provider's own default.")
    return ""


def server_shape(patch: dict, target: str = "") -> str:
    """Refuse a `{"server": {...}}` patch the apply route would reject — the INSTANCE surface
    (operator, 2026-10-19), judged here like the other two so a server proposal never reaches
    the Decisions page wearing a button that cannot work.

    Three refusals, each a state the apply could not land:

    - a body mixing `server` with routine keys. One patch, one surface, one PATCH route: a
      mixed body would need two applies, and the half that landed would settle the record as
      handled while the other half silently did not.
    - a `server` value that is not a mapping, or is empty. `{"server": {}}` reads as a change
      and applies nothing.
    - a key outside `SERVER_PATCH_FIELDS`. That set is an ALLOW-LIST of fields with a
      validated runtime writer behind them, so the auth tokens, the bind address, the homes
      and the endpoint/model/machine/decision CATALOGS are refused by not being in it — the
      catalogs because an entry carries credentials and has its own CRUD routes, not because
      editing them is unthinkable.

    The allow-list is enforced AGAIN in the apply route, which is the half that matters:
    R2702's caution is from the routine that once held a capability it had merely inherited,
    and a check living only in the caller's restraint is not a boundary. This check exists so
    the refusal arrives on the turn that files the ask, with the vocabulary named.
    """
    from ..configflow import SERVER_KEY, SERVER_PATCH_FIELDS

    if target:
        return (f"config_patch names routine {target!r} AND the {SERVER_KEY!r} surface: the "
                f"instance config is nobody's routine. Drop `routine` to propose a server "
                f"change, or drop `{SERVER_KEY}` to propose a routine change.")
    if elsewhere := sorted(k for k in patch if k != SERVER_KEY):
        return (f"config_patch mixes the {SERVER_KEY!r} surface with "
                f"{', '.join(repr(k) for k in elsewhere)}: one patch is applied by ONE route, "
                f"so a mixed body would half-land. File the server change and the routine "
                f"change as two decisions.")
    body = patch[SERVER_KEY]
    if not isinstance(body, dict):
        return (f"config_patch {SERVER_KEY!r} must be an object of instance config fields, "
                f'e.g. {{"{SERVER_KEY}": {{"decision_model": "jev"}}}}.')
    if not body:
        return (f"config_patch {SERVER_KEY!r} is empty: it names no field to apply. "
                f"Patchable here: {', '.join(sorted(SERVER_PATCH_FIELDS))}.")
    if stray := sorted(k for k in body if k not in SERVER_PATCH_FIELDS):
        return (f"config_patch {SERVER_KEY} key(s) {', '.join(repr(k) for k in stray)} are not "
                f"patchable instance config — the apply would refuse them and the user would "
                f"be left with a decision he can read and not take. The catalogs "
                f"(`endpoints`, `models`, `machines`, `decision_endpoints`, `decision_models`) "
                f"have their own settings routes, and the auth tokens, bind address and homes "
                f"are install-time. Patchable here: "
                f"{', '.join(sorted(SERVER_PATCH_FIELDS))}.")
    return ""


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
