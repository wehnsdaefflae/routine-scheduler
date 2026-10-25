"""The turn that came back WELL-FORMED AND WRONG, filed where a rate can be read (R2652).

`engine/degrade.py` is documented as "when a turn comes back WRONG" and carries four ordered
nets — two window shrinks, a media fallback, a chain advance. Every one of them fires on a turn
that failed to ARRIVE: a transport error, an empty completion, a classifier decline. A turn that
arrives whole and is WRONG is, to that ladder, a success. The engine rejects it, corrects the
model, re-drives the same turn, and the only trace is a transcript `error` event plus two
counters (`RunContext.schema_retries`, `schema_forcefails`) that reach status.json and the
workflow-usage record — per run, never as a fleet rate, and never beside the turn it happened on.

So the question the next increment has to answer first — how often does a turn come back wrong,
at which turn of a run, under which model and effort — is answerable on this instance nowhere.
That is what this module adds, and all it adds.

THIS IS A MEASUREMENT AND NOTHING ELSE. No effort is changed, no model is swapped, no retry is
added, no routing layer appears. The engine behaves on a wrong turn exactly as it did before:
the same rejection, the same correction, the same attempt budget. R2652's first increment is the
TRIGGER alone, deliberately, because the trigger was the missing piece and the cascade behind it
has to be justified by a measured rate rather than by a plausible story. The accumulated rows are
also the `(turn, effort-that-sufficed)` corpus token-lab's R1668 says this instance lacks.
`tests/test_wrong_turn_event.py` holds that line two ways: a static guard that this module
resolves nothing and mutates nothing, and a run-level assertion that the resolved model and
effort are identical across a run that trips a trigger and one that does not.

THE FOUR TRIGGERS are the visible failures the engine ALREADY DETECTS — nothing here is a new
judgement about a turn:

* `schema_rejected` — a reply that is not a valid action: unparseable, schema-invalid, or
  refused by a per-kind, capability or grant check (`completion.action_candidate`). The
  commonest one, and the one that recovers: the turn usually lands on the next attempt, which
  is exactly why nothing recorded it.
* `repeat_invalid` — a correction that taught NOTHING, in either of the two ways it can:
  the model returned the IDENTICAL invalid reply inside ONE turn
  (`completion.next_action`'s `repeated`), or it earned a rejection MESSAGE this run had
  already earned at an EARLIER turn (`engine/schemamemo.recall`, F736 — which holds the
  measurement, and is also what stops that retry being the identical ask). The second half is
  why this reason reads nonzero at all: `repeated` compares against a per-turn local, so a
  rejection re-earned fifty turns later was invisible to it. Its own reason rather than a
  second rejection, because a correction that teaches nothing is the signature the later
  increment is built for.
* `attempts_exhausted` — every schema attempt spent with no action accepted, so the turn that
  was due produced nothing at all and the run ends `failed`.
* `action_repeat` — a perfectly schema-VALID action that is the same one again, at the streak
  where the engine already warns (`loop.REPEAT_WARN`; `loop.REPEAT_FAIL` ends the run). Wrong
  in the way token-lab's blind spot is wrong — chained state, not disguise: each action is
  individually well-formed and the run is no longer moving.

ONE ROW PER (reason, turn). A single turn can be rejected three times, and three rows would make
the stream say three wrong turns where there was one — `health_cooldown`'s docstring records what
per-occurrence filing did to this stream once (one resumed run wrote 26 of 1,289 rows for a
condition 80 runs had produced). The corpus is keyed on the TURN, so the turn is the unit; the
per-attempt record already exists on the transcript's `error` events. A COOLDOWN would be the
wrong instrument and is deliberately absent: a rate suppressed by the hour is not a rate, and
nothing in `health_stream.BLOCKED_EVENTS` may be cooled down at all, since that fold counts rows.

The latch is read back out of the stream rather than held in memory — `shellwrite.already_filed`'s
idiom, for its reason: a writer inside one turn has no long-lived object to latch on, and a
resumed leg is a new process. The parse is the stream's ONE memoized reader, so a turn rejected
three times costs one read.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: The health event this module files. In `health_events.py`'s machine-checked header enum and
#: in `readmodels/health_stream.BLOCKED_EVENTS`; deliberately NOT in `REPEAT_COOLDOWN_HOURS`.
EVENT = "wrong_turn_detected"

#: The `reason` vocabulary, as the module docstring defines each. A filter asks "how many turns
#: came back wrong, and which way" — so the four stay distinguishable rather than folded into a
#: count that cannot tell a recovered rejection from a run the engine had to end.
REASONS = ("schema_rejected", "repeat_invalid", "attempts_exhausted", "action_repeat")

#: The identical-action streak at which a repeat is filed — the streak the engine ALREADY warns
#: the run about, never a new threshold of this module's own. Pinned equal to `loop.REPEAT_WARN`
#: by `tests/test_wrong_turn_event.py`, because a second copy of a constant is how two readings
#: of "the run is stuck" drift apart.
WARN_STREAK = 3


def _already_filed(routines_home: Path, run_id: str, reason: str, turn: int) -> bool:
    """Whether this run already has a row for this (reason, turn).

    Fails OPEN: trouble reading the stream files the event. A suppressor that cannot read its own
    history must not become the thing that silences the log.
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

    `turn` is the turn the wrongness belongs to: the turn being composed for a schema failure
    (`ctx.turn` counts turns that LANDED, so a rejection on the way to turn N is N), the landed
    turn for a repeated action. `model` and `effort` ride the row because the corpus this event
    accumulates is per (turn, model, effort) — a rate without them cannot say what sufficed.

    Best-effort twice over: `log_health_event` swallows its own I/O errors, and anything else
    that goes wrong here is swallowed too. A measurement must never cost the turn it measures.
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
