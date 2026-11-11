"""THE PROMPT FLOOR AT BOOT — fit the composed prompt into the window, and state what that cost.

The second half of F539, decided by the operator as D223. The first half shipped in 0.474.0: a
run served by a smaller model is TOLD its window, durably (`engine/substitution.py`). What
stayed open was what to do about a run whose incompressible floor exceeds the resolved window
before it has completed a single turn.

The first answer (2026-10-30) was a count and nothing else: file a health event, start the run
unchanged. That bought the evidence it was asked for. `prompt_floor_over_window` then fired NINE
times for ONE conversation, `c-20261014-134208` — three times on 2026-11-08 (1,063,175 →
1,073,520 → 1,085,503 estimated tokens) and six more on 2026-11-10 (1,089,162 → 1,138,237),
every attempt measuring bigger than the one before, against a 1,000,000-token window. The same
thread had fired `prompt_oversize_shrunk` seven times on 2026-11-02: a week of successful
shrinking, and then the floor crossed the window and shrinking stopped being possible. 1,333
turns sit on disk and read normally; what was gone was the ability to ADD to them.

HIS SECOND ANSWER (2026-11-10), verbatim: *"start it, but have the engine's FIRST act be a hard
compaction that drops the middle to whatever fits, so the thread continues with a stated loss"*.

He was offered the refusal — decline the start and tell him the thread is full — and chose
against it. A thread that answers from a smaller context is worth more than one that cannot be
continued at all. So this module no longer records and steps aside; it SHRINKS the prompt and
says what it dropped.

WHY COMPACTING MAY ACT ON A FIGURE A REFUSAL COULD NOT. The window compared here is the CATALOG
window, and an F278 run-local correction can make the real clamp smaller than it
(`window._calibrated_window` derives that ratio from a completion's reported usage, and at boot
no completion has happened). That approximation is exactly why refusing was the wrong call: a
veto resting on an estimate turns a degraded run into no run. Compaction is not a veto. Acting a
little early costs context the run can still read off disk; acting too late costs the turn.

WHAT IT DOES, AND WHAT IT DOES NOT OWN. Eliding the middle belongs to `compaction.maybe_compact`
(head 6, a one-line-per-turn digest, tail 24) and trimming oversized bodies to
`compaction.clamp_to_cap`; both are called here, in that order, and neither is reimplemented.
The archival model pass (`window._archive_if_needed` → `archival.start`) is deliberately NOT
run: it is a model call, and the engine's first act before turn one is not the place for one.
The elided middle therefore survives in this run's `transcript.jsonl` — and, for a resumed
thread, in the events the next leg replays (`history.replay_messages` rebuilds from events and
ignores compaction) — so nothing is destroyed, only taken out of the context. Rewriting the
message list is what `window.py`'s header calls a break in the prompt-caching contract; here it
happens BEFORE the first request, so there is no cached prefix to invalidate — the first thing
the provider ever sees is the compacted prompt.

THE LOSS IS STATED, which is the load-bearing half of his answer. A conversation whose middle is
discarded in silence answers from a context it no longer has, and sounds exactly as certain as
one that can see everything. So the compaction writes an ENGINE NOTE (`enginenote.append`),
which lands in the live prompt where the next turn's model reads it AND is recorded as a
`user_injection` that both pages render — the conversation page as a system line
(`static/components/chat.js`) and the run transcript (`static/components/transcript.js`). The
health event carries the same facts as fields, for counting rather than for reading.

WHEN IT CANNOT BE MADE TO FIT. The head, the tail and the composed system prompt are
incompressible: `maybe_compact` returns unchanged below 30 messages (a fresh boot has two), and
`clamp_to_cap` never touches message 0, the largest body in every run. A 32,768-token
substitute under a 200k-class prompt — the F539 specimen `llmsectest-weekday:20260922-060001`,
dead at turn 106 having spent $1.03 — cannot be fitted by any amount of eliding. That case is
recorded (`fits: false`), and SAID in the note, once. It does not loop and it does not raise:
the run starts, as he asked, and the first completion then fails at the provider the way it
would have anyway.

BEST-EFFORT, BUT NO LONGER SILENT. Nothing here may be the reason a boot fails, so every failure
path returns instead of raising. What changed is that silence is no longer an outcome: a
compaction that cannot be performed leaves the run in the exact state it could not survive, so
the event is filed with what was attempted rather than nothing at all.
"""

from __future__ import annotations

from typing import Any

#: The event name. Registered in `health_vocab.EVENTS` in the same change — an event at a
#: call site and absent from that enum is invisible to every documented filter, which is the
#: `prompt_oversize_shrunk` failure `tests/test_health_events.py` exists to catch.
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
        # The measurement and the compaction are both best-effort: an unresolvable model, a
        # stand-in loop, a malformed message list are not fitted, and the run starts exactly as
        # it did before this module existed. A layer that can stop a boot is worse than a layer
        # that occasionally does nothing — but see `fits: false`: when the compaction RUNS and
        # still cannot clear the window, that is recorded rather than swallowed.
        return None


def _resolved_ref(ctx):
    """The model this run will actually complete against, as the registry resolves it."""
    _endpoint, ref = ctx.registry.for_model("main", ctx.routine.models, ctx.routine.efforts)
    return ref


def _reserved_tokens(loop, ref) -> int:
    """Output plus the separately transmitted action schema — `window._reserved_tokens`'s pair.

    Read defensively: this runs before the first turn, so a field the loop sets later is simply
    not counted, and the ceiling is then the window itself.
    """
    from .promptsegments import schema_tokens

    return int(getattr(ref, "max_tokens", 0) or 0) + int(schema_tokens(loop))


def _model_name(ref) -> str:
    return str(getattr(ref, "name", None) or getattr(ref, "model", None) or "?")


#: Room kept for the note this module is about to append. Compacting to the exact ceiling and
#: THEN stating the loss would put the prompt back over it — the statement is part of the
#: prompt, so it is part of the budget. Generous: the note is bounded prose, a few hundred
#: tokens at its longest, and over-reserving costs context the run can still read off disk.
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
    """Say what the compaction cost, where it is actually read.

    One engine note carries both surfaces his answer requires: the live prompt (the next turn's
    model) and the rendered `user_injection` (the person reading the conversation or the run).
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
    """The count. Same event name as the recording-only version, so the stream stays one series
    — with the fields that now say what was DONE about it, not only that it happened.
    """
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
        # FIELDS, not only prose: "how often, and by how much" is the question this surface
        # exists to answer, and `fits` is the one row a sweep must be able to find — a run the
        # compaction could not rescue.
        floor_tokens=info["floor_tokens"], window_tokens=info["window_tokens"],
        ceiling_tokens=info["ceiling_tokens"], after_tokens=info["after_tokens"],
        elided_messages=info["elided_messages"], clamped_messages=info["clamped_messages"],
        fits=info["fits"], model=info["model"])
