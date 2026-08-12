"""POST /api/llm — the PROCEDURE-side model call (operator symmetry rule 2026-08-12).

A procedure is the recipe's co-equal, so it may drop into model judgment exactly the way
the recipe drops into code: the `llm` global util POSTs here (routine token) and the
daemon resolves THE CALLING ROUTINE'S OWN configured model — by default its `main` role,
the very model the recipe runs on — through the one EndpointRegistry chokepoint. Utils
never hold provider keys (STRIP_VARS), so this endpoint is the only path from a
procedure to a model: transport stays server-side, task telemetry stays centralized, and
the spend lands in the durable usage stream under the calling routine
(`workflow: "(procedure-llm)"` rows — monthly spend aggregates them like any run's).

The routine token may POST here (app.ROUTINE_TOKEN_MUTATIONS carries the pair): a
completion mutates no config — it spends model budget, which the stream records visibly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..config import MODEL_KINDS
from ..endpoints import EndpointRegistry
from ..endpoints.base import EndpointError
from ..health_events import log_workflow_usage
from .routines_common import _info, _state

router = APIRouter(tags=["llm"])


class LlmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine: str = Field(min_length=1)   # the calling routine's slug (the util reads its cwd)
    prompt: str = Field(min_length=1)
    system: str = ""
    # "main" = the recipe's own default model — the symmetric choice; any configured
    # role may be named, mirroring the recipe's llm/subtask `model` field (D81)
    role: str = "main"
    response_schema: dict | None = None


@router.post("/llm")
def complete(request: Request, body: LlmBody) -> dict:
    info = _info(request, body.routine)          # 404 on an unknown slug
    if body.role not in MODEL_KINDS:
        raise HTTPException(400, f"role must be one of {list(MODEL_KINDS)}")
    reg = EndpointRegistry(_state(request).server)
    messages = ([{"role": "system", "content": body.system}] if body.system else []) \
        + [{"role": "user", "content": body.prompt}]
    try:
        if body.role == "uncensored":
            target = reg.for_uncensored(info.cfg.models)
            if target is None:
                raise HTTPException(400, "model role 'uncensored' is not configured for "
                                         f"routine {body.routine!r}")
            endpoint, ref = target
        else:
            endpoint, ref = reg.for_model(body.role, info.cfg.models)
        completion = endpoint.complete(
            messages, model=ref.model, schema=body.response_schema, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens,
            purpose=f"procedure-llm · {body.routine}"[:80], kind="procedure_llm")
    except (EndpointError, LookupError, IndexError) as exc:
        # resolution failures (no endpoints configured / unknown catalog name) and
        # transport failures alike: the caller gets a 502 with the reason, never a 500
        raise HTTPException(502, str(exc)) from exc
    usage = completion.usage or {}
    log_workflow_usage(
        _state(request).server.routines_home, routine=body.routine,
        run_id=f"{body.routine}:procedure", workflow="(procedure-llm)", depth=0,
        status="ok", turns=0,
        tokens=int(usage.get("in") or 0) + int(usage.get("out") or 0),
        cost=float(usage.get("cost") or 0.0))
    return {"reply": completion.text, "parsed": completion.parsed,
            "endpoint": ref.endpoint, "model": ref.model, "usage": usage}
