"""The ONE parser of the health stream (`.control/health-events.jsonl`), and the two folds
the console reads it through.

The stream had fourteen writers and no reader inside the product: the nightly audit recipe
opens the file over ssh, and nothing a person clicks in the console showed a line of it. Six
of those events say the same thing — something DUE did not happen — and they are precisely
the ones nothing else surfaces: a refused fire leaves no run, a stopped chain leaves no
member run, a capped trigger leaves a dark routine. A failure has a run page; a fire that
never happened has nothing at all, which is how F316's week of missed lane fires passed with
zero signal.

`blocked_fleet` is that fold: one row per (event, subject) with a count and the newest
detail, so "what is the fleet not doing" is answered by a fetch instead of by ssh.
`budget_endings` is the per-routine one — which of a routine's partial finishes a BUDGET
forced, the distinction the usage stream cannot carry because both land there as `partial`.

Both are memoized on the stream's stat fingerprint with single-flight misses: this rides a
bus-event path (the dashboard refetches on `run_*`), and an un-memoized 280 KB parse per
request is what starved the daemon behind `/api/items` and `/api/questions`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig
from ..health_events import HEALTH_EVENTS_FILE
from . import memo

#: The events that mean WORK THAT WAS DUE DID NOT HAPPEN, each with the one line the console
#: labels the row with. The vocabulary is `health_events.py`'s header enum — a name added
#: there that belongs in this set must be added here too, or the console stays blind to it
#: exactly the way it was blind to all six.
BLOCKED_EVENTS: dict[str, str] = {
    "fire_refused": "a due scheduled fire produced no run — the routine was still active, "
                    "or the daemon was draining",
    "lane_fire_refused": "a due lane fire armed nothing — the previous chain is still in "
                         "flight",
    "lane_chain_stopped": "a lane chain ended early, so its remaining members never ran",
    "lane_chain_member_skipped": "a chain named a member that is not a routine in any home",
    "scheduler_tick_error": "a scheduler tick raised — whatever that tick owed is late",
    "trigger_capped": "a trigger hit its daily cap: the routine is dark until the cap resets",
}

#: Partial finishes, split by whether a budget forced them (health_events.py's own
#: distinction). Read per routine, never fleet-wide: the fleet number says nothing a routine
#: page does not say better.
ENDING_EVENTS = ("budget_exhausted", "run_partial")

#: How far back a fold looks by default. A week covers every routine's own cadence (the
#: slowest live lane is weekly), which is the point: a row is evidence only against the
#: schedule that should have fired it.
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 90


def stream_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / HEALTH_EVENTS_FILE


def health_records(routines_home: Path) -> list[dict]:
    """Every event, oldest first. Missing file → []. Unparseable lines are skipped.

    The returned list is SHARED — treat it as immutable, exactly like `usage_stream`'s.
    """
    path = stream_path(routines_home)

    def parse() -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    return memo.memoized_shared(f"health-stream:{path}", [path], parse)


def _since(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _window(records: list[dict], events, days: int) -> list[dict]:
    since = _since(days)
    return [r for r in records
            if r.get("event") in events and str(r.get("ts") or "") >= since]


def blocked_fleet(server: ServerConfig, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """What the fleet did not do in the last `days`: one row per (event, subject), newest
    first, each carrying its count, its first and last stamp and the newest `detail`.

    `subject` is the event's `routine` field as written — a routine slug for a refused fire
    or a capped trigger, a LANE ID for the three lane events (opaque by contract: resolve it
    against the lane store, never by reading a prefix), and empty for a scheduler tick.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))

    def fold() -> dict:
        rows: dict[tuple[str, str], dict] = {}
        for rec in _window(health_records(server.routines_home), BLOCKED_EVENTS, days):
            event, subject = str(rec.get("event")), str(rec.get("routine") or "")
            ts = str(rec.get("ts") or "")
            row = rows.get((event, subject))
            if row is None:
                row = rows[(event, subject)] = {
                    "event": event, "means": BLOCKED_EVENTS[event], "subject": subject,
                    "count": 0, "first_ts": ts, "last_ts": ts, "detail": ""}
            row["count"] += 1
            row["first_ts"] = min(row["first_ts"], ts) if row["first_ts"] else ts
            if ts >= row["last_ts"]:
                row["last_ts"], row["detail"] = ts, str(rec.get("detail") or "")
        ordered = sorted(rows.values(), key=lambda r: (r["last_ts"], r["event"]), reverse=True)
        return {"window_days": days, "since": _since(days),
                "total": sum(r["count"] for r in ordered), "rows": ordered,
                "vocabulary": dict(BLOCKED_EVENTS)}

    path = stream_path(server.routines_home)
    return memo.memoized(f"health-blocked:{path}:{days}", [path], fold)


def budget_endings(server: ServerConfig, slug: str, *,
                   days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """How this routine's partial finishes ended in the last `days`: `budget_exhausted` is
    the count a budget forced, `run_partial` the count the model chose with budget to spare.

    The usage stream files both as `partial`, so a routine that outgrew its ceiling and one
    that keeps finding a source down read identically there. `last_detail` names which budget
    (the event carries `resource`/`limit`), which is the sentence a reader needs.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))

    def fold() -> dict:
        counts = dict.fromkeys(ENDING_EVENTS, 0)
        last_detail, last_ts = "", ""
        for rec in _window(health_records(server.routines_home), ENDING_EVENTS, days):
            if str(rec.get("routine") or "") != slug:
                continue
            counts[str(rec["event"])] += 1
            ts = str(rec.get("ts") or "")
            if ts >= last_ts:
                last_ts, last_detail = ts, str(rec.get("detail") or "")
        return {"window_days": days, **counts,
                "last_ts": last_ts, "last_detail": last_detail}

    path = stream_path(server.routines_home)
    return memo.memoized(f"health-endings:{path}:{slug}:{days}", [path], fold)
