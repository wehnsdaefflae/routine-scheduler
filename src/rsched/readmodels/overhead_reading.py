"""Is this routine's run time going to its task? — the PROCESS-OVERHEAD invariant, per routine.

Every finished depth-0 run records how its landed actions split between task, process and
friction (`engine/overhead.py`, carried as `quality.overhead` on its usage row and as `overhead`
in its status.json). This reads that record back as the fleet invariant: the MEDIAN
`process_share` of a routine's newest `overhead.WINDOW` measured runs, judged against
`overhead.THRESHOLD`. A median, not a mean, because one long orientation run — a first run, a
recovery after a broken tool — is not a routine's habit; and nothing is judged under
`overhead.MIN_RUNS` measured runs.

ONE SOURCE per value. The usage stream is the only thing read: a run whose row carries no
`overhead` (its transcript was never measured — older than the record and gone before the
one-shot backfill, `migrate_overhead.py`) is UNMEASURED and left out of the window, never
recomputed here from a transcript and never read as zero. Four readers ask it — the setup
surface's `recipe:overhead` row, `rsched validate` through that row, the run-end alarm
(`engine/overhead_alarm.py`) and the boot digest's FINISH block — and the surface rides
bus-event paths, so the fold is memoized on the stream's stat fingerprint (`readmodels/memo`).
"""

from __future__ import annotations

import statistics
from pathlib import Path

from ..engine import overhead
from . import memo
from .usage_stream import stream_path, usage_runs


def _share(rec: dict) -> float | None:
    q = rec.get("quality")
    o = q.get("overhead") if isinstance(q, dict) else None
    share = o.get("process_share") if isinstance(o, dict) else None
    return float(share) if isinstance(share, (int, float)) and not isinstance(share, bool) \
        else None


def _fold(routines_home: Path) -> dict[str, dict]:
    window: dict[str, list[dict]] = {}
    for rec in usage_runs(routines_home):
        if rec.get("depth") != 0 or (share := _share(rec)) is None:
            continue
        runs = window.setdefault(str(rec.get("routine") or ""), [])
        runs.append({"run_id": str(rec.get("run_id") or ""), "process_share": share})
        del runs[:-overhead.WINDOW]
    out: dict[str, dict] = {}
    for slug, runs in window.items():
        median = round(statistics.median(r["process_share"] for r in runs), 3)
        judged = len(runs) >= overhead.MIN_RUNS
        out[slug] = {"routine": slug, "median": median, "runs": runs,
                     "threshold": overhead.THRESHOLD, "judged": judged,
                     "over": judged and median >= overhead.THRESHOLD}
    return out


def readings(routines_home: Path) -> dict[str, dict]:
    """`{routine: reading}` for every routine with a measured depth-0 run — `median`, the
    `runs` it was taken over (oldest first, each `run_id` + `process_share`), `threshold`,
    `judged` (enough runs to say anything) and `over` (judged and at or above the bar).
    """
    path = stream_path(Path(routines_home))
    return memo.memoized(f"overhead-readings:{path}", [path], lambda: _fold(Path(routines_home)))


def reading(routines_home: Path, slug: str) -> dict | None:
    """One routine's reading, or None when none of its runs was measured."""
    return readings(routines_home).get(slug)


def describe(r: dict) -> str:
    """The reading in one clause — the words the surface row, the alarm and `validate` share."""
    return (f"the median of its last {len(r['runs'])} runs spent {round(100 * r['median'])}% of "
            f"their actions on its own process and harness friction (the fleet bar is "
            f"{round(100 * r['threshold'])}%)")
