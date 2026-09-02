"""Health-events file: append-only JSONL log of key daemon/engine events for audit consumption.

Writes to <routines_home>/.control/health-events.jsonl. Each line is a JSON object:
{"ts": <iso>, "event": "run_failed"|"budget_exhausted"|"orphaned_run"|"run_canceled"
        |"wizard_build_degraded"|"fire_refused"|"model_window_corrected"
        |"group_chain_done"|"group_chain_stopped"|"group_chain_member_skipped"
        |"group_fire_refused"|"scheduler_tick_error",
 "routine": <slug>, "run_id": <id>, "detail": <str>}

model_window_corrected: a completion 400'd with a context-overflow whose provider-stated
maximum is SMALLER than the catalog entry's configured window — the config lies, the
run's window guard shrank its local view to the provider's figure, re-clamped and
retried (engine/completion.py, F278). detail names the entry to correct; the run
survives, but every such event is a standing config defect an audit should surface.

run_canceled: a user-requested abort killed the engine before it could write its own
finish (same payload shape as orphaned_run, which is reserved for genuine crashes).

Both of those close-out events carry two OPTIONAL structured fields the reap fills in:
`rc` (the engine process's exit status — negative means it died on that signal, so
rc=-9 is a SIGKILL) and `vm_hwm_kb` (its peak resident memory, F348). They are fields
rather than prose because the question they answer — "did anything die by signal in this
window?" — has to be answerable by a filter. It was not: the five rc=-9 deaths of
2026-09-01 were all recorded, but as `run_canceled` with the signal buried in `detail`,
so a health sweep looking for failures read the window as clean (F422, whose premise —
"rc=-9 emits no health-event" — was wrong; what it could not do was FIND them). Absent on
every other event: only a close-out has a process to report on.

fire_refused: a DUE scheduled (cron) fire produced no run — the routine was still active
from a prior run (overrun) or the daemon was draining for a self-update restart. run_id
empty (no run was created). Makes a routine that goes chronically un-fired for one of those
reasons visible to audit consumers instead of only a log.info line. Only the scheduled fire
path logs this; resume/trigger/manual overruns are expected and stay quiet. A deliberate
global PAUSE is NOT this event: it is skipped earlier, in the scheduler, and is the
operator's own intentional action — never logged as a refusal.

group_chain_done / group_chain_stopped: a sequential group chain ended (daemon/group_runs,
F316). routine = the GROUP id (grp-...), run_id = the chain record id (gr-...), detail
counts member runs and not-ok outcomes. The in-flight file is consumed at that moment, so
this event is the chain's durable record - and a scheduled group's periodic done event is
a HEARTBEAT: its absence across a schedule period means the group starved (F316's defect
class: a week of missed fires with zero signal).

group_chain_member_skipped: a chain reached a member that is missing or disabled -
routine = the member slug, detail names the group and whether the chain stopped or
continued past it.

group_fire_refused: a DUE scheduled group fire armed nothing because the previous chain
is still in flight (the group analog of fire_refused; routine = the group id, run_id
empty). One-off overlap is benign; a run of these is a wedged chain starving the group.

wizard_build_degraded: a new-routine build's stage-generation pipeline failed hard and
the routine was scaffolded from the verbatim pattern (run_id empty — builds happen in
the daemon, not a run; detail carries the failure cause, F197).

Best-effort: I/O errors are silently swallowed so logging never blocks the daemon or engine.
"""

from __future__ import annotations

import json
from pathlib import Path

from .ids import now_iso

HEALTH_EVENTS_FILE = "health-events.jsonl"
WORKFLOW_USAGE_FILE = "workflow-usage.jsonl"


def log_health_event(routines_home: Path, event: str, *, routine: str,
                     run_id: str, detail: str = "", **fields: object) -> None:
    """Append a health event to the JSONL log under routines_home/.control/.

    `fields` adds event-specific STRUCTURED keys beside the five common ones (today: `rc`
    and `vm_hwm_kb` on a close-out). A None value is dropped rather than written, so a
    caller can pass an unknown reading without minting a null the readers must handle.

    Best-effort: silently ignores I/O errors so logging never blocks the daemon or engine.
    """
    path = Path(routines_home) / ".control" / HEALTH_EVENTS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso(),
                "event": event,
                "routine": routine,
                "run_id": run_id,
                "detail": detail[:500],
                **{k: v for k, v in fields.items() if v is not None},
            }) + "\n")
    except OSError:
        pass


def log_workflow_usage(routines_home: Path, *, routine: str, run_id: str,  # noqa: PLR0913 — a flat record writer: one keyword per stream field keeps the vocabulary explicit
                       workflow: str, depth: int, status: str, turns: int, tokens: int,
                       cost: float = 0.0, referrals: int = 0,
                       recipe_commit: str | None = None, utils: dict | None = None,
                       asks_deferred: int = 0) -> None:
    """Append one line per finished (sub)run to <routines_home>/.control/workflow-usage.jsonl —
    the feedback stream the routine-improver routine mines to maintain the shared library it
    owns (its `library-pass` stage), and the
    DURABLE spend series (run dirs fall to retention; this stream survives — monthly spend
    aggregation reads it). Subruns report like any other run (depth > 0), so per-purpose
    child workflows inform pattern evolution too. Best-effort, like the health log.

    Payload extensions (never a new shape): `recipe_commit` — the recipe version that
    produced the run (health-by-recipe-version outlives retention thanks to this field);
    `utils` — the run's per-util outcome counts (RunContext.util_stats; ALWAYS present on
    new records, even empty — its presence marks the record as util-counted, which is how
    the Stats read-model knows not to double count the run from its transcript);
    `asks_deferred` — deferred-question churn.
    """
    path = Path(routines_home) / ".control" / WORKFLOW_USAGE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso(),
                "routine": routine,
                "run_id": run_id,
                "workflow": workflow or "(unknown)",
                "depth": depth,
                "status": status,
                "turns": turns,
                "tokens": tokens,
                "cost": round(cost, 6),
                "referrals": referrals,
                "recipe_commit": recipe_commit,
                "utils": utils or {},
                "asks_deferred": asks_deferred,
            }) + "\n")
    except OSError:
        pass
