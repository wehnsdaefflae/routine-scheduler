"""The engine's side of the hook mirror: a reminder written, and an injection delivered.

`rsched/hooks/store.py` owns the stream and says why the duplication is deliberate at this
step of the D152 campaign. This module is only the seam that gives it a RUN's context — the
routines home it writes under, the routine and run that caused the row — which the pure stores
(`rsched/reminders`, the inbox) do not have and should not acquire.

Both functions are best-effort and return nothing: the reminder write and the injection
delivery are the real work, already done when these are called, and a mirror that could fail
either of them would be a defect dressed as bookkeeping. They are called AFTER the thing they
record, for the same reason.
"""

from __future__ import annotations

from ..hooks import store as hookstore


def reminder_written(loop, reminder, target: str = "") -> None:
    """Mirror a consequence reminder as a hook (`trigger: match`).

    Called from the two paths that write one: the routine's own add/revise (`remind._persist`)
    and a hook set on ANOTHER routine (`remind._set_for_other`). `target` distinguishes them,
    and the store turns it into the addressee — `self` for a routine's own future runs, the
    routine's slug for a foreign hook.

    A revise re-records rather than editing: the stream is append-only, so a later row about
    the same id is the later fact about it. That is what makes "when did this pattern change"
    answerable at all, which neither the local file nor the library store can say.
    """
    ctx = loop.ctx
    try:
        hookstore.record_reminder(
            ctx.server.routines_home, reminder_id=reminder.id, routine=ctx.routine.slug,
            run_id=ctx.run_id, regex=reminder.regex, description=reminder.description,
            kind=reminder.kind, scope=reminder.scope, target=target,
            disposition=getattr(reminder, "disposition", "keep") or "keep")
    except (AttributeError, OSError, TypeError, ValueError):
        return


def injection_delivered(loop, m: dict) -> None:
    """Mirror a mid-run injection as a hook (`trigger: send`, addressee `run:<run_id>`).

    Called from `control.inject_user_message` once the message is actually in the conversation,
    so the row records a delivery rather than an intent — the web layer's `inject` route only
    queues a file, and whether it reached a live run or waits for the next one is decided here.

    Every channel rides this seam (`inbox.VIAS`): the operator typing in the run view, an
    escalation rung's directive, a report being injected, a background result. `via` is what
    says which, and it is the one thing a reader needs that the text does not carry.
    """
    ctx = loop.ctx
    try:
        hookstore.record_injection(
            ctx.server.routines_home, run_id=ctx.run_id, turn=int(getattr(ctx, "turn", 0) or 0),
            via=str(m.get("via") or ""), text=str(m.get("text") or ""),
            routine=ctx.routine.slug)
    except (AttributeError, OSError, TypeError, ValueError):
        return
