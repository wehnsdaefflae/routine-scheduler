"""A run that ended NOT-OK, was resumed, and finished `ok` — the correction every reader of
the health stream resolves its earlier endings to (F637 + F703).

A run killed by three rejections of the same correctable mistake, then resumed to `ok`, left
two surfaces permanently disagreeing about whether it failed. The run's OWN record
self-corrects: the resumed leg reuses the run dir and the run_id (`daemon/runner.resume`), so
status.json, result.md and `readmodels/usage_stream`'s leg fold — which keeps the NEWEST leg's
status — all read `ok` the moment the second leg ends. The HEALTH STREAM does not and cannot:
it is append-only by design, and it had no vocabulary for "that ending was superseded". Its
`run_failed` / `budget_exhausted` / `attempts_exhausted` rows therefore say the run failed
forever, to the blocked-work fold, to the routine page's endings split and to the nightly audit
recipe that reads the file over ssh.

A CORRECTION SUPERSEDES, IT DOES NOT ERASE. The original row stays exactly as written — the
ending really happened, and an append-only audit trail that edits itself is not one. What this
module adds is a LATER event, `run_corrected`, carrying `superseded` (the event names it
resolves) so a reader can fold the pair without re-deriving the rule.

WHAT IS CORRECTABLE, and the line this draws deliberately:

* The ENDINGS that claim the run did not finish — `run_failed`, `run_canceled`, `orphaned_run`,
  `run_partial`, and `budget_exhausted` whose own `status` is not `ok`. A later `ok` finish of
  the SAME run contradicts each of them directly.
* `wrong_turn_detected` with `reason="attempts_exhausted"` ALONE: that is the row meaning "the
  turn produced nothing at all and the run ended" (`engine/wrongturn.py`), which a resume
  refutes. The other three reasons are NOT corrected — `schema_rejected`, `repeat_invalid` and
  `action_repeat` measure turns that were paid for at full prompt price and produced nothing,
  and a resume does not give that spend back. They are the corpus that event exists to
  accumulate; folding them away on a later success would make the fleet's wrong-turn rate read
  as the rate of turns nobody eventually recovered from, which is a different number.
* `finish_gate_failed` is NOT corrected, also deliberately: it does not claim the run failed,
  it claims the CHECKS did not judge that ending. A later checked ending does not make the
  earlier unchecked one checked.

A `budget_exhausted` row whose `status` is already `ok` is not an ending claim at all (the
reserved turn was spent and the run still finished) — it is left alone for the same reason.

IDEMPOTENT ACROSS REPEATED RESUMES. `_pending` walks the run's rows backwards and stops at the
run's own last correction, so a run resumed twice files a second row only for endings that
happened after the first — and a leg that fails AGAIN after a correction is not retroactively
excused by it (the readers compare stamps, never a flat "this run was corrected once" flag).

Best-effort throughout, like every other writer on this stream: a correction that raises must
never cost the finish it is describing.
"""

from __future__ import annotations

from pathlib import Path

#: The health event this module files. In `health_events.py`'s machine-checked header enum;
#: deliberately NOT in `health_cooldown.REPEAT_COOLDOWN_HOURS` (a suppressed correction leaves
#: the original ending standing on every surface) and NOT in
#: `readmodels/health_stream.BLOCKED_EVENTS` (a corrected run did its work — it is the opposite
#: of work that was due and did not happen).
EVENT = "run_corrected"

#: The endings a later `ok` finish of the same run contradicts outright.
CORRECTED_ENDINGS = ("run_failed", "run_canceled", "orphaned_run", "run_partial",
                     "budget_exhausted")

#: `wrong_turn_detected`'s one correctable reason — the turn that produced nothing and ended
#: the run. The module docstring says why the other three stay.
CORRECTED_TURN_REASON = "attempts_exhausted"


def is_correctable(rec: dict) -> bool:
    """Whether this health row CLAIMS the run did not finish — the only kind a correction
    supersedes. Everything else on the stream is left exactly as written.
    """
    event = rec.get("event")
    if event == "budget_exhausted":
        return str(rec.get("status") or "partial") != "ok"
    if event == "wrong_turn_detected":
        return rec.get("reason") == CORRECTED_TURN_REASON
    return event in CORRECTED_ENDINGS


def _label(rec: dict) -> str:
    """One ending, as the correction's detail names it: the event, plus the field that says
    which flavour of it (`attempts_exhausted`, `turns`) where the row carries one.
    """
    event = str(rec.get("event") or "")
    qualifier = rec.get("reason") or rec.get("resource")
    return f"{event} ({qualifier})" if qualifier else event


def _pending(records: list[dict], run_id: str) -> tuple[list[str], list[str]]:
    """The endings of `run_id` that no correction has superseded yet — (event names, labels),
    oldest first. Walks backwards and stops at this run's own last correction, so a second
    resume corrects only what happened after the first.
    """
    names: list[str] = []
    labels: list[str] = []
    for rec in reversed(records):
        if str(rec.get("run_id") or "") != run_id:
            continue
        if rec.get("event") == EVENT:
            break
        if not is_correctable(rec):
            continue
        name = str(rec.get("event") or "")
        if name not in names:
            names.insert(0, name)
        labels.insert(0, _label(rec))
    return names, labels


def record(routines_home: Path | str, *, routine: str, run_id: str, summary: str = "") -> None:
    """File one `run_corrected` row when this run has earlier endings to supersede.

    Called at the finish of a run that ended `ok`. Files NOTHING when the run has no not-ok
    row behind it, which is the overwhelmingly common case — an ordinary successful run must
    not put a line on this stream.
    """
    try:
        home = Path(routines_home)
        if not run_id:
            return
        from .readmodels.health_stream import health_records

        names, labels = _pending(health_records(home), str(run_id))
        if not names:
            return
        from .health_events import log_health_event

        detail = "resumed and finished ok, superseding " + ", ".join(labels)
        log_health_event(home, EVENT, routine=routine, run_id=str(run_id),
                         detail=f"{detail}. {summary}" if summary else detail,
                         superseded=names)
    except Exception:
        return


def lane_note(stopped_by: str) -> str:
    """What a corrected member adds to a lane chain's not-ok close.

    The chain is NOT reopened and the row is NOT folded away: its remaining members really did
    not run, which is what `lane_chain_stopped` is on the blocked-work fold to say. What the
    row must stop claiming is that the member FAILED, once that member's own run was resumed
    and finished ok.
    """
    return (f" — {stopped_by} was resumed and finished ok, so this close's not-ok verdict is "
            "superseded; the chain's remaining members still did not run")
