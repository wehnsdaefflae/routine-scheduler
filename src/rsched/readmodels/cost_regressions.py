"""COST REGRESSIONS — the latest `cost_trend_degraded` finding per routine, for the Stats page.

`engine/runtime._log_cost_trend` files `cost_trend_degraded` at a run's end when a routine's last
runs are clearly worse than the ones before them, and only after `readmodels/trend_attribution`
has read the same window across the fleet and said WHOSE regression it is (F568). The stream had
that finding and no reader a person clicks: the operator's decision of 2026-11-21 (#9) is to
SURFACE it — on Stats, in the Develop group, because a cost regression is how the system is
changing, not work that was due and did not happen (it is not blocked work).

One row per SUBJECT, the newest filing only: a routine re-flags at most every 12 hours
(`health_cooldown`), and the question the panel answers is "which routines regressed, and is it
theirs", not a history. The subject is the event's `routine` field as written — a slug for a
`routine` or `unattributable` verdict, and EMPTY for a `host` verdict, which carries no routine by
design (`trend_attribution.event_payload`: one condition of the box is one finding) and names the
run that read it in `detected_on`. Each row carries the attribution — `scope`, the fleet's own
numbers and the event's detail, which leads with the attribution sentence — and the slug whose
development view (`#/changes/<slug>`) holds the per-run cost profile that says what was compared.

The fold over the file is memoized on the stream's stat fingerprint (`readmodels/memo`, like
`health_stream._stamped`); the window is cut at every call, because it moves while the file
stands still.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig
from . import memo
from .health_stream import health_records, stream_path
from .stamps import instant

EVENT = "cost_trend_degraded"
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365

#: The event fields a row carries through unchanged — the attribution and the measured window.
_FIELDS = ("scope", "peers_compared", "peers_degraded", "fleet_share", "window",
           "turns_median_before", "turns_median_after", "volume_median_before",
           "volume_median_after", "fail_rate_before", "fail_rate_after")


def _latest(routines_home: Path) -> list[tuple[datetime, dict]]:
    """The newest filing per subject, newest first — the half that depends on the file alone.
    SHARED: treat the list and its records as immutable.
    """
    path = stream_path(routines_home)

    def fold() -> list[tuple[datetime, dict]]:
        newest: dict[str, tuple[datetime, dict]] = {}
        for rec in health_records(routines_home):
            if rec.get("event") != EVENT:
                continue
            when = instant(rec.get("ts"))
            subject = str(rec.get("routine") or "")
            if when is not None and (subject not in newest or when >= newest[subject][0]):
                newest[subject] = (when, rec)
        return sorted(newest.values(), key=lambda pair: pair[0], reverse=True)

    return memo.memoized_shared(f"cost-regressions:{path}", [path], fold)


def _row(rec: dict) -> dict:
    subject = str(rec.get("routine") or "")
    return {"subject": subject,
            # where the per-run cost profile lives: the routine, or the one a host row was read on
            "slug": subject or str(rec.get("detected_on") or ""),
            "ts": str(rec.get("ts") or ""), "run_id": str(rec.get("run_id") or ""),
            "detail": str(rec.get("detail") or ""),
            **{f: rec.get(f) for f in _FIELDS}}


def regressions(server: ServerConfig, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """`rows`: the latest finding per subject filed in the last `days`, newest first."""
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    since = datetime.now(UTC) - timedelta(days=days)
    rows = [_row(rec) for when, rec in _latest(server.routines_home) if when >= since]
    return {"window_days": days, "rows": rows}
