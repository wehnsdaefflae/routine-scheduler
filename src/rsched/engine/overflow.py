"""When the PROVIDER says the prompt did not fit — reading the 400, and recovering from it.

Split out of `window.py` (F393): keeping a prompt inside a window we believe in is one job,
and deciding what to believe when the provider rejects it is another. This one owns the
vendor vocabulary, the two shapes a stated figure arrives in, and the two nets that repair a
REQUEST fault in place — `window.py` never parses an error, and this file never decides when
to compact.

Both nets retry the SAME model on purpose. A prompt over the wall travels with the request:
the next chain member receives it unchanged and fails identically, or answers from a silently
truncated context. Only the ENDPOINT's faults belong to the failover chain (engine/degrade).
"""

from __future__ import annotations

import dataclasses

from ..endpoints import errorsignal
from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import errorclass
from .compaction import clamp_to_cap, estimate_input_tokens
from .window import _reserved_tokens, compact_if_needed

#: How many times one turn may shrink-and-retry an oversize prompt ON ONE MODEL before the
#: turn dies naming its size. Two: the first shrink uses the provider's stated maximum, the
#: second absorbs a tokenizer that counts heavier than our estimate. A third would be the
#: loop this guard exists to end. The counter is keyed (endpoint, model), so a chain of K
#: models permits K × 2 recoveries in one turn; the whole-turn ceiling on completion calls
#: is K × (MAX_SCHEMA_ATTEMPTS + _MAX_OVERSIZE_RETRIES) + 1 for the media fallback.
_MAX_OVERSIZE_RETRIES = 2

# F278: the window guard. The clamp (`clamp_to_cap`) sizes everything from the CATALOG's
# context figure — when that figure claims a larger window than the provider actually
# enforces, no compaction gate ever fires and the completion 400s with
# context_length_exceeded (2026-08-05: a gemma entry raised to 250k tokens against the
# provider's real 65,536 disarmed the whole net and killed two live conversations). The
# guard closes the loop at the error itself: parse the provider's STATED maximum from the
# overflow text, shrink this RUN's view of that model's window to it, re-clamp the prompt,
# and retry the same model once. Config stays authoritative for sizing DOWN (a smaller
# configured window is a deliberate budget); the provider is authoritative for sizing UP —
# a stated max at or above the configured window means config wasn't the problem, so the
# guard declines and the ordinary transport nets take over.
# R3108: THE VENDOR VOCABULARY IS GONE. This guard used to turn on four hand-kept phrases
# ("context_length_exceeded", "maximum context length", "context window", "prompt is too long")
# minus nine excluded status words — and a hint list that misses a vendor silently DISARMS the
# whole guard for it. On 2026-09-21 the Anthropic shape matched none of the three
# openai-flavoured hints then present, so self-audit:20260921-000321 400'd four times with a
# prompt that grew each time (1,017,305 → 1,045,385) while the engine read every rejection as a
# broken provider and failed over. The exclusion list was the same bet in reverse: a billing
# error whose message happened to say "maximum context length" pinned the run's window to a
# figure that was never a context size.
#
# What decides now is what the failure DECLARED — its status and its own error.code / error.type,
# decoded from the whole body at the raise (`endpoints/errorsignal.py`) — with a structured
# 400/413 that STATES a token figure covering the vendor nobody has met yet. Nothing in here
# needs a vendor's words added to it, which is the property the phrase list never had.


def _is_overflow_text(error) -> bool:
    """Whether this failure says the INPUT DID NOT FIT, read from what it declared. Takes the
    raised error (whose signal was read from the WHOLE body) or, for a caller that only has the
    rendered text, that text — `errorsignal.of` accepts either.
    """
    return errorsignal.is_overflow(errorsignal.of(error))


def parse_overflow_pair(error) -> tuple[int | None, int | None]:
    """(actual, maximum) tokens stated by a context-overflow error, either side None when it
    states neither. The ACTUAL count is what makes a shrink target honest: a provider that
    reports 1,045,385 against 1,000,000 has told us precisely how much has to go, which no
    fraction of the window can know. Pass the EXCEPTION where you have it: its signal was read
    from the whole body, while its text is a 300-character excerpt of it.
    """
    signal = errorsignal.of(error)
    return signal.tokens if errorsignal.is_overflow(signal) else (None, None)


def parse_overflow_limit(error) -> int | None:
    """The provider-stated maximum context TOKENS of a context-overflow failure, or None when
    it is not one (or states no usable figure).
    """
    return parse_overflow_pair(error)[1]


def new_turn(loop) -> None:
    """Re-arm the shrink allowance for the turn about to be asked for.

    `_MAX_OVERSIZE_RETRIES` bounds ONE turn's shrink-and-retry loop — the guard against a
    prompt that keeps growing between attempts. Kept for the whole run, it also ended any
    long run on its third oversize 400, however cleanly the first two had been recovered
    turns apart, before trying a shrink that would have worked.
    """
    loop._oversize_attempts = {}


def _shrink_window_to_provider(loop, endpoint, ref, exc: EndpointError) -> tuple | None:
    """Net 0 of _recover_transport: a context-overflow failure whose stated maximum is
    SMALLER than the configured window means the catalog entry lies — shrink the run-local
    window to the provider's figure, re-clamp the prompt under it, emit the audit trail
    (transcript event + `model_window_corrected` health event naming the bad entry), and
    hand back the same endpoint with the corrected ref for one clean retry. Returns None
    when the error is not an overflow, states no figure, config was not the problem, or
    this model was already corrected once this run (never loops).
    """
    stated = parse_overflow_limit(exc)
    if stated is None:
        return None
    key = (ref.endpoint, ref.model)
    overrides = getattr(loop, "_window_overrides", None)
    if overrides is None:
        overrides = loop._window_overrides = {}
    if overrides.get(key, float("inf")) <= stated or stated >= ref.context_tokens:
        return None
    overrides[key] = stated
    new_ref = dataclasses.replace(ref, context_tokens=stated)
    cl = clamp_to_cap(loop.messages, new_ref.context_tokens, _reserved_tokens(loop, new_ref))
    ctx = loop.ctx
    ctx.transcript.event("compaction", {"window_guard": {
        "model": ref.name or ref.model, "configured_tokens": ref.context_tokens,
        "provider_max_tokens": stated, "corrected_tokens": stated,
        **({"clamp": cl} if cl else {})}})
    log_health_event(ctx.server.routines_home, "model_window_corrected",
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=(f"{ref.name or ref.model}: catalog claims "
                             f"{ref.context_tokens:,} context tokens but the provider "
                             f"enforces {stated:,} tokens — run continues on "
                             f"{stated:,} tokens; correct the catalog entry"))
    return endpoint, new_ref

def _recover_oversize_prompt(loop, endpoint, ref, exc: EndpointError) -> tuple | None:
    """Net 0b: the prompt itself is too big for a window the catalog states CORRECTLY.

    The F278 guard above answers "is the catalog lying?" and declines when the provider's
    stated maximum is at or above the configured window. That is the right answer to that
    question and the wrong place to stop: the request is still over the wall. On 2026-09-21
    the decline handed a 400 to `_switch_to_fallback`, which read "the model failed" — so a
    healthy model was cooled for 300 s and the identical oversize prompt was posted to the
    next model in the chain, four times, growing each time, until the run died.

    A too-long prompt is a fault of the REQUEST, and every model in the chain receives the
    same request. So recovery is local: shrink here, retry the SAME model, and never touch
    the failover registry. Returns (endpoint, ref) for one retry, or None when the error is
    not an oversize fault at all (the ordinary nets take over) — while an oversize prompt
    that CANNOT be shrunk raises, because the honest outcome is a turn that dies naming its
    size, not a silent degrade onto a model that will answer from a truncated context.
    """
    actual, stated = parse_overflow_pair(exc)
    if stated is None and actual is None:
        # R3108: both nets have declined. When the body is a 400 that QUOTES a token count, the
        # decline is the one worth counting — a vendor shape no hint covers (2026-09-21) or a
        # correctly-excluded rate limit, and the row says which. Measurement only: the decline
        # itself, and every window decision, are exactly as they were.
        errorclass.note_overflow_declined(loop, exc)
        return None                       # not an overflow error — not ours
    ctx = loop.ctx
    before = estimate_input_tokens(loop.messages)
    # The provider's own numbers are authoritative over any estimate we could make. Scale
    # the run-local window by the measured ratio of estimate to truth when both are known:
    # the tokenizer counted `actual` where we estimated `before`, so our figures run light
    # by exactly that factor and the target has to absorb it.
    target = stated if stated is not None else int((actual or before) * 0.9)
    if actual and before and actual > 0:
        target = min(target, int(target * before / actual))
    # A floor, so a pathological ratio cannot shrink the window to nothing — but never
    # ABOVE what the provider said it accepts, or the retry is posted over the wall again
    # and burns both attempts proving it (reviewer's worked case: a large `max_tokens` plus
    # a big action schema floors at 1,101,000 against a stated 1,000,000).
    target = max(target, _reserved_tokens(loop, ref) + 1_000)
    if stated is not None:
        target = min(target, stated)
    attempts = getattr(loop, "_oversize_attempts", None)
    if attempts is None:
        attempts = loop._oversize_attempts = {}
    key = (ref.endpoint, ref.model)
    if attempts.get(key, 0) >= _MAX_OVERSIZE_RETRIES:
        raise EndpointError(
            f"prompt still too long after {_MAX_OVERSIZE_RETRIES} shrink attempts "
            f"({before:,} estimated tokens against a stated maximum of "
            f"{stated:,} tokens)" if stated else
            f"prompt still too long after {_MAX_OVERSIZE_RETRIES} shrink attempts "
            f"({before:,} estimated tokens)")
    attempts[key] = attempts.get(key, 0) + 1
    overrides = getattr(loop, "_window_overrides", None)
    if overrides is None:
        overrides = loop._window_overrides = {}
    # NOT committed to `overrides` yet. `_override_window` re-applies whatever is stored
    # here on every later pick, so an override written on a path that then RAISES would
    # clamp every remaining turn of the run to a window derived from a failure (measured in
    # review: an 18-token prompt that drew a spurious oversize 400 left a 21,106-token
    # override behind). It is recorded only once the shrink is proven to have helped.
    corrected = min(overrides.get(key, target), target)
    new_ref = dataclasses.replace(ref, context_tokens=corrected)
    # Re-run the FULL shrink path — archive the middle, then clamp bodies — under the
    # corrected window. The live defect was that nothing re-ran it inside the retry loop,
    # so every failed attempt only appended and the prompt grew monotonically.
    compact_if_needed(loop, endpoint, new_ref)
    after = estimate_input_tokens(loop.messages)
    ctx.transcript.event("compaction", {"oversize_prompt": {
        "model": ref.name or ref.model,
        **({"provider_counted_tokens": actual} if actual else {}),
        **({"provider_max_tokens": stated} if stated else {}),
        "estimated_before": before, "estimated_after": after,
        "retry_window_tokens": corrected, "attempt": attempts[key]}})
    if after >= before:
        raise EndpointError(
            f"prompt is too long and cannot be shrunk further: {before:,} estimated "
            f"tokens, head+tail floor is incompressible"
            + (f", provider maximum {stated:,} tokens" if stated else ""))
    overrides[key] = corrected      # the shrink helped — now it is worth carrying forward
    log_health_event(ctx.server.routines_home, "prompt_oversize_shrunk",
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=(f"{ref.name or ref.model} rejected a prompt of "
                             f"{actual or before:,} tokens"
                             + (f" against its {stated:,} maximum" if stated else "")
                             + f" — shrunk to ~{after:,} estimated tokens and retried on "
                             f"the same model (no failover: another model would receive "
                             f"the same prompt)"),
                     model=ref.name or ref.model)
    return endpoint, new_ref
