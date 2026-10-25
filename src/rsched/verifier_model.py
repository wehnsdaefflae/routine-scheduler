"""Does a routine's claim verifier judge with the same model that made the claims?

The verifier (engine/verifier.py) checks each `met` a finish asserts, on the routine's
`tool_call` role — long documented as "a SECOND model, never the main one". A role the routine
leaves unset runs the server's `system_model` (`EndpointRegistry.for_model_chain`), so when both
roles are unset, or both name the same catalog model, the second opinion is the first one asked
again. Harness² (arXiv 2610.14224) measured what that costs: a checker written by the same
editor reported PASS three times while 4 of 11 tests failed — "a checker written by the editor
cannot catch the editor's assumption" — and its authors recommend a verifier that is a different
model from the one being judged. On 2026-10-25 every one of this instance's 32 routines resolved
both roles to one model.

Checked offline over names, where `rsched validate` already reads the live config, and REPORTED
as a NOTE like the failover notes beside it: which model may judge which is a judgement about
this instance's providers and budget, so it is the operator's to make, and nothing here counts
toward the exit code. Two roles are the same model when they resolve to the same PROVIDER model
id — a catalog name per effort level is still one model with one set of biases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ServerConfig


def _resolved(server: ServerConfig, models: dict[str, str] | None, role: str) -> str:
    """The catalog name a role runs on: its own, else the server's system model."""
    return (models or {}).get(role) or getattr(server, "system_model", "") or ""


def shared_verifier_problem(server: ServerConfig, models: dict[str, str] | None) -> str | None:
    """One line when the `tool_call` role (the verifier's) resolves to the `main` model's
    provider model, else None — and None when either name is not in the catalog (that is
    `trials.roles_problem`'s line, not this one).
    """
    main, judge = _resolved(server, models, "main"), _resolved(server, models, "tool_call")
    catalog = getattr(server, "models", None) or {}
    if main not in catalog or judge not in catalog:
        return None
    if catalog[main].model != catalog[judge].model:
        return None
    how = "both unset, so both run the system model" if not (models or {}).get("tool_call") \
        and not (models or {}).get("main") else f"main {main!r}, tool_call {judge!r}"
    return (f"the claim verifier judges with the model that made the claims "
            f"({catalog[main].model}; {how}) — a second opinion from the same model shares its "
            "blind spots; set models.tool_call to a different model to make it independent")
