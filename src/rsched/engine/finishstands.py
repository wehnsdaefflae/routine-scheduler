"""The finish gate's own safety net: a guard that RAISES must not cost the run its summary.

F658. `finishgate.check_finish` is a set of GUARDS over an ending the run has already authored.
An exception inside it is the mechanism failing, not a verdict on the ending — so it must not
propagate out of the engine loop and leave the run with no finish, summary or result.md.

The classic cause is BUILD SKEW: a release landing mid-run, so a lazily imported module from disk
meets objects the boot build made. Two defences: the finish path is PINNED at run start (`pin`),
and a skew that happens anyway is NAMED in the event (`engine/buildskew.py`). This module is the
floor beneath both: the summary is the only part of a run that survives it, and no guard, import
or refactor gets to throw it away.

THE RULE: on an unexpected exception the run FINISHES on its own authored status and summary, and
the failure becomes its own `finish_gate_failed` health event — distinct from a gate that REJECTED
(which defers) and from an engine crash. `RunAborted` and `KeyboardInterrupt`/`SystemExit` are NOT
caught: those are the engine being stopped on purpose.

`finish_gate_failed` is an INCIDENT, so it is deliberately absent from
`health_events.REPEAT_COOLDOWN_HOURS` (two broken finishes are two facts), and it is in
`readmodels/health_stream.BLOCKED_EVENTS`: the run reads as finished everywhere, and what did not
happen — the judging — no run page can show.
"""

from __future__ import annotations

import traceback
from types import ModuleType

from ..health_events import log_health_event
from . import buildskew
from .control import RunAborted

#: The health event this guard files. Deliberately not in `REPEAT_COOLDOWN_HOURS`.
EVENT = "finish_gate_failed"

#: The finish path, resolved ONCE (`pin`) and read from here by every finish after it.
_PINNED: dict[str, ModuleType] = {}


def pin() -> ModuleType:
    """Resolve the finish path NOW and keep it — the engine's defence against a release that
    lands while a run is in flight (F677).

    The lazy import resolves `finishgate` and its whole subtree FRESH FROM DISK when first run;
    left until the finish turn, that may be after a release. Called at run START
    (`loopsetup.configure`) so caller and held objects come from one build. Objects created
    later from a newer module remain possible — `engine/buildskew.py` names those.

    Idempotent, and safe to skip: an unpinned caller resolves the gate on first use.
    """
    if "gate" not in _PINNED:
        from . import finishgate
        _PINNED["gate"] = finishgate
    return _PINNED["gate"]


def check_finish_or_stand(loop, action: dict, ctx) -> str | None:
    """`finishgate.check_finish`, but a raising guard ends the run instead of killing it.

    Returns what the gate returns — a status when the finish STANDS, `None` when a guard set it
    aside for another turn — and on an unexpected exception returns the status of a finish
    carried through on the model's own summary.
    """
    # Lazy because a module-scope import of `finishgate` makes an import cycle at boot. Safe:
    # the call signature is stable, a stale call is caught below, and `pin` makes it the gate
    # this process BOOTED with.
    gate = pin()
    try:
        return gate.check_finish(loop, action, ctx)
    except (RunAborted, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # the guard failed, not the finish
        detail = traceback.format_exc()
        # name a mid-run release as the cause where knowable, not a bare AttributeError
        skew = buildskew.from_error(exc) if isinstance(exc, AttributeError) else ""
        ctx.transcript.event("error", {
            "where": "finishgate",
            "error": detail[-2000:],
            "stood": True,
            **({"build_skew": skew} if skew else {}),
            "note": ("the finish gate raised; the run finished on its own authored summary "
                     "(F658) — the guards did not run, so nothing was checked"
                     + (f". {skew}" if skew else "")),
        })
        tail = f" — the finish STOOD on its authored summary{'. ' + skew if skew else ''}"
        log_health_event(ctx.server.routines_home, EVENT,
                         routine=ctx.routine.slug, run_id=ctx.run_id,
                         detail=f"{_one_line(detail, max(500 - len(tail), 80))}{tail}",
                         status=str(action.get("status") or ""),
                         build_skew=skew or None)
        final = action.get("final")
        return loop._finish_run(action["status"], action["summary"], authored=True,
                                reply_to=action.get("reply_to"),
                                final=final if isinstance(final, bool) else None)


def _one_line(formatted: str, limit: int = 500) -> str:
    """The exception line plus the innermost frame — what a reader of the health stream needs to
    recognise the failure without opening the transcript.

    `limit` is how much of the 500-char health `detail` this part may spend, so a build-skew
    diagnosis riding along is not truncated away.
    """
    lines = [ln.strip() for ln in formatted.strip().splitlines() if ln.strip()]
    if not lines:
        return "the finish gate raised"
    frames = [ln for ln in lines if ln.startswith("File ")]
    where = f" at {frames[-1]}" if frames else ""
    return f"{lines[-1]}{where}"[:limit]
