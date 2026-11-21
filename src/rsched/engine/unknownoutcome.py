"""A resumed leg is TOLD when its last action's outcome is UNKNOWN (D278).

The engine records `assistant_action` BEFORE dispatch and the `observation` after it, so a
process killed between the two leaves an action with no result — and a model that sees no result
for an OUTWARD action (mail sent, a claim filed) is likely to issue it AGAIN. So boot writes an
ordinary `observation` event (payload `kind` = `outcome_unknown`, not a new event type) answering
each unanswered action; being a real event, a later resume replays it verbatim.

## The trap: `finish` writes no observation

`chain.take_turn` returns `loop._finish(action)` without `_observe`, so every normally finished
run ends in a bare `assistant_action`. `kind == "finish"` is therefore excluded EXPLICITLY — not
by testing for a trailing `finish` event, since a kill between the finish action and its event
leaves the same bare shape.

## What it does NOT claim

It reports the ABSENCE of a result, so resume guards treat it as having executed nothing:
`donothing.NON_GROUNDING_KINDS` holds this kind (no passing the fabrication guard on it), and the
dead action's kind is kept OUT of the payload's `kind` field so `history.seen_paths` cannot read
it as a file seen.
"""

from __future__ import annotations

#: The observation payload kind. One string, read by the renderer below and by
#: `donothing.NON_GROUNDING_KINDS`.
KIND = "outcome_unknown"


def unanswered(events: list[dict]) -> list[dict]:
    """The TRAILING `assistant_action` events whose `observation` was never written.

    Scanned backwards and stopped at the first `observation`; other event types are passed
    over. Returned in transcript order. A chained reply observes each element in turn, so a
    kill leaves at most the one element that was running.
    """
    out: list[dict] = []
    for ev in reversed(events):
        etype = ev.get("type")
        if etype == "observation":
            break
        if etype != "assistant_action":
            continue
        if (ev.get("payload") or {}).get("kind") == "finish":
            continue          # see the module docstring: the trap, not an oversight
        out.append(ev)
    out.reverse()
    return out


def note(action_kind: str) -> str:
    """The words the resumed leg reads. Prompt surface (`docs/prompt-anatomy.md`), so they live
    here alone and both the live write and every later replay render them from this one place.
    """
    return (f"OBSERVATION (outcome UNKNOWN): the process ended while your `{action_kind}` "
            "action was running, so its result was never recorded. It may or may not have "
            "taken effect — check its effect before repeating it. Anything it would have done "
            "outside this run (a message sent, a file written, a request filed) may already "
            "have happened.")


def format_unknown(obs: dict, kind: str) -> str | None:
    """This kind's wording for `observations._observation_body`'s chain; None for any other."""
    if kind != KIND:
        return None
    return note(str(obs.get("action_kind") or "?"))


def record(ctx, events: list[dict]) -> None:
    """Answer every unanswered trailing action, on disk AND in `events`.

    Appending to `events` (what boot replays from) puts the note in the resumed leg's first
    prompt; writing it to the transcript makes a later resume replay the same words. Written on
    the dead action's own turn, so it reads as that action's answer.
    """
    for ev in unanswered(events):
        turn = ev.get("turn")
        events.append(ctx.transcript.event(
            "observation",
            {"kind": KIND, "action_kind": str((ev.get("payload") or {}).get("kind") or "?")},
            turn=turn if isinstance(turn, int) else None))
