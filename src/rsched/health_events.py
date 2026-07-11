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


def log_health_event(routines_home: Path, event: str, *, routine: str,
                     run_id: str, detail: str = "") -> None:
    """Append a health event to the JSONL log under routines_home/.control/.

    Best-effort: silently ignores I/O errors so logging never blocks the daemon or engine.
    """
    path = Path(routines_home) / ".control" / HEALTH_EVENTS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso(),
                "event": event,
                "routine": routine,
                "run_id": run_id,
                "detail": detail[:500],
            }) + "\n")
    except OSError:
        pass
