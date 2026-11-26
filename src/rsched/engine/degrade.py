"""When a turn comes back WRONG — transport failure, model refusal, or an empty reply.

The happy path is one call and one parsed action; these
are the three ways it does not arrive, and each degrades rather than killing the run: a
transport error walks the failover chain, a refusal is classified and referred (endpoints are
model TRANSPORTS, so a refusal is a transport fact, not the run's verdict), and an empty reply
is retried before it can read as a silent finish.
"""

from __future__ import annotations

import json

from ..endpoints import errorsignal, failover
from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import refusal, substitution, turndebris
from .compaction import estimate_input_tokens
from .mediafallback import apply_media_fallback
from .overflow import _recover_oversize_prompt, _shrink_window_to_provider


def _failover_reason(exc: EndpointError) -> str:
    """Classify a hard failure for the health stream: rate_limit · auth · refusal · empty ·
    server · other — the CLASS a `model_failover` event carries, not the provider prose.

    The basis is what the failure DECLARED (R3108): its status and error.code / error.type
    (`endpoints/errorsignal.py`), or for the engine's own findings the `engine=` set at the
    raise — never phrases matched in the body. `other` stays an honest bucket.
    """
    return errorsignal.failover_class(errorsignal.of(exc))


def _turn_needs(loop) -> dict:
    """What THIS turn's request needs of any model that takes it over: whether it carries
    images, and how big the prompt is. Both travel with the request, so a chain member
    that cannot hold them is the worse choice (endpoints/failover.fits).
    """
    return {"has_media": any(m.get("media") for m in loop.messages),
            "prompt_tokens": estimate_input_tokens(loop.messages)}


def _recover_transport(loop, chain, endpoint, ref, exc: EndpointError) -> tuple:
    """The completion call failed with a hard EndpointError — the runtime nets, in order.

    Ordered by one question: would another model, given this exact request, succeed? A fault
    of the ENDPOINT (5xx, timeout, 429, credentials, refusal, repeated empties) is the chain's
    to absorb; a fault of the REQUEST travels with it to the next model, so it is repaired here.

    Net 0: an overflow whose stated maximum is below the configured window is a lying catalog
    entry — shrink the run-local window and retry the SAME model once
    (_shrink_window_to_provider). Net 0b: an overflow that CONFIRMS the window re-runs the
    shrink path under the provider's numbers and retries the same model, never marking it
    failed (_recover_oversize_prompt). Net 1: an image the endpoint couldn't show becomes
    vision-util text and the SAME model retries, its cooldown lifted. Net 2: a genuine hard
    failure advances down the fallback chain; chain exhausted → re-raise. Returns the
    (endpoint, ref) to retry on.
    """
    shrunk = _shrink_window_to_provider(loop, endpoint, ref, exc)
    if shrunk is not None:
        failover.clear(ref.endpoint, ref.model)   # the CONFIG, not the provider, was at fault
        return shrunk
    oversize = _recover_oversize_prompt(loop, endpoint, ref, exc)
    if oversize is not None:
        failover.clear(ref.endpoint, ref.model)   # the REQUEST, not the provider, was at fault
        return oversize
    if apply_media_fallback(loop, exc):
        failover.clear(ref.endpoint, ref.model)
        return endpoint, ref
    switched = _switch_to_fallback(loop, chain, ref, exc)
    if switched is None:
        raise exc
    return switched

def _handle_refusal(loop, completion, chain, ref, attempt: int,
                    refstate: dict) -> tuple[tuple | None, bool]:
    """A classifier refusal (stop_reason in REFUSAL_STOPS — an HTTP 200, content usually
    empty, stop_details naming the category). Never blind-retried on the same model.

    A refusal-marked transcript error, then ONE takeover engage (engine/refusal.py): the
    refused TURN is re-driven on the routine's uncensored model and its output IS this turn's
    action. Only when no takeover can serve does the fallback chain advance, cooling the
    refused model for this run only (the failover registry is process-local). Chain
    exhausted: raises with the category named. Returns the pair to serve the turn on.
    """
    details = completion.stop_details or {}
    category = str(details.get("category") or "") or "unreported"
    explanation = str(details.get("explanation") or "")
    what = (f"{ref.name or ref.model} refused the turn "
            f"(stop_reason={completion.stop_reason}, category={category}"
            + (f": {explanation[:200]}" if explanation else "") + ")")
    loop.ctx.transcript.event("error", {
        "where": "endpoint", "attempt": attempt, "message": what,
        "refusal": {"category": category, "model": ref.name or ref.model,
                    **({"explanation": explanation[:500]} if explanation else {})}})
    if not refstate["takeover_tried"]:
        refstate["takeover_tried"] = True
        _, target = refusal.engage_takeover(
            loop.ctx, refusal=explanation or what, where="loop",
            model=ref.name or ref.model,
            refusing=(ref.endpoint, ref.model))
        if target is not None:
            # NOT cooling the refused model: the back-off's hand-back must return to the
            # REGULAR model when the window closes, not skip on to its fallbacks.
            return target, True
    # The refusal class is DECLARED, never matched back out of `what`.
    switched = _switch_to_fallback(
        loop, chain, ref,
        EndpointError(what, signal=errorsignal.ErrorSignal(engine="refusal")))
    if switched is not None:
        return switched, False
    raise EndpointError(
        f"{what}; no usable fallback model — configure `fallbacks:` on the catalog "
        f"model to survive classifier refusals")

def _intercept_refusal_finish(loop, candidate, ref, refstate: dict) -> tuple[bool, tuple | None]:
    """A finish whose summary reads as a CONTENT REFUSAL is not accepted
    as the turn's action: flag + arm the back-off episode; the refused turn is re-driven
    whole on the uncensored model when one can serve it (the returned target pair), else
    the turn is re-driven on the regular chain with the refusal named (True = the caller
    `continue`s). Anything else (an honest failure report, a non-finish action) returns
    (False, None) and is accepted normally. Latched via refstate so a model that keeps
    refusing eventually lands its finish honestly.

    `status` decides how hard we LOOK, never whether: a refusal also arrives as
    finish(status=ok), so the summary is always judged — but only a declared `failed` earns a
    classification subcall; `ok`/`partial` use the marker fast-path alone.
    """
    ctx = loop.ctx
    if not (isinstance(candidate, dict) and candidate.get("kind") == "finish"):
        return False, None
    summary = str(candidate.get("summary") or "")
    refused = (refusal.is_refusal(ctx, summary) if candidate.get("status") == "failed"
               else refusal.looks_like_refusal(summary))
    if not refused:
        return False, None
    refstate["takeover_tried"] = True
    _, target = refusal.engage_takeover(
        ctx, refusal=summary, where="loop",
        model=ref.name or ref.model,
        refusing=(ref.endpoint, ref.model))
    if target is not None:
        return True, target
    turndebris.append(loop, {"role": "assistant",
                             "content": json.dumps(candidate, ensure_ascii=False)},
                      {"role": "user", "content":
                       "That finish was a content refusal and was NOT accepted as this "
                       "turn's action. The refused request is being handled by another "
                       "model - do NOT finish-fail on its account; proceed with the "
                       "REMAINDER of the task without it."})
    return True, None

def _handle_empty(loop, completion, chain, ref,
                  attempt: int, refstate: dict) -> tuple | None:
    """One empty completion (no content, no parsed object) that is NOT a refusal — those
    divert to _handle_refusal before this. A provider hiccup gets a clean same-model retry
    (no poisoned context), but a hard-broken model keeps failing the same way, so the
    SECOND empty in one turn engages the failover chain exactly like a hard EndpointError
    (same-model blind retries can never fix it). Returns the switched chain entry, or
    None = plain same-model retry.
    """
    stop = completion.stop_reason
    # stop_details rides along VERBATIM so the transcript shows WHY the reply was empty.
    details = completion.stop_details or {}
    loop.ctx.transcript.event("error", {
        "where": "endpoint", "attempt": attempt,
        "message": "empty completion (no content/reasoning; "
                   f"stop_reason={stop or 'unreported'}"
                   + (f", stop_details={details}" if details else "") + ")"})
    refstate["empty"] += 1
    if refstate["empty"] >= 2:
        failure = EndpointError(
            f"empty completion x{refstate['empty']} (stop_reason={stop or 'unreported'})",
            signal=errorsignal.ErrorSignal(engine="empty"))   # declared, not re-parsed
        switched = _switch_to_fallback(loop, chain, ref, failure)
        if switched is not None:
            refstate["empty"] = 0
            return switched
        # chain exhausted (or none configured): the caller keeps the pre-failover
        # behavior — remaining attempts run schema-free
    return None

def _log_chain_exhausted(loop, chain, failed_ref, exc: EndpointError) -> None:
    """A role's WHOLE chain is gone — every member failed hard or is cooling.

    The run records its own failure; this files the FLEET fact to the health stream, which
    nothing else shows. Keyed by the chain HEAD (what a sweep groups by), with the models as
    structured fields, not prose in `detail`. Best-effort: health logging never blocks a run.
    """
    head = chain[0][1] if chain else failed_ref
    ctx = loop.ctx
    log_health_event(
        ctx.server.routines_home, "model_chain_exhausted",
        routine=getattr(ctx.routine, "slug", "") or "",
        run_id=getattr(ctx, "run_id", "") or "",
        detail=(f"every model in {head.name or head.model}'s fallback chain failed or is "
                f"cooling; last was {failed_ref.name or failed_ref.model}: {str(exc)[:200]}"),
        model=head.name or head.model,
        last_model=failed_ref.name or failed_ref.model,
        cooldown_s=failover.COOLDOWN_S)


def _switch_to_fallback(loop, chain, failed_ref, exc: EndpointError):
    """The picked model failed hard mid-turn (its adapter's transport retries are already
    exhausted, or it kept returning empty completions). Advance to the next chain member
    not in cooldown and log the switch VISIBLY: a transcript `error` event whose
    `failover` payload names both models — so the run records which model serves from here
    (status.json follows via ctx.main_model). None = chain exhausted. What the SUBSTITUTED
    model is told about its situation is `engine/substitution.py`, hence `chain` travels there.

    The abandoned model is marked cooling HERE: InstrumentedEndpoint only cools
    retryable-class transport failures, and a deterministic error or empty-reply pattern is
    visible only at this seam.
    """
    failover.mark_failed(failed_ref.endpoint, failed_ref.model,
                         cooldown_s=failover.cooldown_for(exc))
    # The FIRST failure, kept once and never overwritten: if the whole chain dies, the last
    # rung's error is the least actionable one.
    if not loop.ctx.first_failover_cause:
        loop.ctx.first_failover_cause = (
            f"{failed_ref.endpoint}/{failed_ref.model}: {str(exc)[:200]}")
    nxt = failover.next_after(chain, failed_ref, **_turn_needs(loop))
    if nxt is None:
        _log_chain_exhausted(loop, chain, failed_ref, exc)
        return None
    _, n_ref = nxt
    loop.ctx.failover_rungs += 1
    loop.ctx.transcript.event("error", {
        "where": "endpoint",
        "message": (f"{failed_ref.name or failed_ref.model} failed hard: {str(exc)[:300]} "
                    f"— failing over to {n_ref.name or n_ref.model}"),
        "failover": {"from": failed_ref.name, "to": n_ref.name,
                     "cooldown_s": failover.COOLDOWN_S}})
    _log_failover(loop, chain, failed_ref, n_ref, exc)
    substitution.announce(loop, chain, failed_ref, n_ref, exc)
    return nxt


def _log_failover(loop, chain, failed_ref, new_ref, exc: EndpointError) -> None:
    """A switch reached the FLEET stream, not only this run's transcript.

    A run that fails over finishes `ok`, so nothing downstream would say the primary stopped
    serving; `model_chain_exhausted` fires only when the last rung dies too. Keyed by the
    chain HEAD; `from_model`/`to_model`/`reason` are structured fields, not prose in `detail`.
    Best-effort: health logging never blocks a run.
    """
    head = chain[0][1] if chain else failed_ref
    ctx = loop.ctx
    log_health_event(
        ctx.server.routines_home, "model_failover",
        routine=getattr(ctx.routine, "slug", "") or "",
        run_id=getattr(ctx, "run_id", "") or "",
        detail=(f"{failed_ref.name or failed_ref.model} failed hard, serving from "
                f"{new_ref.name or new_ref.model} instead: {str(exc)[:200]}"),
        model=head.name or head.model,
        from_model=failed_ref.name or failed_ref.model,
        to_model=new_ref.name or new_ref.model,
        reason=_failover_reason(exc))
