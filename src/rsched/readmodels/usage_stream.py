"""The ONE parser of the durable usage stream (`.control/workflow-usage.jsonl`).

Three read-models fold this stream (stats' monthly spend, run_health's recipe buckets,
util_stats' reliability table); each used to re-read and re-parse the whole file per
request. This module parses it ONCE per change (stat-fingerprint memo) and hands every
consumer the same parsed records.

The returned list and its records are SHARED — treat them as immutable (fold, filter,
never mutate). A consumer that must annotate copies first. Unparseable lines are
skipped, exactly as every hand parser did.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..health_events import WORKFLOW_USAGE_FILE
from . import memo


def stream_path(routines_home: Path) -> Path:
    return routines_home / ".control" / WORKFLOW_USAGE_FILE


def usage_records(routines_home: Path) -> list[dict]:
    """All records, oldest first. Missing file → []."""
    path = stream_path(routines_home)

    def parse() -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    return memo.memoized_shared(f"usage-stream:{path}", [path], parse)
