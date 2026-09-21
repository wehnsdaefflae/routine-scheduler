"""Making the turn FIT — the context window, compaction, and the media fallback.

Split out of `completion.py` (F393): asking the model for one action and keeping the request
inside the provider's limits are different jobs, and only this one is allowed to rewrite the
message list.

That permission is the reason it is worth isolating. The composed prompt is a CACHING CONTRACT
— appended-to, never mutated — and compaction, the schema-retry cleanup and the media fallback
are the three sanctioned exceptions, each invalidating the provider cache deliberately. Every
other seam in the engine appends.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import archival, mediaops
from .compaction import (
    ANTICIPATE_AT,
    KEEP_HEAD_MSGS,
    KEEP_TAIL_MSGS,
    archival_fits,
    clamp_to_cap,
    estimate_input_tokens,
    input_cap_tokens,
    maybe_compact,
    window_ceiling_tokens,
)

#: How close to the HARD ceiling still leaves a turn of slack. Below this the fraction is
#: binding and a deferred turn is free; above it the ceiling is, and the warning is skipped.
_EVICT_WARN_HEADROOM = 0.9

#: How many times ONE turn may shrink-and-retry an oversize prompt on the same model before
#: the turn dies naming its size. Two: the first shrink uses the provider's stated maximum,
#: the second absorbs a tokenizer that counts heavier than our estimate. A third would be
#: the loop this guard exists to end.
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
                   "prompt is too long", "too many tokens", "request too large")

_OVERFLOW_TOKENS_RE = re.compile(
    r"(?:maximum context length(?: is)?|context (?:window|length) of(?: only)?|"
    r"context_length_exceeded\D{0,40}?)\s*(\d{4,7})\s*tokens", re.IGNORECASE)

#: Anthropic: "prompt is too long: <actual> tokens > <maximum> maximum" — the only shape
#: that states BOTH numbers, which makes the overshoot exactly computable.
_OVERFLOW_PAIR_RE = re.compile(
    r"(\d{4,9})\s*tokens?\s*(?:>|&gt;|\\u003e|exceeds?)\s*(\d{4,9})", re.IGNORECASE)


def parse_overflow_pair(text: str) -> tuple[int | None, int | None]:
    """(actual, maximum) tokens stated by a context-overflow error, either side None when
    the text does not state it. The ACTUAL count is what makes a shrink target honest: a
    provider that reports 1,045,385 against 1,000,000 has told us precisely how much has
    to go, which no fraction of the window can know.
    """
    low = text.lower()
    if not any(h in low for h in _OVERFLOW_HINTS):
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
    low = text.lower()
    if not any(h in low for h in _OVERFLOW_HINTS):
        return None
    pair = _OVERFLOW_PAIR_RE.search(text)
    if pair:
        return int(pair.group(2))
    m = _OVERFLOW_TOKENS_RE.search(text)
    return int(m.group(1)) if m else None

def _override_window(loop, ref):
    """This run's corrected view of a model's window, when the guard has shrunk it: every
    turn re-picks the ref from the registry (which still carries the catalog's figure), so
    the correction is re-applied here rather than by mutating shared registry state.
    """
    overrides = getattr(loop, "_window_overrides", None)
    shrunk = overrides.get((ref.endpoint, ref.model)) if overrides else None
    if shrunk and shrunk < ref.context_tokens:
        return dataclasses.replace(ref, context_tokens=shrunk)
    return ref

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
    target = max(target, _reserved_tokens(loop, ref) + 1_000)
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
    overrides[key] = min(overrides.get(key, target), target)
    new_ref = dataclasses.replace(ref, context_tokens=overrides[key])
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
        "retry_window_tokens": overrides[key], "attempt": attempts[key]}})
    if after >= before:
        raise EndpointError(
            f"prompt is too long and cannot be shrunk further: {before:,} estimated "
            f"tokens, head+tail floor is incompressible"
            + (f", provider maximum {stated:,} tokens" if stated else ""))
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


def _reserved_tokens(loop, ref) -> int:
    """Reserve output plus the separately transmitted action schema."""
    schema = getattr(loop, "action_schema", None)
    schema_cost = (estimate_input_tokens([{"content": json.dumps(schema, ensure_ascii=False)}])
                   if schema and not getattr(loop, "_schema_off", False) else 0)
    return ref.max_tokens + schema_cost


def compact_if_needed(loop, endpoint, ref) -> None:
    """Keep the next prompt inside the model's window. First ARCHIVE the middle if it has grown
    past the compaction gate (`_archive_if_needed`), then ENFORCE the hard window ceiling as a
    last resort (`clamp_to_cap`) — because archiving cannot shrink the incompressible head+tail
    floor and a short conversation has no middle to elide, so a run with a few very large
    observations would otherwise 400 with context_length_exceeded and die (F265, three
    recurrences on c-20260802-110156). The clamp trims oversized bodies in place with a visible
    marker; the full text stays in the transcript.
    """
    _archive_if_needed(loop, endpoint, ref)
    ctx = loop.ctx
    cl = clamp_to_cap(loop.messages, ref.context_tokens, _reserved_tokens(loop, ref))
    if cl:
        ctx.transcript.event("compaction", {"clamp": cl})
        loop._last_compact_after = estimate_input_tokens(loop.messages)

def _warn_before_eviction(loop, size: float, ref) -> bool:
    """Give the run ONE turn to move what matters into a durable store before the middle goes.

    Retention is positional — head 6, tail 24 — so a load-bearing fact in the middle survives
    only in `history/`, reachable if the run remembers to go looking. `note`, `memory_write`
    and the LEDGER already exist to carry a fact out of the conversation; what was missing was
    the moment to use them, which is precisely the one this layer exists to supply. Returns
    True when the archive should wait a turn.

    The decision uses estimated input occupancy, with headroom for the next turn. The gate is
    `min(fraction × window, ceiling)`, and when the FRACTION binds there is 20–40% of the
    window between here and the hard ceiling — a whole turn of slack. When the CEILING binds
    there is none, so the warning is skipped and the archive happens now: a warning that
    overflowed the window would cost the run the very turns it was trying to protect. Either
    way `clamp_to_cap` still runs afterwards. Provider tokenization can differ from the estimate.

    Once per run. A second warning would be the layer talking about itself.
    """
    if loop._evict_warned:
        return False
    ceiling = window_ceiling_tokens(ref.context_tokens, _reserved_tokens(loop, ref))
    if size > ceiling * _EVICT_WARN_HEADROOM:
        return False            # no slack: the ceiling is binding, archive now
    loop._evict_warned = True
    loop.ctx.transcript.event("user_injection", {
        "text": "[engine] compaction imminent — one turn to externalize", "source": "engine"})
    loop.messages.append({"role": "user", "content":
        "ENGINE NOTE: the middle of this conversation is about to be ARCHIVED — the first "
        f"{KEEP_HEAD_MSGS} and last {KEEP_TAIL_MSGS} messages stay, everything between them "
        "moves to on-disk history you would have to go looking for. Retention is positional, "
        "not semantic: it does not know what mattered.\n"
        "You have this turn. Anything in the middle worth keeping — a finding, a value you "
        "will need again, a dead end worth not repeating, a decision and why — put it in a "
        "durable store NOW: a `note` (free, rides any action), a memory_write, or a LEDGER "
        "entry. Then carry on; the archive happens on your next turn either way."})
    return True


def _archive_if_needed(loop, endpoint, ref) -> None:
    """When the prompt exceeds the compaction gate, elide the middle with the deterministic
    one-line digest (`maybe_compact`) and hand the same middle to `archival.start`, which
    builds the navigable on-disk history OFF the hot path and announces it when it lands. The
    digest is what the run carries meanwhile, and what it keeps if the archival degrades — so a
    run never stalls on compaction. Does NOT guarantee the result clears the window — that is
    the caller's `clamp_to_cap` step (the head+tail floor is incompressible).
    """
    ctx = loop.ctx
    size = estimate_input_tokens(loop.messages)
    # Observed cache hits flip the economics: re-reading carried context costs ~0.1x,
    # while compacting rewrites the prefix and invalidates the whole cache — so compact
    # later (0.8) once the provider demonstrably serves from cache, earlier (0.6) when
    # every turn re-reads at full price. The cap also reserves room for the model's
    # OUTPUT (ref.max_tokens): the provider counts prompt + requested output against ONE
    # window, so a small-window model must compact before input + max_tokens overflows it
    # (F265). Both use the MODEL's window, not the endpoint default.
    context_cap = input_cap_tokens(ref.context_tokens, _reserved_tokens(loop, ref),
                                  cached=bool(ctx.usage.get("cached_in")))
    # Long prompts also burn the token BUDGET — every turn re-sends everything, so a
    # bloated prompt taxes each remaining turn. Once the prompt would eat >10% of the
    # remaining token budget per turn, archive it: the one compaction call costs what
    # the bloat would keep costing every single turn. Floored so a small prompt near
    # budget exhaustion doesn't thrash (compaction itself spends tokens).
    remaining = ctx.tokens_remaining()   # None = unlimited → only the context cap applies
    budget_cap = (float("inf") if remaining is None
                  else max(10_000.0, 0.10 * remaining))
    cap = min(context_cap, budget_cap)
    # A BOUNDARY the engine already detects: this turn begins a new stage module, so the run is
    # between steps rather than mid-edit. Compact now if the prompt is merely APPROACHING the gate
    # — a pass taken here is cheaper and less disruptive than the same pass forced three actions
    # into the next step. The anti-thrash guards below are untouched: this moves WHEN a compaction
    # happens, never whether an extra one does.
    at_boundary = bool(ctx.phase) and ctx.phase != getattr(loop, "_last_seen_phase", None)
    if at_boundary:
        loop._last_seen_phase = ctx.phase
        cap *= ANTICIPATE_AT
    if (size <= cap or len(loop.messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS):
        return
    # Anti-thrash: head + tail are an incompressible floor (large observations in the last
    # 24 messages stay verbatim), so once the middle is a handful of messages — or the size
    # hasn't grown meaningfully since the last archive — another pass can't win. Each
    # attempt costs a full-prompt LLM call; wait until there is enough new middle to pay
    # for one. (Seen live: 4 compactions in one run, the last archiving 3 messages for a
    # 5k-char gain.)
    middle_n = len(loop.messages) - KEEP_HEAD_MSGS - KEEP_TAIL_MSGS
    if middle_n < 8 or size < loop._last_compact_after + 5_000:
        return
    if _warn_before_eviction(loop, size, ref):
        return          # one turn to externalize what matters; the archive happens next turn
    # Archival is machine work — route it to the (usually cheaper) tool-call model
    # whenever its window can hold the middle being archived; the main model is the
    # fallback, never the default.
    c_endpoint, c_ref = endpoint, ref
    try:
        t_endpoint, t_ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        middle_size = estimate_input_tokens(
            loop.messages[KEEP_HEAD_MSGS:len(loop.messages) - KEEP_TAIL_MSGS])
        if t_ref.context_tokens * 0.7 >= middle_size:
            c_endpoint, c_ref = t_endpoint, t_ref
    except Exception:
        pass
    # The INSTANT tier takes the pass and the run carries straight on; the navigable
    # archive is built off the hot path and announced when it lands (engine/archival.py).
    # The archival call is the slow one — 180-600s of a run's time, spent mid-work — and
    # nothing about it needs the run to wait: it reads a middle that has already been
    # decided and writes files. Losslessness is untouched. The digest is a PLACEHOLDER in
    # the prompt for the minute the archive takes, never a summary standing in for it —
    # which is the whole difference from the mainstream summarize-and-replace this
    # deliberately does not adopt.
    middle = loop.messages[KEEP_HEAD_MSGS:len(loop.messages) - KEEP_TAIL_MSGS]
    turn = max((r["turn"] for r in loop.turn_records), default=0)
    loop.messages, cinfo = maybe_compact(loop.messages, loop.turn_records,
                                        ref.context_tokens)
    if cinfo is not None:
        dedicated = ctx.server.compaction_model
        if dedicated:
            try:
                d_endpoint, d_ref = ctx.registry.for_name(dedicated)
                if archival_fits(middle, d_ref):
                    c_endpoint, c_ref = d_endpoint, d_ref
                    if d_ref.name != dedicated:
                        cinfo["archival_selection_fallback"] = (
                            f"{dedicated}: unavailable; catalog fallback {d_ref.name}")
                else:
                    cinfo["archival_selection_fallback"] = (
                        f"{dedicated}: archival request exceeds safe context budget; "
                        "using Automatic")
            except Exception as exc:
                cinfo["archival_selection_fallback"] = (
                    f"{dedicated}: unavailable ({exc}); using Automatic")
        if dedicated:
            cinfo["archival_model"] = c_ref.name or c_ref.model
        archival.start(loop, middle, c_endpoint, c_ref, turn)
        cinfo["archival"] = "background"
    if cinfo:
        # the archival call's spend is booked by archival.collect, on the turn the
        # archive lands — this pass is the deterministic digest and calls no model
        loop._last_compact_after = estimate_input_tokens(loop.messages)
        # `anticipated` says this pass was taken EARLY, at a stage boundary, rather than because
        # the prompt had actually crossed the gate — without it the two are indistinguishable in
        # the transcript and the feature could not be evaluated after the fact.
        ctx.transcript.event("compaction",
                             {**cinfo, **({"anticipated": ctx.phase} if at_boundary else {})})


def apply_media_fallback(loop, exc: EndpointError) -> bool:
    """The main endpoint failed on a turn whose tail user message carries image `media`
    (for example, it rejected the file). Convert that
    media to vision-util text IN PLACE and drop it, so the retried completion is text-only
    and the model still gets the content. False when the tail has no media — then the
    failure is a genuine endpoint error that must propagate.
    """
    if not loop.messages:
        return False
    last = loop.messages[-1]
    media = last.get("media")
    if not media:
        return False
    notes = []
    for item in media:
        desc = mediaops.vision_describe(loop.ctx, item["path"], "")
        notes.append(f"[{Path(item['path']).name}: this run's model could not display it — "
                     f"description from the vision util]\n{desc}")
    last.pop("media", None)
    last["content"] = last["content"] + "\n\n" + "\n\n".join(notes)
    loop.ctx.transcript.event("error", {"where": "media",
        "message": f"main endpoint could not show {len(media)} file(s) "
                   f"({str(exc)[:120]}); fell back to the vision util"})
    return True
