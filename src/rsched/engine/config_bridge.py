"""The CONFIG BRIDGE's filing check — what a `config_patch` riding a decision record is FOR, and
whether the apply that will carry it out would take it.

A run cannot write routine.yaml: it proposes the change on an `ask_user` and the Decisions page
applies it with one click. The checks run where the durable record is WRITTEN
(`interact.handle_ask`), never at apply time, so a record never reaches the operator wearing an
apply button that cannot work.

This file holds the KEY half — the target surface (`patch_target`), its fields (`patch_shape`,
`server_shape`) and mapping fields' inner keys (`patch_nested`). The VALUE half is
`config_values.py`. `resolve` composes both and is the one entry point.
"""

from __future__ import annotations

from pathlib import Path

from . import runkind

# The VALUE half, re-exported: callers import it from this module.
from .config_values import (
    TRIAL_SHAPE,
    capability_value,
    outcome_value,
    role_value,
    trial_value,
)

__all__ = ["TRIAL_SHAPE", "capability_value", "live_fields", "outcome_value", "patch_nested",
           "patch_shape",
           "patch_target", "resolve", "role_value", "server_shape", "trial_value"]


def resolve(ctx, cpatch: dict | None) -> tuple[str, str]:
    """`(target, error)` for the `config_patch` an ask carries: the target first — it POPS the
    routing key — then the body, judged in the vocabulary of the surface that target means.
    `target` is "" for the asker itself; a non-empty `error` refuses the ask before any record
    exists.
    """
    conversation = runkind.lands_in_conversation(ctx)
    target, error = patch_target(ctx, cpatch, conversation=conversation)
    return target, (error or patch_shape(cpatch, target, conversation=conversation)
                    or patch_nested(cpatch) or outcome_value(cpatch)
                    or role_value(ctx.server, cpatch) or trial_value(ctx.server, cpatch)
                    or capability_value(ctx, cpatch, target))


def live_fields(ctx, cpatch: dict | None, target: str = "") -> dict:
    """What a LIVE blocking question must carry so the surface the operator is READING can
    offer the patch's one apply control — or `{}` when the ask carries no patch.

    Everything rendered while the run WAITS (the pinned panels and the Decisions page's live
    row) is built from the status.json copy, not the durable record, so a patch missing here
    reaches no surface. The fields are what `static/components/configproposal.js` routes on:
    the PATCH body, the resolved target (absent = the asker) and the home. Absent for an
    ordinary ask.
    """
    if not cpatch:
        return {}
    return {"config_patch": cpatch, "routine": ctx.routine.slug,
            **({"config_target": target} if target else {}),
            **({"conversation": True} if runkind.lands_in_conversation(ctx) else {})}


def patch_shape(patch: dict | None, target: str = "", *, conversation: bool = False) -> str:
    """Refuse a `config_patch` whose KEYS the apply route would reject, at the moment it is
    filed rather than when the operator clicks.

    The keys are judged in the vocabulary of the surface the apply will PATCH — `RoutinePatch`
    or, for a conversation's proposal about itself, `ConversationPatch` — never their union.
    `configflow` spells both sets out and `tests/test_configflow.py` pins them to the models.
    An empty patch is left to the caller.
    """
    if not patch:
        return ""
    from ..configflow import CONVERSATION_PATCH_FIELDS, ROUTINE_PATCH_FIELDS, SERVER_KEY
    if SERVER_KEY in patch:
        return server_shape(patch, target)
    # `patch_target` already POPPED the routing key: the surface comes from the target
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

    Judged against the closed vocabularies in `config/base.py` (`DEFAULT_BUDGETS`,
    `ROUTINE_MODEL_ROLES`, `MODEL_KINDS`, `EFFORT_LEVELS`), the same for both patch surfaces. A
    VALUE needing server state stays with the click: a filing check stricter than the apply
    would refuse a proposal that works.
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
    """Refuse a `{"server": {...}}` patch (the INSTANCE surface) the apply route would reject.

    Refused: a body mixing `server` with routine keys (one patch, one route — a mixed body
    would half-land); a `server` value that is not a non-empty mapping; a key outside the
    `SERVER_PATCH_FIELDS` ALLOW-LIST. The apply route enforces the allow-list AGAIN — that is
    the boundary; this only makes the refusal arrive on the filing turn.
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

    D123: the slug rides INSIDE the patch as `routine` (keeping `config_patch` one schema
    object) and is popped here so the rest is a clean PATCH body. An unknown slug is refused on
    the asking turn; falling back to the asker is never done. The target resolves against the
    ROUTINES home whoever asks; a `routine` naming the asker means the asker itself only when
    the asker IS a routine.
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
