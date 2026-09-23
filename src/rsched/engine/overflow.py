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
import re

from ..endpoints.base import EndpointError
from ..health_events import log_health_event
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
# Provider vocabulary. Each vendor words the same fault differently, and a hint list that
# misses a vendor silently DISARMS this whole guard for it — on 2026-09-21 the Anthropic
# shape ("prompt is too long: 1045385 tokens > 1000000 maximum") matched none of the three
# openai-flavoured hints, so self-audit:20260921-000321 400'd four times with a prompt that
# grew each time (1,017,305 → 1,045,385) while the engine read every rejection as a broken
# provider and failed over. Add the vendor's words here when a new one appears.
_OVERFLOW_HINTS = ("context_length_exceeded", "maximum context length", "context window",
                   "prompt is too long")

# …and a fault that merely QUOTES two token counts is not an overflow. Rate limits ("Request
# too large: 32000 tokens > 30000 maximum tokens per minute"), billing errors and an auth
# failure echoing the request body all carry that shape, and reading one as an overflow is
# worse than missing an overflow: it pins the run's window to a number that was never a
# context size — a per-minute budget, a credit balance — for every remaining turn, while
# suppressing the failover the real fault needed. These statuses are therefore never an
# overflow, whatever their prose says.
_NEVER_OVERFLOW_STATUS = ("http 429", "http 402", "http 401", "http 403",
                          "rate_limit", "rate limit", "insufficient_quota",
                          "per minute", "per day", "tokens per")

_OVERFLOW_TOKENS_RE = re.compile(
    r"(?:maximum context length(?: is)?|context (?:window|length) of(?: only)?|"
    r"context_length_exceeded\D{0,40}?)\s*(\d{4,7})\s*tokens", re.IGNORECASE)

#: Anthropic: "prompt is too long: <actual> tokens > <maximum> maximum" — the only shape
#: that states BOTH numbers, which makes the overshoot exactly computable.
_OVERFLOW_PAIR_RE = re.compile(
    r"(\d{4,9})\s*tokens?\s*(?:>|&gt;|\\u003e|exceeds?)\s*(\d{4,9})", re.IGNORECASE)


def _is_overflow_text(text: str) -> bool:
    """Whether this error is a CONTEXT-SIZE fault at all. Prose alone is not enough: the
    vendor vocabulary has to be there AND the fault must not be one of the statuses that
    quote token counts for a different reason entirely (a rate limit, a quota, a balance).
    """
    low = text.lower()
    if any(bad in low for bad in _NEVER_OVERFLOW_STATUS):
        return False
    return any(h in low for h in _OVERFLOW_HINTS)


def parse_overflow_pair(text: str) -> tuple[int | None, int | None]:
    """(actual, maximum) tokens stated by a context-overflow error, either side None when
    the text does not state it. The ACTUAL count is what makes a shrink target honest: a
    provider that reports 1,045,385 against 1,000,000 has told us precisely how much has
    to go, which no fraction of the window can know.
    """
    if not _is_overflow_text(text):
        return None, None
    pair = _OVERFLOW_PAIR_RE.search(text)
    if pair:
        return int(pair.group(1)), int(pair.group(2))
    m = _OVERFLOW_TOKENS_RE.search(text)
    return None, (int(m.group(1)) if m else None)


def parse_overflow_limit(text: str) -> int | None:
    """The provider-stated maximum context TOKENS from a context-overflow error message,
    or None when the text is not an overflow error (or states no usable figure).
    """
    if not _is_overflow_text(text):
        return None
    pair = _OVERFLOW_PAIR_RE.search(text)
    if pair:
        return int(pair.group(2))
    m = _OVERFLOW_TOKENS_RE.search(text)
    return int(m.group(1)) if m else None

def _shrink_window_to_provider(loop, endpoint, ref, exc: EndpointError) -> tuple | None:
    """Net 0 of _recover_transport: a context-overflow failure whose stated maximum is
    SMALLER than the configured window means the catalog entry lies — shrink the run-local
    window to the provider's figure, re-clamp the prompt under it, emit the audit trail
    (transcript event + `model_window_corrected` health event naming the bad entry), and
    hand back the same endpoint with the corrected ref for one clean retry. Returns None
    when the error is not an overflow, states no figure, config was not the problem, or
    this model was already corrected once this run (never loops).
    """
    stated = parse_overflow_limit(str(exc))
    if stated is None:
        return None
    corrected = stated
    key = (ref.endpoint, ref.model)
    overrides = getattr(loop, "_window_overrides", None)
    if overrides is None:
        overrides = loop._window_overrides = {}
    if overrides.get(key, float("inf")) <= corrected or corrected >= ref.context_tokens:
        return None
    overrides[key] = corrected
    new_ref = dataclasses.replace(ref, context_tokens=corrected)
    cl = clamp_to_cap(loop.messages, new_ref.context_tokens, _reserved_tokens(loop, new_ref))
    ctx = loop.ctx
    ctx.transcript.event("compaction", {"window_guard": {
        "model": ref.name or ref.model, "configured_tokens": ref.context_tokens,
        "provider_max_tokens": stated, "corrected_tokens": corrected,
        **({"clamp": cl} if cl else {})}})
    log_health_event(ctx.server.routines_home, "model_window_corrected",
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=(f"{ref.name or ref.model}: catalog claims "
                             f"{ref.context_tokens:,} context tokens but the provider "
                             f"enforces {stated:,} tokens — run continues on "
                             f"{corrected:,} tokens; correct the catalog entry"))
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
    actual, stated = parse_overflow_pair(str(exc))
    if stated is None and actual is None:
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
