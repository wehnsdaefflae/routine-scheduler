"""Health-events file: append-only JSONL log of key daemon/engine events for audit consumption.

Writes to <routines_home>/.control/health-events.jsonl. Each line is a JSON object:
{"ts": <iso>, "event": "run_failed"|"budget_exhausted"|"orphaned_run",
 "routine": <slug>, "run_id": <id>, "detail": <str>}

Best-effort: I/O errors are silently swallowed so logging never blocks the daemon or engine.
"""

from __future__ import annotations

import json
from pathlib import Path

from .ids import now_iso

HEALTH_EVENTS_FILE = "health-events.jsonl"
WORKFLOW_USAGE_FILE = "workflow-usage.jsonl"


def log_health_event(routines_home: Path, event: str, *, routine: str,
                     run_id: str, detail: str = "") -> None:
    """Append a health event to the JSONL log under routines_home/.control/.

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
            }) + "\n")
    except OSError:
        pass


def log_workflow_usage(routines_home: Path, *, routine: str, run_id: str,  # noqa: PLR0913 — a flat record writer: one keyword per stream field keeps the vocabulary explicit
                       workflow: str, depth: int, status: str, turns: int, tokens: int,
                       cost: float = 0.0, referrals: int = 0,
                       recipe_commit: str | None = None, utils: dict | None = None,
                       asks_deferred: int = 0) -> None:
    """Append one line per finished (sub)run to <routines_home>/.control/workflow-usage.jsonl —
    the feedback stream the meta-workflows routine mines to optimize the library, and the
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
