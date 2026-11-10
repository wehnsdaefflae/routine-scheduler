"""D278: a resumed leg is TOLD when its last action's outcome is UNKNOWN.

The engine records `assistant_action` BEFORE dispatch (`loop.py`) and the `observation` after
it, so a process killed between the two — a deploy, the D99 SIGKILL auto-resume,
`daemon/reap_resume.py` after a restart — leaves the transcript ending in an action with no
result. `history.replay_messages` replays that action as the last assistant message, and the
only framing the resumed leg used to get was `rewind._RESUMED`: "continue from the last
observation", which says nothing about the last action maybe having happened. For an OUTWARD
action that is the costly case — mail sent, a claim filed, a return submitted — because a model
that sees no result is likely to issue the action AGAIN.

So boot writes an ordinary `observation` event answering each unanswered action, saying the
outcome is unknown and to check its effect before repeating it. A real transcript event, not an
engine note: the prompt stays append-only and a later resume replays it verbatim instead of
re-authoring it.

NOT A NEW TRANSCRIPT EVENT TYPE. An `observation` whose payload `kind` is `outcome_unknown`
suffices; a new `EVENT_TYPES` member would cost five pinned surfaces for no gain.

## THE TRAP: a naive detection fires on EVERY normally finished run

`chain.take_turn` returns `loop._finish(action)` WITHOUT calling `_observe`, so a `finish`
never writes an observation. "The events end in an `assistant_action` with no answering
`observation`" is therefore true of every run that ended normally — and of every conversation
follow-up leg, which is the commonest resume there is. A detection without the exclusion below
would tell each of them that its finish may not have happened.

`kind == "finish"` is excluded EXPLICITLY, and deliberately not by testing for a trailing
`finish` event: a kill between the finish action and the finish event leaves exactly the same
bare shape, and relying on the event would reintroduce the defect for precisely the runs this
feature exists to serve.

## What it does NOT claim

It reports the ABSENCE of a result, so the guards a resume rebuilds must treat it as having
executed nothing: `donothing.NON_GROUNDING_KINDS` holds this kind (a resumed leg may not pass
the fabrication guard on an action nobody knows ran) and the dead action's own kind is kept OUT
of the payload's `kind` field, so `history.seen_paths` cannot read it as a file the run has
seen. A blocking `ask_user` killed while waiting is reported too, and correctly: its question
may be on disk, and "check its effect" is cheap to act on.
"""

from __future__ import annotations

#: The observation payload kind. One string, read by the renderer below and by
#: `donothing.NON_GROUNDING_KINDS`.
KIND = "outcome_unknown"


def unanswered(events: list[dict]) -> list[dict]:
    """The TRAILING `assistant_action` events whose `observation` was never written.

    Scanned backwards and stopped at the first `observation`, so only the tail of the
    transcript is ever in question; event types that are neither (a `finish`, a `question`, a
    `header`) are passed over rather than treated as a result. Returned in transcript order.

    PER ELEMENT for a chained reply, which this shape gives for free: `chain.take_turn` lands
    and observes each element in turn, so every element has its own `assistant_action` and a
    kill can leave at most the one that was running — the elements after it were never landed
    and are named by the reply's own unexecuted note.
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

    `events` is the list boot replays from, so appending to it is what puts the note in the
    resumed leg's first prompt; writing it to the transcript is what makes a SECOND resume
    replay the same words instead of re-deriving them. Both, in one pass: the event is written
    on the dead action's own turn, directly after it, so the record reads as the answer to that
    action rather than as something the new leg did.
    """
    for ev in unanswered(events):
        turn = ev.get("turn")
        events.append(ctx.transcript.event(
            "observation",
            {"kind": KIND, "action_kind": str((ev.get("payload") or {}).get("kind") or "?")},
            turn=turn if isinstance(turn, int) else None))
