"""Making the turn FIT — the context window, compaction, and the media fallback.

This is the module allowed to rewrite the message list. The composed prompt is a CACHING
CONTRACT — appended-to, never mutated — and compaction and the media fallback are sanctioned
exceptions that invalidate the provider cache deliberately. Recovering from a provider's
overflow 400 lives in `overflow.py`, which builds on this file.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ..endpoints.base import EndpointError
from . import archival, boundary, enginenote, mediaops, promptsegments
from .compaction import (
    KEEP_HEAD_MSGS,
    KEEP_TAIL_MSGS,
    clamp_to_cap,
    estimate_input_tokens,
    input_cap_tokens,
    maybe_compact,
    window_ceiling_tokens,
)
from .historyarchive import archival_fits

#: How close to the HARD ceiling still leaves a turn of slack. Below this the fraction is
#: binding and a deferred turn is free; above it the ceiling is, and the warning is skipped.
_EVICT_WARN_HEADROOM = 0.9

#: The measured estimate→provider ratio is clamped to this band. Below 1.0 there is nothing
#: to correct — an estimate that runs HEAVY already keeps the prompt inside the window, and
#: acting on it would compact later than the gate intends. Above 2.0 the reading is not a
#: tokenizer difference any more but a measurement fault, and a run must not shrink its own
#: window to a third on one bad sample.
_RATIO_BOUNDS = (1.0, 2.0)

#: How much of the PROVIDER'S refusal the media-fallback error event keeps: enough to name the
#: limit it refused on. Written at most once per run, on a path that has already failed.
MEDIA_ERROR_CHARS = 1200

#: Only a prompt already occupying this much of the window calibrates: the provider's request
#: framing is a roughly FIXED gap, so `reported / estimate` explodes on a short prompt.
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

def _reserved_tokens(loop, ref) -> int:
    """Reserve output plus the separately transmitted action schema."""
    return ref.max_tokens + promptsegments.schema_tokens(loop)


def note_prompt_size(loop, ref, usage: dict | None) -> None:
    """Calibrate this run's token ESTIMATE against what the provider just counted, and record
    the same prompt's SEGMENT breakdown (engine/promptsegments.py), before anything appends.

    `estimate_input_tokens` is a packing guess; when it runs light the compaction gate never
    fires and the turn dies at the provider instead. The ratio measured here is applied to the
    next turn by `_calibrated_window`. The schema is subtracted (the provider counted it, the
    message list does not carry it); `ref.max_tokens` is NOT — an output reservation is not
    part of the prompt, and subtracting it would drive every ratio under 1.0.

    Run-local, not per-model: it mostly measures how this CONTENT packs, and every completion
    re-measures it.
    """
    promptsegments.note(loop)     # the same prompt BY SEGMENT, diagnostic, read-only
    reported = sum(int((usage or {}).get(k) or 0) for k in ("in", "cached_in", "cache_write"))
    if reported <= 0:
        return
    estimate = estimate_input_tokens(loop.messages)
    if estimate <= 0 or estimate < _CALIBRATE_ABOVE * ref.context_tokens:
        return
    low, high = _RATIO_BOUNDS
    ratio = (reported - promptsegments.schema_tokens(loop)) / estimate
    loop._token_ratio = min(max(ratio, low), high)


def _calibrated_window(loop, ref):
    """`ref` with its window restated in ESTIMATE tokens — the currency every gate below
    compares against.
    """
    ratio = getattr(loop, "_token_ratio", 1.0)
    if ratio <= 1.0:
        return ref
    return dataclasses.replace(ref, context_tokens=int(ref.context_tokens / ratio))


def compact_if_needed(loop, endpoint, ref) -> None:
    """Keep the next prompt inside the model's window. First ARCHIVE the middle if it has grown
    past the compaction gate (`_archive_if_needed`), then ENFORCE the hard window ceiling as a
    last resort (`clamp_to_cap`) — archiving cannot shrink the incompressible head+tail floor.
    The clamp trims oversized bodies in place with a visible marker; the full text stays in
    the transcript. Every gate sizes in ESTIMATED tokens against `_calibrated_window`.
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

    Retention is positional, so a fact in the middle survives only in `history/`. Returns True
    when the archive should wait a turn; the caller records the pass as OWED
    (`loop._evict_owed`) so the next turn takes it under the cap that decided it.

    Skipped when the hard CEILING (not the fraction) binds: then there is no turn of slack, and
    a warning that overflowed the window would cost the turns it protects. Once per guard scope,
    across a resume too: the note is marked `evict_warning` and `rebuild` reads it back.
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
        "durable store NOW: a `note` (free, rides any action) or a memory_write. Then carry "
        "on; the archive happens on your next turn either way.",
        evict_warning=True)
    return True


def rebuild(loop, events: list[dict]) -> None:
    """The guard scope's resume seed (engine/guardscope.py): a warning already given in it
    stays given.
    """
    loop._evict_warned = any(ev.get("type") == "user_injection"
                             and (ev.get("payload") or {}).get("evict_warning")
                             for ev in events)


def _pick_archival_model(loop, middle: list[dict], endpoint, ref, cinfo: dict):
    """The (endpoint, ref) that will archive this middle, or None when nothing can hold it.

    ONE fit test decides every candidate: `archival_fits`, which reserves the archival prompt,
    the history schema and the model's own output.

    Order: the configured compaction model when it fits (it was chosen on purpose), then the
    tool-call model (machine work belongs on the cheaper tier), then the main model. Every
    rejection is named in `cinfo` so the transcript says which model was skipped and why.
    """
    ctx = loop.ctx
    candidates = []
    dedicated = ctx.server.compaction_model
    if dedicated:
        try:
            d_endpoint, d_ref = ctx.registry.for_name(dedicated, ctx.server.compaction_effort)
            if d_ref.name != dedicated:
                cinfo["archival_selection_fallback"] = (
                    f"{dedicated}: unavailable; catalog fallback {d_ref.name}")
            candidates.append((d_endpoint, d_ref))
        except Exception as exc:
            cinfo["archival_selection_fallback"] = (
                f"{dedicated}: unavailable ({exc}); using Automatic")
    try:
        candidates.append(ctx.registry.for_model(
            "tool_call", ctx.routine.models, ctx.routine.efforts))
    except Exception as exc:
        # Reported like the dedicated model's failure above, `; `-joined onto the same key.
        prior = cinfo.get("archival_selection_fallback")
        note = f"tool_call: unavailable ({exc})"
        cinfo["archival_selection_fallback"] = f"{prior}; {note}" if prior else note
    candidates.append((endpoint, ref))
    declined: list[str] = []
    for c_endpoint, c_ref in ((e, _calibrated_window(loop, r)) for e, r in candidates):
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
    # Compact later once the provider demonstrably serves from cache (compacting invalidates
    # it), earlier when every turn re-reads at full price. The cap reserves the model's OUTPUT
    # too: prompt + requested output count against ONE window.
    context_cap = input_cap_tokens(ref.context_tokens, _reserved_tokens(loop, ref),
                                  cached=bool(ctx.usage.get("cached_in")))
    # A prompt eating >10% of the remaining token BUDGET per turn is archived too; floored so a
    # small prompt near budget exhaustion doesn't thrash.
    remaining = ctx.tokens_remaining()   # None = unlimited → only the context cap applies
    budget_cap = (float("inf") if remaining is None
                  else max(10_000.0, 0.10 * remaining))
    cap = min(context_cap, budget_cap)
    # At a STAGE BOUNDARY the pass is taken whatever the size gate says when archiving now costs
    # less than carrying the finished stage to the end (engine/boundary.py's breakeven test).
    anticipated, economics = boundary.assess(loop, size)
    if economics.get("compact"):
        cap = 0.0
    # A pass the eviction warning DEFERRED is owed now, under the cap that decided it: the run
    # was told the archive happens next turn either way.
    if (owed := getattr(loop, "_evict_owed", None)) is not None:
        loop._evict_owed = None
        cap = min(cap, owed[0])
        anticipated, economics = anticipated or owed[1], economics or owed[2]
    if (size <= cap or len(loop.messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS):
        return
    # Anti-thrash: head + tail are incompressible, so a tiny middle or little growth since the
    # last archive cannot win another pass.
    middle_n = len(loop.messages) - KEEP_HEAD_MSGS - KEEP_TAIL_MSGS
    if middle_n < 8 or size < loop._last_compact_after + 5_000:
        return
    if _warn_before_eviction(loop, size, ref):
        # one turn to externalize what matters; the archive happens next turn, on THIS cap
        loop._evict_owed = (cap, anticipated, economics)
        return
    # The INSTANT tier takes the pass and the run carries straight on; the navigable archive is
    # built off the hot path (engine/archival.py). The digest is a PLACEHOLDER until it lands,
    # never a summary standing in for it.
    middle = loop.messages[KEEP_HEAD_MSGS:len(loop.messages) - KEEP_TAIL_MSGS]
    turn = max((r["turn"] for r in loop.turn_records), default=0)
    loop.messages, cinfo = maybe_compact(loop.messages, loop.turn_records, cap)
    if cinfo is not None:
        picked = _pick_archival_model(loop, middle, endpoint, ref, cinfo)
        if picked is None:
            # No candidate's window holds the archival request; posting anyway buys a 400, so
            # the deterministic digest stands and the transcript says why.
            cinfo["archival"] = "skipped"
        else:
            c_endpoint, c_ref = picked
            archival.start(loop, middle, c_endpoint, c_ref, turn)
            cinfo["archival"] = "background"
    if cinfo:
        # the archival call's spend is booked by archival.collect, on the turn the
        # archive lands — this pass is the deterministic digest and calls no model
        loop._last_compact_after = estimate_input_tokens(loop.messages)
        # `anticipated`/`economics` tell a boundary pass from a forced one in the transcript.
        ctx.transcript.event("compaction",
                             {**cinfo, **({"anticipated": anticipated} if anticipated else {}),
                              **({"economics": economics} if economics else {})})


def apply_media_fallback(loop, exc: EndpointError) -> bool:
    """The endpoint failed on a turn whose prompt carries image `media` (it rejected the
    file, or serves no model that can see one). Convert EVERY media-bearing message to
    vision-util text in place and drop the attachments, so the retried completion is
    text-only and the model still gets the content. False when the prompt carries no media
    at all — then the failure is a genuine endpoint error that must propagate.

    EVERY message, not just the tail: a later message may follow the media-bearing one, and an
    image anywhere is a liability to a model that cannot see one. A deliberate, sanctioned
    break in the prompt-caching contract.
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
    # The DESCRIPTIONS the model then reasons from go on the record, and the provider's own
    # message is kept long enough to name the bound it refused on (MEDIA_ERROR_CHARS).
    loop.ctx.transcript.event("error", {"where": "media",
        "message": f"this run's model could not show {files} file(s) in {converted} message(s) "
                   f"({str(exc)[:MEDIA_ERROR_CHARS]}); fell back to the vision util",
        "descriptions": [m["content"][-2000:] for m in loop.messages
                         if "description from the vision util]" in m["content"]][-3:]})
    return True
