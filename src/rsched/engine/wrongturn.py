"""The turn that came back WELL-FORMED AND WRONG, filed where a rate can be read (R2652).

`engine/degrade.py`'s nets fire on a turn that failed to ARRIVE; a turn that arrives whole and
is WRONG is rejected, corrected and re-driven, leaving only a transcript `error` and per-run
counters. This module files it as a health row carrying turn, model and effort, so the rate is
readable per (turn, model, effort).

THIS IS A MEASUREMENT AND NOTHING ELSE: no effort, model, retry or routing changes. A cascade
behind the trigger must be justified by the measured rate. `tests/test_wrong_turn_event.py`
holds that line (a static guard, and identical resolution with and without a trigger).

THE TRIGGERS are failures the engine ALREADY DETECTS:

* `schema_rejected` — a reply that is not a valid action (unparseable, schema-invalid, or
  refused by a per-kind, capability or grant check).
* `repeat_invalid` — a correction that taught NOTHING: the IDENTICAL invalid reply inside one
  turn, or a rejection message already earned at an earlier turn (`engine/schemamemo.recall`).
* `attempts_exhausted` — every schema attempt spent with no action accepted; the run fails.
* `action_repeat` — a schema-VALID action repeated at the streak the engine already warns at
  (`loop.REPEAT_WARN`).
* `action_cycle` and `varied_failures` — the shapes the identical streak cannot see
  (`engine/loopshape.py`), warned once per onset.

ONE ROW PER (reason, turn): a turn rejected three times is one wrong turn; per-attempt detail is
on the transcript. No COOLDOWN — a rate suppressed by the hour is not a rate, and
`health_stream.BLOCKED_EVENTS` folds count rows. The latch is read back from the stream (the
one memoized reader), since a resumed leg is a new process.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: The health event this module files. In the machine-checked `health_vocab.EVENTS` and
#: in `readmodels/health_stream.BLOCKED_EVENTS`; deliberately NOT in `REPEAT_COOLDOWN_HOURS`.
EVENT = "wrong_turn_detected"

#: The `reason` vocabulary, as the module docstring defines each; kept distinguishable.
REASONS = ("schema_rejected", "repeat_invalid", "attempts_exhausted", "action_repeat",
           "action_cycle", "varied_failures")

#: The identical-action streak at which a repeat is filed — the engine's own warn streak,
#: pinned equal to `loop.REPEAT_WARN` by `tests/test_wrong_turn_event.py`.
WARN_STREAK = 3


def _already_filed(routines_home: Path, run_id: str, reason: str, turn: int) -> bool:
    """Whether this run already has a row for this (reason, turn).

    Fails OPEN: trouble reading the stream files the event.
    """
    if not run_id:
        return False
    try:
        from ..readmodels.health_stream import health_records

        return any(rec.get("event") == EVENT and rec.get("run_id") == run_id
                   and rec.get("reason") == reason and rec.get("turn") == turn
                   for rec in reversed(health_records(Path(routines_home))))
    except Exception:
        return False


def record(ctx: RunContext, reason: str, *, turn: int, detail: str = "",
           streak: int | None = None) -> None:
    """File one `wrong_turn_detected` row for this (reason, turn) — a MEASUREMENT, never a
    behaviour change.

    `turn` is the turn the wrongness belongs to: the turn being composed for a schema failure,
    the landed turn for a repeated action. Best-effort: a measurement must never cost the turn
    it measures.
    """
    try:
        home = Path(ctx.server.routines_home)
        run_id = str(getattr(ctx, "run_id", "") or "")
        if _already_filed(home, run_id, reason, turn):
            return
        from ..health_events import log_health_event

        efforts = getattr(ctx.routine, "efforts", None) or {}
        log_health_event(
            home, EVENT,
            routine=getattr(ctx.routine, "slug", "") or "", run_id=run_id,
            detail=f"turn {turn}: {detail}" if detail else f"turn {turn}: {reason}",
            reason=reason, turn=turn, streak=streak,
            model=getattr(ctx, "main_model", "") or None,
            effort=efforts.get("main") or None)
    except Exception:
        return
