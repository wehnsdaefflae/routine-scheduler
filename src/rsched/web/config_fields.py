"""The config fields a ROUTINE and a CONVERSATION both carry, validated once.

A conversation is routine-shaped — same `routine.yaml`, same model roles, same connection
and machine bindings, same budgets and folder grants — so its PATCH and the routine PATCH
were two copies of the same five checks, and copies drift. They already had: the
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

from .. import rootgrants
from ..config import (
    DECISION_ROLES,
    DEFAULT_BUDGETS,
    EFFORT_LEVELS,
    MODEL_KINDS,
    ROUTINE_MODEL_ROLES,
)
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


def validate_efforts(mapping: dict | None) -> dict:
    """Role → reasoning effort: a CHAT role and one of EFFORT_LEVELS. REPLACE wholesale, so a
    role left out (or blanked) runs at the provider's default. Effort is its own setting beside
    `models` — it needs no catalog entry per level.
    """
    out: dict = {}
    for role, level in (mapping or {}).items():
        if role not in MODEL_KINDS:
            raise HTTPException(400, f"efforts.{role}: unknown role (expected one of "
                                     f"{', '.join(MODEL_KINDS)})")
        if level in ("", None):
            continue
        if level not in EFFORT_LEVELS:
            raise HTTPException(400, f"efforts.{role}: {level!r} is not one of "
                                     f"{', '.join(EFFORT_LEVELS)}")
        out[role] = level
    return out


def effort_picker(server, cfg) -> dict:
    """What the effort pickers need: each chat role's level (null = the provider's default —
    effort is a setting of its own beside the model, never a catalog row per level), the levels
    on offer, and the instance defaults a role that names no model of its own resolves to.

    The TOOL_CALL role has its own instance default (D230, `config.base.role_default_model`), so
    the page carries it beside `system_effort`: the blank option of a picker states what the
    ENGINE resolves that role to, and a label naming the system model while the engine ran a
    different one is the defect `tests/ui/test_role_blank_defaults.py` exists to pin.
    """
    return {"efforts": {role: cfg.efforts.get(role) or None for role in MODEL_KINDS},
            "effort_levels": list(EFFORT_LEVELS),
            "system_effort": server.system_effort or None,
            "system_tool_call_model": server.system_tool_call_model or None,
            "system_tool_call_effort": server.system_tool_call_effort or None}


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
    `workdir`), REPLACED wholesale — the ONE enforcer every edge where a grant is MADE calls:
    the routine PATCH, the conversation PATCH and the conversation create form.

    The judgement is `rootgrants.normalize_roots` — absolute after `~`/`$VARS` expand, never a
    credential store unless already held (`current`) — shared with the proposal judge
    (`patterns/validate.problem`), so a pending change is refused at its writer by the very rule
    its accept would apply. This edge only turns that refusal into the 400 the page shows.
    """
    try:
        return rootgrants.normalize_roots(key, values, current=current)
    except rootgrants.RootGrantError as exc:
        raise HTTPException(400, str(exc)) from exc


def clean_tags(values: list | None) -> list[str]:
    """Freeform filter tags, stripped, blanks dropped."""
    return [t.strip() for t in (values or []) if isinstance(t, str) and t.strip()]
