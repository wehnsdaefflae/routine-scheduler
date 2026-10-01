"""The ONE parser of the health stream (`.control/health-events.jsonl`), and the two folds
the console reads it through.

The stream had fourteen writers and no reader inside the product: the nightly audit recipe
opens the file over ssh; nothing a person clicks in the console showed a line of it.
`BLOCKED_EVENTS` are the ones that say the same thing — something DUE did not happen — and
they are precisely the ones nothing else surfaces: a refused fire leaves no run, a stopped
chain leaves no member run, a capped trigger leaves a dark routine, a commit that did not land
leaves files no history holds. A failure has a run page; a fire that never happened has
nothing at all, which is how F316's week of missed lane fires passed with zero signal.

`blocked_fleet` is that fold: one row per (event, subject) with a count and the newest
detail, so "what is the fleet not doing" is answered by a fetch instead of by ssh.
`budget_endings` is the per-routine one — which of a routine's partial finishes a BUDGET
forced, the distinction the usage stream cannot carry because both land there as `partial`.

A fold answers for a WINDOW, and a window is a question about the clock as much as about the
file. So the half that depends on the file alone — the parse, and each folded event's stamp
read as an instant — is memoized on the stream's stat fingerprint with single-flight misses:
this rides a bus-event path (the dashboard refetches on `run_*`), and an un-memoized 280 KB
parse per request is what starved the daemon behind `/api/items` and `/api/questions`. The
window itself is cut at every call, over that shared list. A fold memoized on the file alone
kept answering for the moment it was first computed until the next append, so on a quiet
instance a week-old refused fire still read as this week's.

Stamps are compared as INSTANTS (`stamps.instant`), never as strings: `ids.now_iso` writes the
host's local time with its offset, so a string compare against a UTC cutoff moved the window's
edge by the host's offset, and two stamps either side of a DST change compared in the wrong
order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig
from ..health_events import HEALTH_EVENTS_FILE
from ..paths import read_jsonl
from . import memo
from .stamps import instant

#: The events that mean WORK THAT WAS DUE DID NOT HAPPEN, each with the one line the console
#: labels the row with. The vocabulary is `health_events.py`'s header enum — a name added
#: there that belongs in this set must be added here too, or the console stays blind to it
#: exactly the way it was blind to all six.
BLOCKED_EVENTS: dict[str, str] = {
    "fire_refused": "a due scheduled fire produced no run — the routine was still active, "
                    "or the daemon was draining",
    "lane_fire_refused": "a due lane fire armed nothing — the previous chain is still in "
                         "flight",
    "lane_fire_paused": "a due lane fire was skipped because scheduling was paused — the "
                        "fire is gone, not deferred, and the next one is a full cron "
                        "interval away",
    "lane_chain_stopped": "a lane chain ended early, so its remaining members never ran",
    "lane_chain_member_skipped": "a chain named a member that is not a routine in any home",
    "scheduler_tick_error": "a scheduler tick raised — whatever that tick owed is late",
    "trigger_capped": "a trigger hit its daily cap: the routine is dark until the cap resets",
    "commit_failed": "a commit into a versioned repo did not land — the files are on disk and "
                     "out of its history until a later commit takes them",
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

    return memo.memoized_shared(f"health-stream:{path}", [path], lambda: read_jsonl(path))


def _stamped(routines_home: Path) -> list[tuple[datetime, dict]]:
    """Every event either fold reads, paired with its stamp as an instant, oldest first — the
    half of a fold that depends on the file alone, so the half memoized on its fingerprint.
    An event whose stamp names no instant cannot sit inside any window and is left out here.

    SHARED like `health_records`: treat the list and its records as immutable.
    """
    path = stream_path(routines_home)

    def pair() -> list[tuple[datetime, dict]]:
        out: list[tuple[datetime, dict]] = []
        for rec in health_records(routines_home):
            event = rec.get("event")
            if event in BLOCKED_EVENTS or event in ENDING_EVENTS:
                when = instant(rec.get("ts"))
                if when is not None:
                    out.append((when, rec))
        return out

    return memo.memoized_shared(f"health-stamped:{path}", [path], pair)


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _window(routines_home: Path, events, since: datetime) -> list[tuple[datetime, dict]]:
    """The stamped events named in `events` at or after `since` — cut at every call, because
    the window moves while the file stands still.
    """
    return [(when, rec) for when, rec in _stamped(routines_home)
            if rec.get("event") in events and when >= since]


def blocked_fleet(server: ServerConfig, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """What the fleet did not do in the last `days`: one row per (event, subject), newest
    first, each carrying its count, its first and last stamp and the newest `detail`.

    `subject` is the event's `routine` field as written — a routine slug for a refused fire
    or a capped trigger, a LANE ID for the three lane events (opaque by contract: resolve it
    against the lane store, never by reading a prefix), and empty for a scheduler tick.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    since = _since(days)
    rows: dict[tuple[str, str], dict] = {}
    span: dict[tuple[str, str], tuple[datetime, datetime]] = {}   # (first, last) instants
    for when, rec in _window(server.routines_home, BLOCKED_EVENTS, since):
        key = (str(rec.get("event")), str(rec.get("routine") or ""))
        ts, detail = str(rec.get("ts") or ""), str(rec.get("detail") or "")
        row = rows.get(key)
        if row is None:
            rows[key] = {"event": key[0], "means": BLOCKED_EVENTS[key[0]], "subject": key[1],
                         "count": 1, "first_ts": ts, "last_ts": ts, "detail": detail}
            span[key] = (when, when)
            continue
        row["count"] += 1
        first, last = span[key]
        if when < first:
            first, row["first_ts"] = when, ts
        if when >= last:
            last, row["last_ts"], row["detail"] = when, ts, detail
        span[key] = (first, last)
    ordered = [rows[k] for k in sorted(rows, key=lambda k: (span[k][1], k[0]), reverse=True)]
    return {"window_days": days, "since": since.isoformat(),
            "total": sum(r["count"] for r in ordered), "rows": ordered,
            "vocabulary": dict(BLOCKED_EVENTS)}


def budget_endings(server: ServerConfig, slug: str, *,
                   days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """How this routine's partial finishes ended in the last `days`: `budget_exhausted` is
    the count a budget forced, `run_partial` the count the model chose with budget to spare.

    The usage stream files both as `partial`, so a routine that outgrew its ceiling and one
    that keeps finding a source down read identically there. `last_detail` names which budget
    (the event carries `resource`/`limit`), which is the sentence a reader needs.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    counts = dict.fromkeys(ENDING_EVENTS, 0)
    last_detail, last_ts, newest = "", "", None
    for when, rec in _window(server.routines_home, ENDING_EVENTS, _since(days)):
        if str(rec.get("routine") or "") != slug:
            continue
        counts[str(rec["event"])] += 1
        if newest is None or when >= newest:
            newest, last_ts = when, str(rec.get("ts") or "")
            last_detail = str(rec.get("detail") or "")
    return {"window_days": days, **counts, "last_ts": last_ts, "last_detail": last_detail}
