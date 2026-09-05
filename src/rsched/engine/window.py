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
import re
from pathlib import Path

from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import mediaops
from .compaction import (
    ANTICIPATE_AT,
    CHARS_PER_TOKEN,
    KEEP_HEAD_MSGS,
    KEEP_TAIL_MSGS,
    clamp_to_cap,
    compact_to_history,
    input_cap_chars,
    maybe_compact,
    messages_size,
    window_ceiling_chars,
)

#: How close to the HARD ceiling still leaves a turn of slack. Below this the fraction is
#: binding and a deferred turn is free; above it the ceiling is, and the warning is skipped.
_EVICT_WARN_HEADROOM = 0.9

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
_OVERFLOW_HINTS = ("context_length_exceeded", "maximum context length", "context window")

_OVERFLOW_TOKENS_RE = re.compile(
    r"(?:maximum context length(?: is)?|context (?:window|length) of(?: only)?|"
    r"context_length_exceeded\D{0,40}?)\s*(\d{4,7})\s*tokens", re.IGNORECASE)



def parse_overflow_limit(text: str) -> int | None:
    """The provider-stated maximum context TOKENS from a context-overflow error message,
    or None when the text is not an overflow error (or states no usable figure).
    """
    low = text.lower()
    if not any(h in low for h in _OVERFLOW_HINTS):
        return None
    m = _OVERFLOW_TOKENS_RE.search(text)
    return int(m.group(1)) if m else None

def _override_window(loop, ref):
    """This run's corrected view of a model's window, when the guard has shrunk it: every
    turn re-picks the ref from the registry (which still carries the catalog's figure), so
    the correction is re-applied here rather than by mutating shared registry state.
    """
    overrides = getattr(loop, "_window_overrides", None)
    shrunk = overrides.get((ref.endpoint, ref.model)) if overrides else None
    if shrunk and shrunk < ref.context_chars:
        return dataclasses.replace(ref, context_chars=shrunk)
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
    corrected = int(stated * CHARS_PER_TOKEN)
    key = (ref.endpoint, ref.model)
    overrides = getattr(loop, "_window_overrides", None)
    if overrides is None:
        overrides = loop._window_overrides = {}
    if overrides.get(key, float("inf")) <= corrected or corrected >= ref.context_chars:
        return None
    overrides[key] = corrected
    new_ref = dataclasses.replace(ref, context_chars=corrected)
    cl = clamp_to_cap(loop.messages, new_ref.context_chars, new_ref.max_tokens)
    ctx = loop.ctx
    ctx.transcript.event("compaction", {"window_guard": {
        "model": ref.name or ref.model, "configured_chars": ref.context_chars,
        "provider_max_tokens": stated, "corrected_chars": corrected,
        **({"clamp": cl} if cl else {})}})
    log_health_event(ctx.server.routines_home, "model_window_corrected",
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=(f"{ref.name or ref.model}: catalog claims "
                             f"{ref.context_chars:,} context chars but the provider "
                             f"enforces {stated:,} tokens — run continues on "
                             f"{corrected:,} chars; correct the catalog entry"))
    return endpoint, new_ref

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
    cl = clamp_to_cap(loop.messages, ref.context_chars, ref.max_tokens)
    if cl:
        ctx.transcript.event("compaction", {"clamp": cl})
        loop._last_compact_after = messages_size(loop.messages)

def _warn_before_eviction(loop, size: float, ref) -> bool:
    """Give the run ONE turn to move what matters into a durable store before the middle goes.

    Retention is positional — head 6, tail 24 — so a load-bearing fact in the middle survives
    only in `history/`, reachable if the run remembers to go looking. `note`, `memory_write`
    and the LEDGER already exist to carry a fact out of the conversation; what was missing was
    the moment to use them, which is precisely the one this layer exists to supply. Returns
    True when the archive should wait a turn.

    **Deferring is safe by construction, and that is what makes this cheap.** The gate is
    `min(fraction × window, ceiling)`, and when the FRACTION binds there is 20–40% of the
    window between here and the hard ceiling — a whole turn of slack. When the CEILING binds
    there is none, so the warning is skipped and the archive happens now: a warning that
    overflowed the window would cost the run the very turns it was trying to protect. Either
    way `clamp_to_cap` still runs unconditionally afterwards, so a deferred turn cannot 400.

    Once per run. A second warning would be the layer talking about itself.
    """
    if loop._evict_warned:
        return False
    ceiling = window_ceiling_chars(ref.context_chars, ref.max_tokens)
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
    """When the prompt exceeds the compaction gate, archive the middle to a navigable on-disk
    history via the LLM (compact_to_history); fall back to the deterministic one-line digest if
    that fails, so a run never stalls on compaction. Does NOT guarantee the result clears the
    window — that is the caller's `clamp_to_cap` step (the head+tail floor is incompressible).
    """
    ctx = loop.ctx
    size = messages_size(loop.messages)
    # Observed cache hits flip the economics: re-reading carried context costs ~0.1x,
    # while compacting rewrites the prefix and invalidates the whole cache — so compact
    # later (0.8) once the provider demonstrably serves from cache, earlier (0.6) when
    # every turn re-reads at full price. The cap also reserves room for the model's
    # OUTPUT (ref.max_tokens): the provider counts prompt + requested output against ONE
    # window, so a small-window model must compact before input + max_tokens overflows it
    # (F265). Both use the MODEL's window, not the endpoint default.
    context_cap = input_cap_chars(ref.context_chars, ref.max_tokens,
                                  cached=bool(ctx.usage.get("cached_in")))
    # Long prompts also burn the token BUDGET — every turn re-sends everything, so a
    # bloated prompt taxes each remaining turn. Once the prompt would eat >10% of the
    # remaining token budget per turn, archive it: the one compaction call costs what
    # the bloat would keep costing every single turn. Floored so a small prompt near
    # budget exhaustion doesn't thrash (compaction itself spends tokens).
    remaining = ctx.tokens_remaining()   # None = unlimited → only the context cap applies
    budget_cap = (float("inf") if remaining is None
                  else max(40_000.0, 0.10 * 4 * remaining))
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
    if middle_n < 8 or size < loop._last_compact_after + 20_000:
        return
    if _warn_before_eviction(loop, size, ref):
        return          # one turn to externalize what matters; the archive happens next turn
    # Archival is machine work — route it to the (usually cheaper) tool-call model
    # whenever its window can hold the middle being archived; the main model is the
    # fallback, never the default.
    c_endpoint, c_ref = endpoint, ref
    try:
        t_endpoint, t_ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        middle_size = messages_size(
            loop.messages[KEEP_HEAD_MSGS:len(loop.messages) - KEEP_TAIL_MSGS])
        if t_ref.context_chars * 0.7 >= middle_size:
            c_endpoint, c_ref = t_endpoint, t_ref
    except Exception:
        pass
    cinfo = None
    degraded = None
    try:
        result = compact_to_history(loop.messages, loop.turn_records, c_endpoint, c_ref,
                                    ctx.run_dir, loop._hist_rel)
    except Exception as exc:
        # A failed archival is a DESIGNED degrade (the deterministic digest takes the
        # pass), not a run error — a red error card for it alarmed operators (F376).
        # The reason stays visible: it rides on the compaction event below.
        degraded = str(exc)[:300]
        result = None
    if result is not None:
        loop.messages, cinfo = result
        loop._history_active = True
        loop._hist_note_countdown = 0   # the next observation carries the history pointer
    else:
        loop.messages, cinfo = maybe_compact(loop.messages, loop.turn_records,
                                             ref.context_chars)
        if cinfo is not None and degraded:
            cinfo["archival_degraded"] = degraded
    if cinfo:
        if cinfo.get("usage"):
            ctx.add_usage(cinfo["usage"])   # the archival call itself now hits the books
        loop._last_compact_after = messages_size(loop.messages)
        # `anticipated` says this pass was taken EARLY, at a stage boundary, rather than because
        # the prompt had actually crossed the gate — without it the two are indistinguishable in
        # the transcript and the feature could not be evaluated after the fact.
        ctx.transcript.event("compaction",
                             {**cinfo, **({"anticipated": ctx.phase} if at_boundary else {})})
    elif degraded:
        # digest found nothing to elide either — the failed archival must still be visible
        ctx.transcript.event("compaction", {"archival_degraded": degraded})

def apply_media_fallback(loop, exc: EndpointError) -> bool:
    """The main endpoint failed on a turn whose tail user message carries image `media`
    (it rejected the file, or claude-cli's stream-json path is unavailable). Convert that
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
