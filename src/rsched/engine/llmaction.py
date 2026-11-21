"""The `llm` and `list_models` actions — a run reaching for a model directly.

`llm` is the one action whose side effect is another model call — deliberately a single
completion with no tools and no turns, never a nested agent loop. `list_models` lets a per-call
`model` override be chosen against the real catalog.
"""

from __future__ import annotations

import json

from ..config.base import supervisor_role
from ..endpoints.base import EndpointError
from . import outputs, refusal
from .observations import truncate
from .run_context import RunContext


def do_llm(action: dict, ctx: RunContext) -> dict:
    messages = []
    if action.get("system"):
        messages.append({"role": "system", "content": action["system"]})
    messages.append({"role": "user", "content": action["prompt"]})
    schema = action.get("response_schema")
    purpose = ("llm · " + str(action.get("say") or "sub-call"))[:80]
    # The optional `model` field: a ROLE (main/tool_call/uncensored), a CATALOG name, or
    # `<endpoint>/<model id>` (F868) — default tool_call; an unknown value is a teaching error.
    # The ENDPOINT must be one the operator configured (never a bare URL: this runs in the
    # engine, not the util jail); only the model id is free, e.g. for a local model.
    role = str(action.get("model") or "tool_call")
    try:
        if role == "uncensored":
            target = ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts)
            if target is None:
                return {"kind": "llm",
                        "error": "model role 'uncensored' is not configured for this routine "
                                 "— it needs a models.uncensored catalog entry (routine page "
                                 "→ Models). Use the default role, or ask the user to set one."}
            endpoint, ref = target
        elif role in ("main", "tool_call"):
            endpoint, ref = ctx.registry.for_model(role, ctx.routine.models, ctx.routine.efforts)
        elif role in ctx.server.models:
            endpoint, ref = ctx.registry.for_name(role)
        elif "/" in role:
            endpoint, ref = ctx.registry.for_endpoint(role)
        else:
            avail = ", ".join(sorted(ctx.server.models)) or "none configured"
            eps = ", ".join(sorted(ctx.server.endpoints)) or "none configured"
            return {"kind": "llm",
                    "error": f"model {role!r} is neither a role (main/tool_call/uncensored), "
                             f"a catalog model name, nor '<endpoint>/<model id>'. Catalog "
                             f"models: {avail}. Configured endpoints: {eps}. The list_models "
                             "action shows each model's endpoint and attributes."}
        completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                       effort=ref.effort, temperature=ref.temperature,
                                       max_tokens=ref.max_tokens, purpose=purpose,
                                       kind="llm_action")
    except EndpointError as exc:
        return {"kind": "llm", "error": str(exc)}
    ctx.add_usage(completion.usage)

    reply = completion.text
    if completion.parsed is not None:
        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    # An EMPTY completion is not an answer: it reads like "nothing to add" (or a silent
    # refusal the text classifier below cannot see), so it fails LOUDLY instead.
    if not str(reply).strip():
        return {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
                "usage": completion.usage,
                "error": f"{ref.name or ref.model} returned an EMPTY completion (no text, no "
                         "error). That is not an answer: a blank reply and a silent refusal "
                         "look identical, so this is surfaced rather than passed on. Retry "
                         "with the prompt reworded, or with a different `model` — and do not "
                         "read the silence as agreement."}
    full_reply = reply
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    # D246: an oversized reply SPILLS to `.util_outputs/` like a util's output (same pointer),
    # since a model's answer is the least reproducible thing a run buys.
    if truncated and (pointer := outputs.spill(ctx, "llm", full_reply, "",
                                               out_truncated=True, err_truncated=False)):
        out["full_output"] = pointer
    # Refusal takeover (engine/refusal.py): a free-text reply judged a content REFUSAL is
    # re-issued WHOLE on the uncensored model and its reply IS the answer. A schema'd reply and
    # an explicit `model: uncensored` call are never taken over; a sub-call is not a turn.
    if role != "uncensored" and completion.parsed is None \
            and refusal.is_refusal(ctx, completion.text):
        record, takeover_reply = refusal.reissue_on_uncensored(
            ctx, messages=messages, schema=schema, purpose=(purpose + " - re-issued")[:80],
            refusal=completion.text, where="llm", model=ref.name or ref.model)
        out["refusal"] = record
        if takeover_reply is not None:
            r2 = (takeover_reply["text"] if takeover_reply["parsed"] is None
                  else json.dumps(takeover_reply["parsed"], ensure_ascii=False, indent=1))
            reply_text, truncated = truncate(r2)
            out.update({"reply": reply_text, "truncated": truncated,
                        "takeover": True, "endpoint": takeover_reply["endpoint"],
                        "model": takeover_reply["model"],
                        "usage": takeover_reply["usage"]})
    return out

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
    # F868: the configured ENDPOINTS, which `llm`'s `<endpoint>/<model id>` route needs and
    # the catalog does not reveal. Non-secret: name, kind and base_url, never a key.
    endpoints = [{"name": name, "kind": cfg.kind, "base_url": cfg.base_url}
                 for name, cfg in sorted((ctx.server.endpoints or {}).items())]
    out: dict = {"kind": "list_models", "roles": roles, "models": models,
                 "endpoints": endpoints,
                 "note": ("a child/llm action's `model` field takes one of these "
                          "catalog names, or a role (main/tool_call/uncensored/supervisor); "
                          "children default to main, llm to tool_call. An `llm` call may also "
                          "name '<endpoint>/<model id>' from `endpoints` to reach a model the "
                          "catalog does not carry (llm only — a child run's model must be a "
                          "catalog name)")}
    if ctx.server.decision_models:
        # The DECISION catalog is a different contract (endpoints/decisions.py) — listed apart
        # so a name from it is never passed to llm/spawn, nor a chat model's to decide.
        from ..endpoints.decisions import decision_catalog
        out["decision_models"] = decision_catalog(ctx.server, ctx.routine.models)
        out["note"] += ("; a decide action's `model` takes a decision_models name instead "
                        "(default: the one marked default for its payload)")
    return out


def format_models(obs: dict, kind: str) -> str | None:
    """The list_models observation, whole — the generic renderer cuts at 500 characters."""
    if kind != "list_models":
        return None
    body = {k: v for k, v in obs.items() if k != "kind"}
    text, _ = truncate(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    return f"OBSERVATION (list_models):\n{text}"

