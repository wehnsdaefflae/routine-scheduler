"""When the PROVIDER says the prompt did not fit — reading the 400, and recovering from it.

`window.py` keeps a prompt inside a window we believe in and never parses an error; this file
reads the provider's rejection and owns the two nets that repair a REQUEST fault in place,
and never decides when to compact.

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
#: second absorbs a tokenizer that counts heavier than our estimate. Keyed (endpoint, model),
#: so a chain of K models permits K × 2 recoveries in one turn.
_MAX_OVERSIZE_RETRIES = 2

# The window guard (F278): when the CATALOG claims a larger window than the provider enforces,
# no compaction gate fires and the completion 400s. The guard parses the provider's STATED
# maximum, shrinks this RUN's view of the window to it, re-clamps and retries the same model.
# Config is authoritative for sizing DOWN (a deliberate budget); the provider for sizing UP — a
# stated max at or above the configured window means config wasn't the problem, so it declines.
#
# Overflow is recognised from what the failure DECLARED — its status and error.code/error.type,
# decoded from the whole body (`endpoints/errorsignal.py`) — plus a structured 400/413 stating a
# token figure, never from a hand-kept list of vendor phrases (R3108): a phrase list that misses
# a vendor silently disarms the guard for it.


def _is_overflow_text(error) -> bool:
    """Whether this failure says the INPUT DID NOT FIT, read from what it declared. Takes the
    raised error (whose signal was read from the WHOLE body) or, for a caller that only has the
    rendered text, that text — `errorsignal.of` accepts either.
    """
    return errorsignal.is_overflow(errorsignal.of(error))


def parse_overflow_pair(error) -> tuple[int | None, int | None]:
    """(actual, maximum) tokens stated by a context-overflow error, either side None when it
    states neither. The ACTUAL count tells precisely how much has to go. Pass the EXCEPTION
    where you have it: its signal was read from the whole body, its text is an excerpt.
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

    `_MAX_OVERSIZE_RETRIES` bounds ONE turn's shrink-and-retry loop, not the run's: a run-wide
    count would end a long run on its third cleanly-recovered oversize 400.
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

    Net 0 declines when the catalog is right, but the request is still over the wall. A
    too-long prompt is a fault of the REQUEST, which every chain member would receive, so
    recovery is local: shrink, retry the SAME model, never touch the failover registry.
    Returns (endpoint, ref) for one retry, or None when the error is not an oversize fault
    (the ordinary nets take over). An oversize prompt that CANNOT be shrunk raises: the turn
    dies naming its size rather than degrading onto a truncated context.
    """
    actual, stated = parse_overflow_pair(exc)
    if stated is None and actual is None:
        # both nets declined; a 400 quoting a token count is recorded (measurement only, R3108)
        errorclass.note_overflow_declined(loop, exc)
        return None                       # not an overflow error — not ours
    ctx = loop.ctx
    before = estimate_input_tokens(loop.messages)
    # The provider's numbers beat our estimate: scale the target by estimate/actual when both
    # are known, since our figures run light by exactly that factor.
    target = stated if stated is not None else int((actual or before) * 0.9)
    if actual and before and actual > 0:
        target = min(target, int(target * before / actual))
    # A floor, so a pathological ratio cannot shrink the window to nothing — but never ABOVE
    # what the provider said it accepts, or the retry goes over the wall again.
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
    # NOT committed to `overrides` yet: `_override_window` re-applies it on every later pick,
    # so it is recorded only once the shrink is proven to have helped.
    corrected = min(overrides.get(key, target), target)
    new_ref = dataclasses.replace(ref, context_tokens=corrected)
    # Re-run the FULL shrink path (archive the middle, then clamp bodies) under the corrected
    # window, or each retry only appends and the prompt grows.
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
