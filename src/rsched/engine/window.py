"""Making the turn FIT — the context window, compaction, and the media fallback.

Split out of `completion.py` (F393): asking the model for one action and keeping the request
inside the provider's limits are different jobs, and only this one is allowed to rewrite the
message list. Reading a provider's overflow 400 and recovering from it is a third job and
lives in `overflow.py`, which builds on this file.

That permission is the reason it is worth isolating. The composed prompt is a CACHING CONTRACT
— appended-to, never mutated — and compaction, the schema-retry cleanup and the media fallback
are the three sanctioned exceptions, each invalidating the provider cache deliberately. Every
other seam in the engine appends.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ..endpoints.base import EndpointError
from . import archival, enginenote, mediaops
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

#: The measured estimate→provider ratio is clamped to this band. Below 1.0 there is nothing
#: to correct — an estimate that runs HEAVY already keeps the prompt inside the window, and
#: acting on it would compact later than the gate intends. Above 2.0 the reading is not a
#: tokenizer difference any more but a measurement fault, and a run must not shrink its own
#: window to a third on one bad sample.
_RATIO_BOUNDS = (1.0, 2.0)

#: Only a prompt already occupying this much of the window calibrates. The provider counts
#: things the message list never carries — the request framing around every message — so the
#: gap is roughly FIXED and `reported / estimate` explodes on a short prompt: an early
#: 18-token turn would otherwise write a 1.8 ratio and compact a healthy run every few turns.
#: These are also the only turns where the correction changes any decision.
_CALIBRATE_ABOVE = 0.2


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

def _schema_tokens(loop) -> int:
    """The action schema's own cost. It travels BESIDE the message list — the provider
    counts it in the prompt, `estimate_input_tokens(loop.messages)` never sees it.
    """
    schema = getattr(loop, "action_schema", None)
    if not schema or getattr(loop, "_schema_off", False):
        return 0
    return estimate_input_tokens([{"content": json.dumps(schema, ensure_ascii=False)}])


def _reserved_tokens(loop, ref) -> int:
    """Reserve output plus the separately transmitted action schema."""
    return ref.max_tokens + _schema_tokens(loop)


def note_prompt_size(loop, ref, usage: dict | None) -> None:
    """Calibrate this run's token ESTIMATE against what the provider just counted.

    `estimate_input_tokens` is 3.5 UTF-8 bytes per token — a packing guess, not a tokenizer
    — and every gate in this file compares it against a real window. When it runs light the
    compaction gate never fires and the turn dies at the provider instead: self-audit
    20260921-000321 took four `prompt is too long: 1017305 tokens > 1000000 maximum` 400s in
    one run (6 such 400s in a fortnight), each one a full-prompt round trip plus a shrink
    pass. Until now the only correction came FROM that 400 (`_recover_oversize_prompt`),
    although every completion already reports the true prompt size.

    So the ratio is measured on the turn that just came back and applied to the next one by
    `_calibrated_window`. The schema is subtracted because the provider counted it and the
    message list does not carry it; `ref.max_tokens` is NOT, because a provider counts an
    output reservation against the window, never as part of the prompt — subtracting it
    would drive every ratio under 1.0 and disarm the whole correction.

    It is run-local, not per-model: what it mostly measures is how this CONTENT packs (a
    German transcript packs differently from JSON), and every completion re-measures it, so a
    chain step onto a different tokenizer carries the old ratio for exactly one turn.
    """
    reported = sum(int((usage or {}).get(k) or 0) for k in ("in", "cached_in", "cache_write"))
    if reported <= 0:
        return
    estimate = estimate_input_tokens(loop.messages)
    if estimate <= 0 or estimate < _CALIBRATE_ABOVE * ref.context_tokens:
        return
    low, high = _RATIO_BOUNDS
    loop._token_ratio = min(max((reported - _schema_tokens(loop)) / estimate, low), high)


def _calibrated_window(loop, ref):
    """`ref` with its window restated in ESTIMATE tokens — the currency every gate below
    compares against. A run whose estimate reads 30% light has, in those units, 30% less
    window than the catalog says, and that is the honest figure to compact against.
    """
    ratio = getattr(loop, "_token_ratio", 1.0)
    if ratio <= 1.0:
        return ref
    return dataclasses.replace(ref, context_tokens=int(ref.context_tokens / ratio))


def compact_if_needed(loop, endpoint, ref) -> None:
    """Keep the next prompt inside the model's window. First ARCHIVE the middle if it has grown
    past the compaction gate (`_archive_if_needed`), then ENFORCE the hard window ceiling as a
    last resort (`clamp_to_cap`) — because archiving cannot shrink the incompressible head+tail
    floor and a short conversation has no middle to elide, so a run with a few very large
    observations would otherwise 400 with context_length_exceeded and die (F265, three
    recurrences on c-20260802-110156). The clamp trims oversized bodies in place with a visible
    marker; the full text stays in the transcript.

    Every gate below sizes in ESTIMATED tokens, so the window they are given is this run's
    measured view of it (`_calibrated_window`) and not the catalog's figure.
    """
    ref = _calibrated_window(loop, ref)
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
    enginenote.append(loop,
        "the middle of this conversation is about to be ARCHIVED — the first "
        f"{KEEP_HEAD_MSGS} and last {KEEP_TAIL_MSGS} messages stay, everything between them "
        "moves to on-disk history you would have to go looking for. Retention is positional, "
        "not semantic: it does not know what mattered.\n"
        "You have this turn. Anything in the middle worth keeping — a finding, a value you "
        "will need again, a dead end worth not repeating, a decision and why — put it in a "
        "durable store NOW: a `note` (free, rides any action), a memory_write, or a LEDGER "
        "entry. Then carry on; the archive happens on your next turn either way.")
    return True


def _pick_archival_model(loop, middle: list[dict], endpoint, ref, cinfo: dict):
    """The (endpoint, ref) that will archive this middle, or None when nothing can hold it.

    ONE fit test decides every candidate: `archival_fits`, which reserves the archival prompt,
    the history schema and the model's own output. There used to be two — the tool-call branch
    compared a bare `context_tokens * 0.7` against the middle alone, which is the exact gap a
    90,005-token archival slipped through into a 32,768-token window (llmsectest-weekday:
    20260922-060001, turn 106: the run died AND its archive was lost in the same minute).

    Order: the configured compaction model when it fits (it was chosen on purpose), then the
    tool-call model (machine work belongs on the cheaper tier), then the main model. Every
    rejection is named in `cinfo` so the transcript says which model was skipped and why.
    """
    ctx = loop.ctx
    candidates = []
    dedicated = ctx.server.compaction_model
    if dedicated:
        try:
            d_endpoint, d_ref = ctx.registry.for_name(dedicated)
            if d_ref.name != dedicated:
                cinfo["archival_selection_fallback"] = (
                    f"{dedicated}: unavailable; catalog fallback {d_ref.name}")
            candidates.append((d_endpoint, d_ref))
        except Exception as exc:
            cinfo["archival_selection_fallback"] = (
                f"{dedicated}: unavailable ({exc}); using Automatic")
    try:
        candidates.append(ctx.registry.for_model("tool_call", ctx.routine.models))
    except Exception:
        pass
    candidates.append((endpoint, ref))
    declined: list[str] = []
    for c_endpoint, c_ref in candidates:
        if archival_fits(middle, c_ref):
            if dedicated:
                cinfo["archival_model"] = c_ref.name or c_ref.model
            if declined:
                cinfo["archival_selection_fallback"] = (
                    "; ".join(declined) + f" — archiving on {c_ref.name or c_ref.model}")
            return c_endpoint, c_ref
        declined.append(f"{c_ref.name or c_ref.model}: the archival request does not fit "
                        f"its {c_ref.context_tokens:,}-token window")
    cinfo["archival_skipped"] = (
        f"the middle is ~{estimate_input_tokens(middle):,} estimated tokens and no available "
        f"model can archive it ({'; '.join(declined)}) — the deterministic digest stands "
        "alone for these turns")
    return None


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
    loop.messages, cinfo = maybe_compact(loop.messages, loop.turn_records, cap)
    if cinfo is not None:
        picked = _pick_archival_model(loop, middle, endpoint, ref, cinfo)
        if picked is None:
            # Every candidate's window is too small to hold the middle plus the archival
            # prompt, the schema and its own output reserve. Posting it anyway buys a 400
            # and loses the middle just the same (llmsectest-weekday:20260922-060001: a
            # 90,005-token archival posted to a 32,768-token fallback), so the run keeps
            # the deterministic digest and the transcript says why the archive is missing.
            cinfo["archival"] = "skipped"
        else:
            c_endpoint, c_ref = picked
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
    """The endpoint failed on a turn whose prompt carries image `media` (it rejected the
    file, or serves no model that can see one). Convert EVERY media-bearing message to
    vision-util text in place and drop the attachments, so the retried completion is
    text-only and the model still gets the content. False when the prompt carries no media
    at all — then the failure is a genuine endpoint error that must propagate.

    EVERY message, not just the tail. The tail is where the image usually is, and that is
    exactly why looking only there failed the one case that mattered: on 2026-09-22 a user
    message landed AFTER the media-bearing observation, so the next 400 found no media on
    the tail, returned False, and the chain walked four models — three of them
    `multimodal: false` — to `does not support image inputs` and killed the conversation
    (c-20260922-072125). An image anywhere in the prompt is a liability to a model that
    cannot see one; converting the lot costs one vision call each and keeps the run alive.

    This rewrites the message list, which is deliberate and one of the three sanctioned
    breaks in the prompt-caching contract.
    """
    converted = 0
    files = 0
    for msg in loop.messages:
        media = msg.get("media")
        if not media:
            continue
        notes = []
        for item in media:
            desc = mediaops.vision_describe(loop.ctx, item["path"], "")
            notes.append(f"[{Path(item['path']).name}: this run's model could not display it — "
                         f"description from the vision util]\n{desc}")
        msg.pop("media", None)
        msg["content"] = msg["content"] + "\n\n" + "\n\n".join(notes)
        converted += 1
        files += len(media)
    if not converted:
        return False
    # The DESCRIPTION is what the model then reasons from, so it belongs on the record: the
    # event used to carry a 120-char excerpt of the exception and nothing else, and a run
    # reporting "the vision fallback returned empty text" could be neither confirmed nor
    # refuted from disk.
    loop.ctx.transcript.event("error", {"where": "media",
        "message": f"this run's model could not show {files} file(s) in {converted} message(s) "
                   f"({str(exc)[:120]}); fell back to the vision util",
        "descriptions": [m["content"][-2000:] for m in loop.messages
                         if "description from the vision util]" in m["content"]][-3:]})
    return True
