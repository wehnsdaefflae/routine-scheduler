"""The `list_models` action — what models this run can name, and where each one lives.

Its companion `llm` left with D288 (2026-11-25): reaching a model is a util capability, so no
action buys inference any more. This surface stayed, because choosing a model still has to be
possible against the REAL catalog — a child's `model`, or a role and model named in a
model-call capability's own arguments — rather than against a remembered name.
"""

from __future__ import annotations

import json

from ..config.base import supervisor_role
from ..endpoints.base import EndpointError
from .observations import truncate
from .run_context import RunContext


def do_list_models(ctx: RunContext) -> dict:
    """The model DISCOVERY surface: what this run's role bindings resolve to now, plus every
    catalog model a `model` field may name. Read-only. A role or row that fails to resolve
    surfaces as its own error line instead of vanishing or raising.
    """
    roles: dict = {}
    for role in ("main", "tool_call"):
        try:
            _, ref = ctx.registry.for_model(role, ctx.routine.models, ctx.routine.efforts)
            roles[role] = {"catalog": ref.name, "endpoint": ref.endpoint, "model": ref.model}
        except EndpointError as exc:
            roles[role] = {"error": str(exc)}
    try:
        unc = ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts)
        roles["uncensored"] = ({"catalog": unc[1].name, "endpoint": unc[1].endpoint,
                                "model": unc[1].model} if unc else None)
    except EndpointError as exc:
        roles["uncensored"] = {"error": str(exc)}
    try:    # D164: the ladder's overseer role, which resolves through MAIN while it is unset
        role = supervisor_role(ctx.routine.models)
        _, sup = ctx.registry.for_model(role, ctx.routine.models, ctx.routine.efforts)
        roles["supervisor"] = {"catalog": sup.name, "endpoint": sup.endpoint,
                               "model": sup.model, "configured": role == "supervisor"}
    except EndpointError as exc:
        roles["supervisor"] = {"error": str(exc)}
    models = []
    for name in sorted(ctx.server.models):
        try:
            _, ref = ctx.registry.resolve(name)
            row = {"name": name, "endpoint": ref.endpoint, "model": ref.model,
                   "multimodal": ref.multimodal, "context_tokens": ref.context_tokens,
                   "fallbacks": list(ctx.server.models[name].fallbacks)}
            # D279(a): the operator's per-MODEL "when to use" line, only when set (distinct
            # from the payload's top-level `note`, which is the action's usage guidance).
            if ctx.server.models[name].note:
                row["when_to_use"] = ctx.server.models[name].note
            models.append(row)
        except EndpointError as exc:
            models.append({"name": name, "error": str(exc)})
    # F868: the configured ENDPOINTS, which an off-catalog `<endpoint>/<model id>` target needs
    # and the catalog does not reveal. Non-secret: name, kind and base_url, never a key.
    endpoints = [{"name": name, "kind": cfg.kind, "base_url": cfg.base_url}
                 for name, cfg in sorted((ctx.server.endpoints or {}).items())]
    out: dict = {"kind": "list_models", "roles": roles, "models": models,
                 "endpoints": endpoints,
                 "note": ("a child's `model` field takes one of these catalog names, or a role "
                          "(main/tool_call/uncensored/supervisor), and defaults to main. The "
                          "model-call capability takes the same names and roles, plus an "
                          "'<endpoint>/<model id>' from `endpoints` to reach a model the "
                          "catalog does not carry — which a child run's model may not be")}
    if ctx.server.decision_models:
        # The DECISION catalog is a different contract (endpoints/decisions.py) — listed apart
        # so a name from one is never passed where the other is meant.
        from ..endpoints.decisions import decision_catalog
        out["decision_models"] = decision_catalog(ctx.server, ctx.routine.models)
        out["note"] += ("; a TYPED question takes a decision_models name instead (default: the "
                        "one marked default for its payload)")
    return out


def format_models(obs: dict, kind: str) -> str | None:
    """The list_models observation, whole — the generic renderer cuts at 500 characters."""
    if kind != "list_models":
        return None
    body = {k: v for k, v in obs.items() if k != "kind"}
    text, _ = truncate(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    return f"OBSERVATION (list_models):\n{text}"

