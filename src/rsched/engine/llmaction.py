"""The `llm` and `list_models` actions — a run reaching for a model directly.

Split out of `executor.py` (F393). `llm` is the one action whose SIDE EFFECT is another model
call, which makes it the place a second agent loop would creep in — it is deliberately a single
completion with no tools and no turns, never a nested agent. `list_models` exists so a per-call
`model` override can be chosen against the real catalog instead of from memory.
"""

from __future__ import annotations

import json

from ..config.base import supervisor_role
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
    # An EMPTY completion is not an answer, and it must never reach the caller as one. A
    # zero-length reply is indistinguishable from a model that considered the question and
    # had nothing to add — and the refusal classifier below cannot help, because it reads
    # the reply TEXT, which an empty string does not carry. R1611: three consecutive empty
    # replies across two models, on a prompt quoting a security-disclosure draft, after a
    # near-identical call had answered fully minutes earlier. The run nearly read the silence
    # as "the reviewer found no problems with the revised draft" — a false verification of a
    # message going to a stranger under the user's name. So it fails LOUDLY instead.
    if not str(reply).strip():
        return {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
                "usage": completion.usage,
                "error": f"{ref.name or ref.model} returned an EMPTY completion (no text, no "
                         "error). That is not an answer: a blank reply and a silent refusal "
                         "look identical, so this is surfaced rather than passed on. Retry "
                         "with the prompt reworded, or with a different `model` — and do not "
                         "read the silence as agreement."}
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": ref.endpoint, "model": ref.model,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    # Refusal takeover (engine/refusal.py, operator 2026-10-16): a free-text reply the
    # classification subcall judges a content REFUSAL is re-issued WHOLE on the routine's
    # uncensored model - same messages, same schema - and its reply IS the answer (the
    # honeypot era's essence-excision, which discarded the uncensored reply and
    # re-asked the refusing model with the trigger blanked out, is retired with its
    # premise). A schema'd reply is an answer by construction, and an explicit
    # `model: uncensored` call is the caller's own probe - neither is taken over.
    # The turn-level back-off episode is untouched here: a sub-call is not a turn.
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
    """The model DISCOVERY surface (paired with the per-call `model` override,
    2026-08-22): what this run's role bindings resolve to right now, plus every catalog
    model a `model` field may name. Read-only — config stays the user's. A role or catalog
    row that fails to resolve surfaces as its own error line instead of vanishing
    (failure-visibility) — the uncensored role too, which resolved unguarded, so a role naming
    a model no longer in the catalog raised out of the action and failed the run.
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
            models.append({"name": name, "endpoint": ref.endpoint, "model": ref.model,
                           "multimodal": ref.multimodal, "context_tokens": ref.context_tokens,
                           "fallbacks": list(ctx.server.models[name].fallbacks)})
        except EndpointError as exc:
            models.append({"name": name, "error": str(exc)})
    out: dict = {"kind": "list_models", "roles": roles, "models": models,
                 "note": ("a spawn/subtask/llm action's `model` field takes one of these "
                          "catalog names, or a role (main/tool_call/uncensored/supervisor); "
                          "children default to main, llm to tool_call")}
    if ctx.server.decision_models:
        # The DECISION catalog is a different contract (endpoints/decisions.py) — listed apart
        # so a name from it is never passed to llm/spawn, nor a chat model's to decide.
        from ..endpoints.decisions import decision_catalog
        out["decision_models"] = decision_catalog(ctx.server, ctx.routine.models)
        out["note"] += ("; a decide action's `model` takes a decision_models name instead "
                        "(default: the one marked default for its payload)")
    return out


def format_models(obs: dict, kind: str) -> str | None:
    """The list_models observation, whole. It used to fall through to the generic renderer,
    which cuts every observation it does not know at 500 characters — on a 24-model catalog
    that is the roles and the first two models, and none of the decision catalog.
    """
    if kind != "list_models":
        return None
    body = {k: v for k, v in obs.items() if k != "kind"}
    text, _ = truncate(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    return f"OBSERVATION (list_models):\n{text}"

