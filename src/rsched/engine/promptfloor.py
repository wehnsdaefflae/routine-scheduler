"""THE PROMPT FLOOR AT BOOT — fit the composed prompt into the window, and state what that cost.

D223 (second half of F539): a run whose incompressible prompt already exceeds the resolved
window before turn one is STARTED, but the engine's first act is a hard compaction that drops
the middle to whatever fits, and the loss is stated. A thread that answers from a smaller
context is worth more than one that cannot be continued at all.

The window compared is the CATALOG window; a run-local calibration may later make the real
clamp smaller. Acting on an estimate is acceptable here because compaction is not a veto —
acting early costs context still readable on disk.

Eliding the middle is `compaction.maybe_compact`'s and trimming bodies `compaction.clamp_to_cap`'s,
called in that order and not reimplemented. The archival model pass is deliberately NOT run
(no model call before turn one). The elided middle survives in `transcript.jsonl` (and a
resumed thread's replayed events), so nothing is destroyed. Rewriting the message list here
breaks no cache: it happens before the first request.

THE LOSS IS STATED: an ENGINE NOTE (`enginenote.append`) reaches the live prompt and renders as
a `user_injection` on both pages; the health event carries the same facts as fields.

When it cannot fit (head, tail and the system prompt are incompressible), that is recorded
(`fits: false`) and said once; it neither loops nor raises, and the first completion fails at
the provider as it would have anyway. Nothing here may fail a boot: every failure returns.
"""

from __future__ import annotations

from typing import Any

#: The event name; must also be registered in `health_vocab.EVENTS` or no filter sees it.
EVENT = "prompt_floor_over_window"


def check(loop: Any) -> dict | None:
    """Fit this run's composed prompt into its model's window, before turn one.

    Returns what the compaction did (and what it could not do), or None when nothing was due —
    the common case, a prompt that already fits. Never raises: `boot` calls it for effect.
    """
    try:
        ctx = getattr(loop, "ctx", None)
        if ctx is None or not getattr(loop, "messages", None):
            return None
        from .compaction import estimate_input_tokens, window_ceiling_tokens

        floor = int(estimate_input_tokens(loop.messages))
        ref = _resolved_ref(ctx)
        window = int(getattr(ref, "context_tokens", 0) or 0)
        reserved = _reserved_tokens(loop, ref)
        ceiling = window_ceiling_tokens(window, reserved)
        if window <= 0 or floor <= ceiling:
            return None
        info = _fit(loop, window, reserved)
        info.update(floor_tokens=floor, window_tokens=window, ceiling_tokens=ceiling,
                    model=_model_name(ref))
        _state_the_loss(loop, info)
        _file_event(ctx, info)
        return info
    except Exception:
        # Best-effort: an unresolvable model or malformed message list is not fitted and the run
        # starts unchanged. (A compaction that RUNS and still cannot fit records `fits: false`.)
        return None


def _resolved_ref(ctx):
    """The model this run will actually complete against, as the registry resolves it."""
    _endpoint, ref = ctx.registry.for_model("main", ctx.routine.models, ctx.routine.efforts)
    return ref


def _reserved_tokens(loop, ref) -> int:
    """Output plus the separately transmitted action schema — `window._reserved_tokens`'s pair.
    Read defensively: before turn one a field the loop sets later is simply not counted.
    """
    from .promptsegments import schema_tokens

    return int(getattr(ref, "max_tokens", 0) or 0) + int(schema_tokens(loop))


def _model_name(ref) -> str:
    return str(getattr(ref, "name", None) or getattr(ref, "model", None) or "?")


#: Room kept for the note this module appends — the statement is part of the prompt, so part
#: of the budget. Generous on purpose: the note is a few hundred tokens at most.
_NOTE_TOKENS = 400


def _fit(loop, window: int, reserved: int) -> dict:
    """Elide the middle, then trim what is left, to the window's input ceiling LESS the note.

    `after_tokens` is the compaction's result, measured before the note is appended, and
    `fits` already accounts for it: the prompt the first request carries is this plus
    `_NOTE_TOKENS`.
    """
    from .compaction import (
        clamp_to_cap,
        estimate_input_tokens,
        maybe_compact,
        window_ceiling_tokens,
    )

    target = window_ceiling_tokens(window, reserved + _NOTE_TOKENS)
    records = getattr(loop, "turn_records", None) or []
    loop.messages, elided = maybe_compact(loop.messages, records, target)
    clamped = clamp_to_cap(loop.messages, window, reserved + _NOTE_TOKENS)
    after = int(estimate_input_tokens(loop.messages))
    return {"elided_messages": int((elided or {}).get("elided_messages") or 0),
            "clamped_messages": int((clamped or {}).get("clamped_messages") or 0),
            "after_tokens": after, "target_tokens": target, "fits": after <= target}


def _state_the_loss(loop, info: dict) -> None:
    """Say what the compaction cost, where it is actually read: one engine note reaches both the
    live prompt and the rendered `user_injection`.
    """
    from . import enginenote
    from .compaction import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS

    opening = (f"HARD COMPACTION BEFORE TURN ONE (D223): this thread's composed prompt measured "
               f"{info['floor_tokens']:,} estimated tokens against {info['model']}'s "
               f"{info['window_tokens']:,}-token window, so as it stood it could not have "
               f"completed a single turn.")
    parts = [opening]
    if info["elided_messages"]:
        parts.append(f"The MIDDLE was dropped to make it fit: {info['elided_messages']} "
                     f"messages are no longer in your context, leaving ~{info['after_tokens']:,} "
                     f"estimated tokens. The first {KEEP_HEAD_MSGS} messages and the last "
                     f"{KEEP_TAIL_MSGS} are intact, and the digest above is one line per dropped "
                     f"turn — that is all of the middle you can see.")
    if info["clamped_messages"]:
        parts.append(f"{info['clamped_messages']} oversized message bodies were truncated in "
                     f"place, each marked where it was cut.")
    if not info["elided_messages"] and not info["clamped_messages"]:
        parts.append("There was no middle to drop and no body worth truncating: the opening "
                     "prompt alone is over the window.")
    if not info["fits"]:
        parts.append("It still does not fit — the head, the tail and the system prompt are "
                     "incompressible. Expect the first completion to be refused for length, and "
                     "say so rather than retrying it unchanged.")
    parts.append("Nothing was destroyed: the full conversation is on disk in this run's "
                 "transcript.jsonl and read_file reaches it. Answer from what you can see, and "
                 "say when an answer depends on what was dropped.")
    enginenote.append(loop, " ".join(parts))


def _file_event(ctx, info: dict) -> None:
    """The count, with fields saying what was DONE about it."""
    from ..health_events import log_health_event

    verdict = (f"compacted to ~{info['after_tokens']:,} estimated tokens" if info["fits"]
               else "still over the window afterwards — head, tail and system prompt are "
                    "incompressible, so the first completion will be refused for length")
    log_health_event(
        ctx.server.routines_home, EVENT, routine=ctx.routine.slug, run_id=ctx.run_id,
        detail=(f"the composed prompt estimated {info['floor_tokens']:,} tokens against "
                f"{info['model']}'s {info['window_tokens']:,}-token window: the run was started "
                f"and its middle hard-compacted first (D223) — {info['elided_messages']} "
                f"messages elided, {info['clamped_messages']} bodies trimmed, {verdict}. The "
                f"loss is stated in the run's own prompt and transcript."),
        # FIELDS, not only prose: `fits: false` is the row a sweep must be able to find.
        floor_tokens=info["floor_tokens"], window_tokens=info["window_tokens"],
        ceiling_tokens=info["ceiling_tokens"], after_tokens=info["after_tokens"],
        elided_messages=info["elided_messages"], clamped_messages=info["clamped_messages"],
        fits=info["fits"], model=info["model"])
