"""The `llm` and `list_models` actions — a run reaching for a model directly.

Split out of `executor.py` (F393). `llm` is the one action whose SIDE EFFECT is another model
call, which makes it the place a second agent loop would creep in — it is deliberately a single
completion with no tools and no turns, never a nested agent. `list_models` exists so a per-call
`model` override can be chosen against the real catalog instead of from memory.
"""

from __future__ import annotations

import json

from ..endpoints.base import EndpointError
from . import refusal
from .observations import truncate
from .run_context import RunContext


def do_llm(action: dict, ctx: RunContext) -> dict:
    messages = []
    if action.get("system"):
        messages.append({"role": "system", "content": action["system"]})
    messages.append({"role": "user", "content": action["prompt"]})
    schema = action.get("response_schema")
    purpose = ("llm · " + str(action.get("say") or "sub-call"))[:80]
    # The optional `model` field: a ROLE (main/tool_call; uncensored → the routine's
    # uncensored model) or a CATALOG model NAME (`list_models` shows them) — default
    # tool_call. An unknown value is a teaching error naming the catalog (D81 extended,
    # 2026-08-22).
    role = str(action.get("model") or "tool_call")
    try:
        if role == "uncensored":
            target = ctx.registry.for_uncensored(ctx.routine.models)
            if target is None:
                return {"kind": "llm",
                        "error": "model role 'uncensored' is not configured for this routine "
                                 "— it needs a models.uncensored catalog entry (routine page "
                                 "→ Models). Use the default role, or ask the user to set one."}
            endpoint, ref = target
        elif role in ("main", "tool_call"):
            endpoint, ref = ctx.registry.for_model(role, ctx.routine.models)
        elif role in ctx.server.models:
            endpoint, ref = ctx.registry.for_name(role)
        else:
            avail = ", ".join(sorted(ctx.server.models)) or "none configured"
            return {"kind": "llm",
                    "error": f"model {role!r} is neither a role (main/tool_call/uncensored) "
                             f"nor a catalog model name. Catalog models: {avail}. The "
                             "list_models action shows each one's endpoint and attributes."}
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
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    # Refusal clarification (engine/refusal.py): a free-text reply that a classification
    # subcall (markers only fast-path CONFIRM) judges a content refusal is FLAGGED, its
    # trigger isolated, and ONLY the isolated essence delivered to the uncensored model
    # as a normal call (operator, 2026-08-22: authentic environment, dummy responses
    # managed in the background). Everything ELSE goes back to the PRIMARY model with
    # the essence factored out — "without danger of refusal" — and that answer serves
    # the observation; the refusal record rides beside it. A schema'd reply is an answer
    # by construction, and an explicit `model: uncensored` call is the caller's own
    # probe — neither is clarified.
    if role != "uncensored" and completion.parsed is None \
            and refusal.is_refusal(ctx, completion.text):
        record = refusal.clarify_refusal(
            ctx, task=str(action.get("prompt") or ""), refusal=completion.text,
            where="llm", model=ref.name or ref.model)
        out["refusal"] = record
        essence = record.get("isolated")
        if essence and essence in str(action.get("prompt") or ""):
            sanitized = str(action.get("prompt") or "").replace(
                essence, "[this part is handled separately]")
            try:
                remainder = endpoint.complete(
                    [*messages[:-1], {"role": "user", "content": sanitized}],
                    model=ref.model, schema=schema, effort=ref.effort,
                    temperature=ref.temperature, max_tokens=ref.max_tokens,
                    purpose=(purpose + " · remainder")[:80], kind="llm_action")
            except EndpointError:
                remainder = None
            if remainder is not None and (remainder.text or remainder.parsed is not None):
                ctx.add_usage(remainder.usage)
                r2 = (remainder.text if remainder.parsed is None
                      else json.dumps(remainder.parsed, ensure_ascii=False, indent=1))
                out["reply"], out["truncated"] = truncate(r2)
                out["remainder_processed"] = True
    return out

def do_list_models(ctx: RunContext) -> dict:
    """The model DISCOVERY surface (paired with the per-call `model` override,
    2026-08-22): what this run's role bindings resolve to right now, plus every catalog
    model a `model` field may name. Read-only — config stays the user's. A catalog row
    that fails to resolve surfaces as its own error line instead of vanishing
    (failure-visibility).
    """
    roles: dict = {}
    for role in ("main", "tool_call"):
        try:
            _, ref = ctx.registry.for_model(role, ctx.routine.models)
            roles[role] = {"catalog": ref.name, "endpoint": ref.endpoint, "model": ref.model}
        except EndpointError as exc:
            roles[role] = {"error": str(exc)}
    unc = ctx.registry.for_uncensored(ctx.routine.models)
    roles["uncensored"] = ({"catalog": unc[1].name, "endpoint": unc[1].endpoint,
                            "model": unc[1].model} if unc else None)
    models = []
    for name in sorted(ctx.server.models):
        try:
            _, ref = ctx.registry.resolve(name)
            models.append({"name": name, "endpoint": ref.endpoint, "model": ref.model,
                           "multimodal": ref.multimodal, "context_chars": ref.context_chars,
                           "effort": ref.effort,
                           "fallbacks": list(ctx.server.models[name].fallbacks)})
        except EndpointError as exc:
            models.append({"name": name, "error": str(exc)})
    return {"kind": "list_models", "roles": roles, "models": models,
            "note": ("a spawn/subtask/llm action's `model` field takes one of these "
                     "catalog names, or a role (main/tool_call/uncensored); children "
                     "default to main, llm to tool_call")}
