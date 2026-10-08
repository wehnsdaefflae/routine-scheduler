"""The config fields a ROUTINE and a CONVERSATION both carry, validated once.

A conversation is routine-shaped — same `routine.yaml`, same model roles, same connection
and machine bindings, same budgets and folder grants — so its PATCH and the routine PATCH
were two copies of the same five checks, and copies drift. (A model trial's check is
routine-only — a conversation has no fires to try a model on — and lives here beside the
model check it repeats.) They already had: the
conversation path refused a model whose own `max_tokens` fills its context window
(R112/R128 — the next completion dies with `context_length_exceeded`) and the routine path
did not, so a routine could be bound to a model that cannot run a turn and the failure
arrived at 3am in a scheduled run instead of as a 400 at the click.

Every function here raises `HTTPException` with the message the operator reads, and
returns the value to write. `BudgetsPatch` is the typed half — see its own note.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException
from pydantic import AfterValidator

from .. import entities
from ..config import DECISION_ROLES, DEFAULT_BUDGETS, ROUTINE_MODEL_ROLES
from ..paths import expand
from .model_fit import model_window_problem


def _check_budgets(value: dict[str, int]) -> dict[str, int]:
    """Every named budget must be one the loader keeps, and an integer.

    `DEFAULT_BUDGETS` is the one list of names — read, never copied, so a new budget needs
    no edit here. -1 is legal and means unlimited wherever a budget honors it, which is why
    the bound is "an integer" and not "a positive integer".
    """
    unknown = [k for k in value if k not in DEFAULT_BUDGETS]
    if unknown:
        raise ValueError(f"unknown budget {', '.join(sorted(unknown))} "
                         f"(expected one of {', '.join(DEFAULT_BUDGETS)})")
    return dict(value)


#: The budgets a PATCH may set, in the ONE spelling both the routine and the conversation
#: patch models use. Typed rather than a free mapping because the loader DROPS an unknown
#: budget key and `_validate_lenient` drops a non-integer value, which then reverts to its
#: default — so an untyped `budgets` let the endpoint answer `updated: ["budgets"]` for a
#: change the next scan reverted, which is exactly the silent ignore R102 forbids. (The
#: conversation patch had the mirror defect: a bare `int(v)` raising an uncaught
#: ValueError, i.e. a 500 with no detail where every sibling route 422s.)
BudgetsPatch = Annotated[dict[str, int], AfterValidator(_check_budgets)]


def validate_models(server, mapping: dict | None) -> dict:
    """Model-role bindings: a known role, a catalog model NAME, and a window that can
    actually run a turn. REPLACE wholesale, so blanking a role clears it back to the
    instance `system_model`. The two DECISION roles name a decision model instead
    (`_decision_role_problem`) and fall back to the instance's decision defaults.
    """
    for kind, name in (mapping or {}).items():
        if kind not in ROUTINE_MODEL_ROLES:
            raise HTTPException(400, f"unknown model kind {kind!r} (expected one of "
                                     f"{ROUTINE_MODEL_ROLES})")
        if kind in DECISION_ROLES:
            if wrong := _decision_role_problem(server, kind, name):
                raise HTTPException(400, wrong)
            continue
        if not isinstance(name, str) or name not in server.models:
            raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        if problem := model_window_problem(server, name):
            # R112/R128: the model's own output maximum fills its context window, so the
            # first completion would die on `context_length_exceeded`. Refuse at the click.
            raise HTTPException(400, problem)
    return dict(mapping or {})


def validate_trial(server, value: dict) -> dict:
    """A MODEL TRIAL (rsched/trials.py), shaped already by `TrialConfig` at the model edge:
    every model it names must be in the catalog and able to run a turn — the two checks a
    `models:` binding meets, because for the trial's runs it IS one. A trial the catalog cannot
    serve would be ignored at every fire, so it is refused here instead. Returns what the file
    holds.
    """
    from ..config.trialconf import TrialConfig
    from ..trials import catalog_problem

    trial = TrialConfig.model_validate(value)
    if problem := catalog_problem(server, trial):
        raise HTTPException(400, f"{problem} — a trial names models Settings → Models lists")
    for name in trial.models.values():
        if window := model_window_problem(server, name):
            raise HTTPException(400, window)
    return trial.model_dump()


def decision_picker(server) -> dict:
    """What the two decision-role pickers need: every decision model with whether it takes
    images, and the instance defaults a blank role falls back to.
    """
    from ..config.decisionconf import multimodal_effective

    return {"decision_catalog": [
                {"name": m.name, "images": multimodal_effective(
                    m, server.decision_endpoints.get(m.endpoint))}
                for m in server.decision_models.values()],
            "decision_defaults": {"decision": server.decision_model or None,
                                  "decision_media": server.decision_media_model or None}}


def _decision_role_problem(server, kind: str, name: object) -> str:
    """Why `name` cannot fill decision role `kind`, or "" — a decision model, and for the image
    role one that takes images (a chat model here would be a name `decide` cannot find).
    """
    from ..config.decisionconf import multimodal_effective

    model = server.decision_models.get(name) if isinstance(name, str) else None
    if model is None:
        return (f"models.{kind}: must be a decision model (Settings → Decision endpoints): "
                f"{', '.join(sorted(server.decision_models)) or 'none configured'}")
    if kind == "decision_media" and not multimodal_effective(
            model, server.decision_endpoints.get(model.endpoint)):
        return (f"models.{kind}: {name!r} takes text only — the image role needs one that "
                "takes images")
    return ""


def validate_connections(mapping: dict | None) -> dict:
    """OAuth connection bindings {provider: account-label}. Existence of the connection is
    deliberately NOT required — a routine may bind ahead of connecting, and the engine
    injects nothing until the account is there. REPLACE wholesale.
    """
    from ..oauth.providers import PROVIDERS

    for provider, account in (mapping or {}).items():
        if provider not in PROVIDERS:
            # Name the FIELD and the way out: the accept is whole-draft, so this 400 refuses
            # every other change the person kept, and a message naming only the provider
            # leaves them guessing which of the proposed blocks to revert.
            raise HTTPException(400, f"connections: unknown provider {provider!r} — the known "
                                     f"providers are {', '.join(sorted(PROVIDERS))}. A mailbox "
                                     "or API reached through a util is not a connection; revert "
                                     "the Connections field to accept the rest.")
        if not isinstance(account, str) or not account:
            raise HTTPException(400, f"connections.{provider}: must be an account label "
                                     "(a string), not a scope list or an object")
    return dict(mapping or {})


def validate_machines(server, names: list | None) -> list[str]:
    """Remote-machine bindings. Unlike connections these DO require catalog membership: a
    machine name off the catalog is meaningless, and the picker only offers catalog names.
    """
    vals = names or []
    if not isinstance(vals, list) or any(not isinstance(n, str) for n in vals):
        raise HTTPException(400, "machines: must be a list of catalog machine names")
    for name in vals:
        if name not in server.machines:
            raise HTTPException(400, f"unknown machine {name!r} (add it in Settings → Machines)")
    return list(vals)


def validate_roots(key: str, values: list | None, *, current: list | None = None) -> list[str]:
    """One folder-grant list (`fs_read_roots` / `fs_write_roots`, or a conversation's
    `workdir` — write root #1), REPLACED wholesale — the ONE enforcer every edge where a grant
    is MADE calls: the routine PATCH, the conversation PATCH and the conversation create form.

    Each entry is stripped of whitespace and a trailing `/`, deduplicated, and returned as the
    raw string the file holds (`~` kept, never expanded here). Refused with a 400 naming it:

    - a path that is not ABSOLUTE once `~`/`$VARS` expand — a relative root would resolve
      against the daemon's working directory, which no operator means;
    - a credential store (SEC-1, `entities.guarded_roots`): the credential FILES in the config
      dir, `~/.credentials` and `~/.ssh` are never grantable, and the never-grantable promise
      lived only on the runtime ask path until a typed root mounted the instance's credential
      dir on a routine. Since D169 the guard names those files rather than the directory, so
      `config.yaml` — ordinary settings, no credential — is clean on its own and needs no
      allowance; the directory is still refused for CONTAINING them.
      A store ALREADY in a file is the loader's to report, never dropped (config/routine.py).
      `current` is what the routine holds for this key RIGHT NOW (the two PATCH edges have the
      file in hand): a guarded value already there, or strictly inside one that is, does not widen
      what the routine reaches — and narrowing is exactly what that advisory asks for — so it is
      accepted; see `_already_held_or_narrower`. An edge that is MAKING the grant passes no
      `current`, so nothing new is ever grantable.

    The three edges used to hold three subsets of this (the routine PATCH no absolute check,
    the conversation create form its own), and a check that exists on one edge is a check a
    grant can route around.
    """
    vals = values or []
    if not isinstance(vals, list) or any(not isinstance(p, str) or not p.strip() for p in vals):
        raise HTTPException(400, f"{key}: must be a list of non-empty path strings")
    roots: list[str] = []
    for raw in vals:
        root = raw.strip().rstrip("/") or "/"
        try:
            absolute = expand(root).is_absolute()
        except RuntimeError:          # `~name` for an account that does not exist
            absolute = False
        if not absolute:
            raise HTTPException(
                400, f"{key}: {raw!r} is not an absolute path (use /abs/path or ~/path)")
        if root not in roots:
            roots.append(root)
    if guarded := [g for g in entities.guarded_roots(roots) if not _already_held_or_narrower(
            g, current)]:
        raise HTTPException(400, f"{key}: {', '.join(guarded)} {entities.GUARDED_ROOT_REASON}")
    return roots


def _already_held_or_narrower(value: str, current: list | None) -> bool:
    """Is `value` a root the routine ALREADY holds, or one strictly inside it (F582)?

    The guard's own advisory asks an operator to narrow a credential store a routine already
    reads — and this edge refused the narrower path, so the only fix it offered could not be
    applied (D159 sat at its resolution for exactly this reason). A value already granted, or
    under one already granted, does not increase what the run reaches; every other direction is
    unchanged, so this allows nothing a routine does not already have:

    - **equal to a held root** → allowed, because nothing changes. Without this an unrelated edit
      to the same list (adding `~/routines` beside it) is refused for a grant that is already in
      the file — the file the loader itself reports and keeps.
    - **strictly inside a held root** → allowed: this is the NARROWING the advisory asks for.
    - no `current` at all (the conversation CREATE form, the composer, a settings pattern) → every
      guarded value is a NEW grant and stays refused.
    - a value CONTAINING a held root (a widening back to the store, or up to `~/.config`) → the
      held root is not among the value's ancestors and is not equal to it, so it is refused.

    Paths are compared RESOLVED (`~` and `$VARS` expanded), because a file may spell the same
    store either way — the loader's own comparison does the same.
    """
    try:
        target = expand(value)
    except RuntimeError:
        return False
    for raw in current or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            held = expand(raw.strip().rstrip("/") or "/")
        except RuntimeError:
            continue
        if target == held or held in target.parents:
            return True
    return False


def clean_tags(values: list | None) -> list[str]:
    """Freeform filter tags, stripped, blanks dropped."""
    return [t.strip() for t in (values or []) if isinstance(t, str) and t.strip()]
