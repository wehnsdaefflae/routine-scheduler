"""When a turn comes back WRONG — transport failure, model refusal, or an empty reply.

Split out of `completion.py` (F393). The happy path is one call and one parsed action; these
are the three ways it does not arrive, and each degrades rather than killing the run: a
transport error walks the failover chain, a refusal is classified and referred (endpoints are
model TRANSPORTS, so a refusal is a transport fact, not the run's verdict), and an empty reply
is retried before it can read as a silent finish.
"""

from __future__ import annotations

import json

from ..endpoints import failover
from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import refusal
from .window import (
    _recover_oversize_prompt,
    _shrink_window_to_provider,
    apply_media_fallback,
)


def _recover_transport(loop, chain, endpoint, ref, exc: EndpointError) -> tuple:
    """The completion call failed with a hard EndpointError — the runtime nets, in order.

    They are ordered by ONE question: *would another model, given this exact request,
    succeed?* Only when the answer is yes does failing over help. A fault of the ENDPOINT
    (5xx, timeout, 429, exhausted credentials, a classifier refusal, repeated empties) is a
    property of that provider and the chain is the right answer. A fault of the REQUEST (a
    prompt over the window, a body the API rejects) travels with the request: the next model
    receives it unchanged and fails identically — or, with a smaller window, silently answers
    from a truncated context. Nets 0 and 0b exist so a request fault is repaired HERE.

    Net 0: a context-overflow whose stated maximum is smaller than the configured
    window is a lying catalog entry, not a provider failure — shrink the run-local window,
    re-clamp, and retry the SAME model (once; see _shrink_window_to_provider, F278).
    Net 0b: an overflow whose stated maximum CONFIRMS the configured window — the catalog is
    honest and the prompt is simply too big — re-runs the whole shrink path under the
    provider's own numbers and retries the same model, never marking it failed
    (_recover_oversize_prompt; self-audit:20260921-000321 died in the loop this closes).
    Net 1: a turn whose tail carries an image the endpoint couldn't show is
    converted to vision-util text (media fallback) and retried text-only on the SAME
    model — the cooldown the instrumentation just started is lifted, since the image, not
    the provider, was the problem. Net 2: a genuine hard provider failure advances down
    the role's fallback chain (the failed model is already cooling); chain exhausted →
    re-raise, failing the run exactly as before fallbacks existed. Returns the
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

def _handle_refusal(loop, completion, chain, ref, attempt: int, refstate: dict) -> tuple:
    """A classifier refusal (stop_reason in REFUSAL_STOPS — an HTTP 200, so error-rate
    monitoring never sees it; content usually empty, stop_details naming the category).
    Never blind-retried against the same model: re-sending a refused prompt usually earns
    another refusal, so the pre-R5 path — 3 same-model retries, then a "failed to produce
    a valid action" death — burned the run while hiding the cause. Instead: a distinct
    refusal-marked transcript error first, then ONE clarification pass (engine/refusal.py:
    flag → isolate the trigger → deliver only its essence to the uncensored
    HARNESS, whose output is evidence and never this turn's action), then the fallback
    chain advances — cooling the refused model like a hard failure, which is RUN-scoped
    (the failover registry is process-local), so the rest of this run stops re-asking
    while other runs, with other prompts, still probe it fresh. Chain exhausted (or no
    fallbacks): raises, failing the run HONESTLY with the category named — never "empty
    completion". Returns the switched chain entry.
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
    if not refstate["referral_tried"]:
        refstate["referral_tried"] = True
        refusal.clarify_refusal(loop.ctx, task=_turn_task_text(loop),
                                refusal=explanation or what, where="loop",
                                model=ref.name or ref.model)
    switched = _switch_to_fallback(loop, chain, ref, EndpointError(what))
    if switched is not None:
        return switched
    raise EndpointError(
        f"{what}; no usable fallback model — configure `fallbacks:` on the catalog "
        f"model to survive classifier refusals")

def _intercept_refusal_finish(loop, candidate, ref, refstate: dict) -> bool:
    """A finish(status=failed) whose summary reads as a CONTENT REFUSAL is not accepted
    as the turn's action: flag + isolate the essence + deliver it to the honeypot, then
    tell the main model the essence is handled separately and re-drive the turn (True =
    the caller `continue`s). Anything else (an honest failure report, a non-finish
    action) returns False and is accepted normally. Latched via refstate so a model that
    keeps refusing eventually lands its finish honestly.
    """
    ctx = loop.ctx
    if not (isinstance(candidate, dict) and candidate.get("kind") == "finish"
            and refusal.is_refusal(ctx, str(candidate.get("summary") or ""))):
        return False
    refstate["referral_tried"] = True
    rec = refusal.clarify_refusal(ctx, task=_turn_task_text(loop),
                                  refusal=str(candidate.get("summary") or ""),
                                  where="loop", model=ref.name or ref.model)
    if rec.get("isolated"):
        note = (f"the fragment «{rec['isolated']}» is being handled separately by another "
                "model — do NOT finish-fail on its account; proceed with the REMAINDER of "
                "the task without it.")
    else:
        note = ("the refusal-triggering part is being handled separately — do NOT "
                "finish-fail on its account; proceed with the REMAINDER of the task.")
    loop.messages.append({"role": "assistant",
                          "content": json.dumps(candidate, ensure_ascii=False)})
    loop.messages.append({"role": "user", "content":
                          "That finish was a content refusal and was NOT accepted as this "
                          "turn's action. " + note})
    return True

def _turn_task_text(loop) -> str:
    """The refused turn's TASK text for the isolation subcall: the newest USER-role
    message (the observation/instruction the model was answering), falling back to the
    run instruction. Length-capped inside engine/refusal.py.
    """
    for m in reversed(loop.messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return str(loop.instruction or "")

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
    # stop_details rides along VERBATIM so the transcript shows WHY the reply was
    # empty (F164) — e.g. a reasoning model that spent its whole budget thinking.
    details = completion.stop_details or {}
    loop.ctx.transcript.event("error", {
        "where": "endpoint", "attempt": attempt,
        "message": "empty completion (no content/reasoning; "
                   f"stop_reason={stop or 'unreported'}"
                   + (f", stop_details={details}" if details else "") + ")"})
    refstate["empty"] += 1
    if refstate["empty"] >= 2:
        failure = EndpointError(
            f"empty completion x{refstate['empty']} (stop_reason={stop or 'unreported'})")
        switched = _switch_to_fallback(loop, chain, ref, failure)
        if switched is not None:
            refstate["empty"] = 0
            return switched
        # chain exhausted (or none configured): the caller keeps the pre-failover
        # behavior — remaining attempts run schema-free
    return None

def _log_chain_exhausted(loop, chain, failed_ref, exc: EndpointError) -> None:
    """A role's WHOLE chain is gone — every member failed hard or is cooling (F491).

    The run itself already records this: the turn raises and the finish names the endpoint
    failure. What nothing records is the FLEET fact. A cooling primary is survivable per
    run — the chain absorbs it and the run finishes `ok` — so the tax is paid in wall-clock
    and tokens and shows up in no health signal at all. On 2026-09-16, 11 of 13 fleet runs
    opened with "All credentials for model gpt-6-astra are cooling down"; every one finished
    `ok` and the health stream was empty. That is the same reason `cache_read_degraded` and
    `model_window_corrected` exist: an engine-side fact emitted precisely because nothing
    else shows it.

    The chain HEAD is the interesting key, not the member that happened to fail last: it is
    what a sweep groups by to answer "which model is burning the fleet's time, and since
    when". Both ride as structured fields rather than prose, per the F422 lesson — a
    question that has to be answerable by a filter must not live in `detail`.

    Best-effort by construction: health logging never blocks a run, and this seam is already
    handling a failure.
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
    (status.json follows via ctx.main_model). None = chain exhausted.

    The abandoned model is marked cooling HERE — the engine's own judgment that it failed
    a real turn. (InstrumentedEndpoint only cools retryable-class transport failures; a
    deterministic error or an empty-reply pattern is visible only at this seam.)
    """
    failover.mark_failed(failed_ref.endpoint, failed_ref.model)
    nxt = failover.next_after(chain, failed_ref)
    if nxt is None:
        _log_chain_exhausted(loop, chain, failed_ref, exc)
        return None
    _, n_ref = nxt
    loop.ctx.transcript.event("error", {
        "where": "endpoint",
        "message": (f"{failed_ref.name or failed_ref.model} failed hard: {str(exc)[:300]} "
                    f"— failing over to {n_ref.name or n_ref.model}"),
        "failover": {"from": failed_ref.name, "to": n_ref.name,
                     "cooldown_s": failover.COOLDOWN_S}})
    return nxt
