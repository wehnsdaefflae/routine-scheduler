"""The engine's side of the hook mirror: a reminder written, and an injection delivered.

`rsched/hooks/store.py` owns the stream (D152). This module is only the seam that gives it a
RUN's context — routines home, routine and run — which the pure stores do not have.

Every function is best-effort, returns nothing, and is called AFTER the real work it records:
a mirror must never fail the write or delivery itself.
"""

from __future__ import annotations

from ..hooks import store as hookstore


def reminder_written(loop, reminder, target: str = "") -> None:
    """Mirror a consequence reminder as a hook (`trigger: match`).

    Called from the routine's own add/revise (`remind._persist`) and a hook set on ANOTHER
    routine (`remind._set_for_other`); `target` becomes the addressee (`self` or the slug).
    A revise re-records: the stream is append-only, so a later row is the later fact.
    """
    ctx = loop.ctx
    try:
        hookstore.record_reminder(
            ctx.server.routines_home, record=reminder.as_record(),
            routine=ctx.routine.slug, run_id=ctx.run_id, target=target)
    except (AttributeError, OSError, TypeError, ValueError):
        return


def reminder_removed(loop, reminder) -> None:
    """Record a routine DELETING ITS OWN reminder as a later row about that id
    (`disposition: remove`) — the half of the write path that was missing.

    Without it, a fold of the stream would re-raise a reminder its own routine removed — and a
    reminder decides whether an action is HELD. Addressed `self`, like the write it undoes
    (the slug would make it indistinguishable from a sibling's hook); the projection subtracts
    on `remove` (`reminderproj.REMOVING`).
    """
    ctx = loop.ctx
    try:
        hookstore.record_reminder(
            ctx.server.routines_home, record=reminder.as_record(),
            routine=ctx.routine.slug, run_id=ctx.run_id, disposition="remove")
    except (AttributeError, OSError, TypeError, ValueError):
        return


def reminder_disposed(loop, reminder, disposition: str) -> None:
    """Record the TARGET's say over a foreign hook as a later row about the same id (D152 step
    4): `keep`, `mute` or `remove`.

    The same reminder hook with its disposition moved — append-only, so "when was this muted,
    and by whose run" stays answerable after a `mute` stamp expires.
    """
    ctx = loop.ctx
    try:
        hookstore.record_reminder(
            ctx.server.routines_home, record=reminder.as_record(),
            routine=ctx.routine.slug, run_id=ctx.run_id, target=ctx.routine.slug,
            disposition=str(disposition or "keep"))
    except (AttributeError, OSError, TypeError, ValueError):
        return


def reminder_curated(loop, record: dict, holder: str, disposition: str) -> None:
    """Record the CURATOR's disposal of a row in ANOTHER routine's store (D231): `dead` for a
    row retired with its tally kept, `remove` for one deleted outright.

    Addressed to the HOLDER, not `self`, so a fold by addressee finds the disposal beside the
    hook; the origin names the curating routine and run (who retired it, and when).
    """
    ctx = loop.ctx
    try:
        hookstore.record_reminder(
            ctx.server.routines_home, record=record, routine=ctx.routine.slug,
            run_id=ctx.run_id, target=holder, disposition=str(disposition or "dead"))
    except (AttributeError, OSError, TypeError, ValueError):
        return


def injection_delivered(loop, m: dict) -> None:
    """Mirror a mid-run injection as a hook (`trigger: send`, addressee `run:<run_id>`).

    Called from `control.inject_user_message` once the message is in the conversation, so the
    row records a delivery, not an intent. Every channel rides this seam; `via` says which.
    """
    ctx = loop.ctx
    try:
        hookstore.record_injection(
            ctx.server.routines_home, run_id=ctx.run_id, turn=int(getattr(ctx, "turn", 0) or 0),
            via=str(m.get("via") or ""), text=str(m.get("text") or ""),
            routine=ctx.routine.slug)
    except (AttributeError, OSError, TypeError, ValueError):
        return
