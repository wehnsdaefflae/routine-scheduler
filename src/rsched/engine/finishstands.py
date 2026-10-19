"""The finish gate's own safety net: a guard that RAISES must not cost the run its summary.

F658. `finishgate.check_finish` is a set of GUARDS over an ending the run has already authored.
An exception inside it is not a verdict on that ending — it is the mechanism failing — and until
this module existed it propagated out of the engine loop and took the whole run with it.

WHAT IT COST, once, measurably. On 2026-10-09 run scheduler-builder:20261009-120002 had already
shipped two gated releases when its finish died with
`AttributeError: module 'rsched.engine.verifier' has no attribute 'unentered'`. The engine exited
rc=1 with NO finish, NO summary and NO result.md; the run reads `orphaned_run` on every surface
to this day. The cause was a stale LAZY import: the engine process held `finishgate`'s bytecode
from its boot, the lazy `from . import verifier` resolved fresh from disk at the finish, and the
0.401.0 caller met the 0.403.0 module the run itself had just shipped. The release gate cannot
see this — it runs a fresh interpreter, where caller and module always agree.

Eager-importing the finish path (the companion half of F658) removes that INSTANCE. This module
removes the CLASS: the summary is the only part of a run that survives it, and no guard, no
import and no future refactor gets to throw it away. The two are deliberately separate — one is
a fix, this is a floor.

F677 added the other half of the same lesson. Eager-importing the GATE's own imports left this
module's one lazy import — and a lazy import of `finishgate` pulls the gate's whole subtree from
disk, so the finish turn was still the moment a mid-run release first met the run's held objects.
Three runs on 2026-10-12 (07:34-08:26) loaded a `jobops` from 0.416.0 at their finish and asked it
about a `GrantPolicy` their boot build had made: `AttributeError: … has no attribute 'jobs_on'`,
three times, the instance's first `finish_gate_failed` rows. So the path is now PINNED at run
start (`pin`, called from `loopsetup.configure`) and a skew that happens anyway is NAMED in the
event (`engine/buildskew.py`) instead of reaching the operator as a bare AttributeError.

THE RULE: on an unexpected exception the run FINISHES on its own authored status and summary, and
the failure becomes its own `finish_gate_failed` health event, distinguishable on every surface
from a gate that ran and REJECTED (that defers, and says so in the transcript) and from the
engine crash it used to be. `RunAborted` and `KeyboardInterrupt`/`SystemExit` are NOT caught:
those are the engine being stopped on purpose, not a guard failing.

`finish_gate_failed` is an INCIDENT, not a standing condition, so it is deliberately absent from
`health_events.REPEAT_COOLDOWN_HOURS`: two broken finishes are two facts, and the second must
never be suppressed as a repeat of the first.

WHERE IT IS READ (F678): it is in `readmodels/health_stream.BLOCKED_EVENTS`, so the console's
blocked-work surface renders it. It belongs there for a reason the other events make obvious by
contrast — a refused fire leaves no run to look at, and this leaves a run that reads as finished
on every surface, because it DID finish. What did not happen is the judging, which no run page
can show.
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

    The one lazy import this module keeps (the reason is in `check_finish_or_stand`) resolves
    `finishgate` — and with it the whole subtree the gate imports — FRESH FROM DISK the moment
    it first runs. Left until the finish turn, that moment is hours after boot on a long run,
    and on a run that ships releases it is after its own release: on 2026-10-12 three runs
    loaded a `jobops` from 0.416.0 at their finish and asked it about a `GrantPolicy` object
    their boot build had made, and all three finish gates died with AttributeError.

    Called at run START (`loopsetup.configure`), while the build on disk is still the build this
    process booted with, so caller and held objects come from one release. It cannot close the
    window for objects a run creates LATER from a newer module — nothing can, short of
    restarting the process — which is why `engine/buildskew.py` is the other half.

    Idempotent, and safe to skip: a caller that never pinned resolves the gate the old way.
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
    # The one lazy import F658 does NOT move to module scope, and the reason is the finding's
    # own: `loop` imports this module eagerly and `finishgate` imports `assist`, which reaches
    # back through the engine's seams — importing it here at module scope makes a cycle at boot.
    # It is SAFE where the gate's own were not: this module is a 3-line pass-through with no
    # symbol of `finishgate`'s in its signature, so an old caller meeting a new gate calls the
    # same `check_finish(loop, action, ctx)` it always did — and if that call is itself stale,
    # catching it is precisely this module's job. Resolved through `pin` so that, on a run that
    # pinned at its start, the gate is the one this process BOOTED with rather than whatever a
    # release left on disk since (F677).
    gate = pin()
    try:
        return gate.check_finish(loop, action, ctx)
    except (RunAborted, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # the guard failed, not the finish
        detail = traceback.format_exc()
        # WHY it failed, where that is knowable: a held object from an older build meeting
        # newer code is a release that landed mid-run, not an engine bug, and the three rows of
        # 2026-10-12 said only `AttributeError: … has no attribute 'jobs_on'` — which sent
        # nobody to the release that caused them (F677).
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

    `limit` is how much of the health `detail` this part may spend. It matters because
    `log_health_event` truncates `detail` at 500 characters: when a build-skew diagnosis rides
    along, the exception must not eat the budget and leave the reader with the symptom and none
    of the cause (F677).
    """
    lines = [ln.strip() for ln in formatted.strip().splitlines() if ln.strip()]
    if not lines:
        return "the finish gate raised"
    frames = [ln for ln in lines if ln.startswith("File ")]
    where = f" at {frames[-1]}" if frames else ""
    return f"{lines[-1]}{where}"[:limit]
