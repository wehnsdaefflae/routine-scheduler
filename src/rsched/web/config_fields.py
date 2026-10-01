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

from .. import entities
from ..config import DEFAULT_BUDGETS, MODEL_KINDS
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
    instance `system_model`.
    """
    for kind, name in (mapping or {}).items():
        if kind not in MODEL_KINDS:
            raise HTTPException(400,
                                f"unknown model kind {kind!r} (expected one of {MODEL_KINDS})")
        if not isinstance(name, str) or name not in server.models:
            raise HTTPException(400, f"models.{kind}: must be a catalog model name")
        if problem := model_window_problem(server, name):
            # R112/R128: the model's own output maximum fills its context window, so the
            # first completion would die on `context_length_exceeded`. Refuse at the click.
            raise HTTPException(400, problem)
    return dict(mapping or {})


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


def validate_roots(key: str, values: list | None) -> list[str]:
    """One folder-grant list (`fs_read_roots` / `fs_write_roots`, or a conversation's
    `workdir` — write root #1), REPLACED wholesale — the ONE enforcer every edge where a grant
    is MADE calls: the routine PATCH, the conversation PATCH and the conversation create form.

    Each entry is stripped of whitespace and a trailing `/`, deduplicated, and returned as the
    raw string the file holds (`~` kept, never expanded here). Refused with a 400 naming it:

    - a path that is not ABSOLUTE once `~`/`$VARS` expand — a relative root would resolve
      against the daemon's working directory, which no operator means;
    - a credential store (SEC-1, `entities.guarded_roots`): the config dir, `~/.credentials`
      and `~/.ssh` are never grantable, and the never-grantable promise lived only on the
      runtime ask path until a typed root mounted the instance's credential dir on a routine.
      A store ALREADY in a file is the loader's to report, never dropped (config/routine.py).

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
    if guarded := entities.guarded_roots(roots):
        raise HTTPException(400, f"{key}: {', '.join(guarded)} {entities.GUARDED_ROOT_REASON}")
    return roots


def clean_tags(values: list | None) -> list[str]:
    """Freeform filter tags, stripped, blanks dropped."""
    return [t.strip() for t in (values or []) if isinstance(t, str) and t.strip()]
