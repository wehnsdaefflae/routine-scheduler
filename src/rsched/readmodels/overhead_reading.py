"""Is this routine's run time going to its task? — the PROCESS-OVERHEAD invariant, per routine.

Every finished depth-0 run records how its landed actions split between task, process and
friction (`engine/overhead.py`, carried as `quality.overhead` on its usage row and as `overhead`
in its status.json). This reads that record back as the fleet invariant: the MEDIAN
`process_share` of a routine's newest `overhead.WINDOW` measured runs, judged against
`overhead.THRESHOLD`. A median, not a mean, because one long orientation run — a first run, a
recovery after a broken tool — is not a routine's habit; and nothing is judged under
`overhead.MIN_RUNS` measured runs.

ONLY RUNS OF THE CURRENT RECIPE COUNT. The remedy this reading names is a recipe revision, so a
window reaching back past one would go on judging a recipe that no longer exists: on 2026-11-06
every routine's recipe was cut of its bookkeeping in one sweep, and a plain last-five window would
have told each freshly repaired routine — in its own boot digest — to propose revisions for
bookkeeping that was already gone, which is new bookkeeping. So the window holds only rows whose
`recipe_commit` (stamped at run start, `recipes.current_recipe_commit`) is the routine's current
recipe version — the newest commit touching main.md, stages/ or tuning.yaml, read without
writing (`current_recipe`) — and a recipe change RESETS it: the judgement waits for
`MIN_RUNS` runs under the new recipe. An unversioned dir compares as `None` to `None`.

ONE SOURCE per value. The usage stream is the only thing read: a run whose row carries no
`overhead` (its transcript was never measured — older than the record and gone before the
one-shot backfill, `migrate_overhead.py`) is UNMEASURED and left out of the window, never
recomputed here from a transcript and never read as zero. Four readers ask it — the setup
surface's `recipe:overhead` row, `rsched validate` through that row, the run-end alarm
(`engine/overhead_alarm.py`) and the boot digest's FINISH block — and the surface rides
bus-event paths, so the stream fold is memoized on its stat fingerprint and the recipe version
on the routine repo's HEAD and reflog (`readmodels/memo`).
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


def _measured(routines_home: Path) -> dict[str, list[dict]]:
    """`{routine: [run, …]}` — every measured depth-0 run, oldest first, with its recipe."""
    out: dict[str, list[dict]] = {}
    for rec in usage_runs(routines_home):
        if rec.get("depth") != 0 or (share := _share(rec)) is None:
            continue
        out.setdefault(str(rec.get("routine") or ""), []).append(
            {"run_id": str(rec.get("run_id") or ""), "process_share": share,
             "recipe_commit": rec.get("recipe_commit") or None})
    return out


def current_recipe(routine_dir: Path) -> str | None:
    """The routine's current recipe version — the newest commit touching its recipe files — or
    None for a dir without git history. READ-ONLY, unlike `recipes.current_recipe_commit`, which
    snapshots dirty recipe files at a run's start: a read model never commits.
    """
    from ..recipes import recipe_log

    git = Path(routine_dir) / ".git"
    if not git.is_dir():
        return None

    def newest() -> str | None:
        log = recipe_log(Path(routine_dir), limit=1)
        return str(log[0]["commit"]) if log else None

    return memo.memoized(f"overhead-recipe:{routine_dir}", [git / "HEAD", git / "logs" / "HEAD"],
                         newest)


def reading(routines_home: Path, slug: str, routine_dir: Path | None = None) -> dict | None:
    """One routine's reading over its runs of the CURRENT recipe — `median`, the `runs` it was
    taken over (oldest first), `recipe_commit`, `threshold`, `judged` (enough runs under this
    recipe to say anything) and `over` (judged and at or above the bar) — or None when no run
    of this recipe was measured. `routine_dir` defaults to `<routines_home>/<slug>`.
    """
    home = Path(routines_home)
    path = stream_path(home)
    measured = memo.memoized(f"overhead-measured:{path}", [path], lambda: _measured(home))
    commit = current_recipe(routine_dir or home / slug)
    runs = [r for r in measured.get(slug, []) if r["recipe_commit"] == commit][-overhead.WINDOW:]
    if not runs:
        return None
    median = round(statistics.median(r["process_share"] for r in runs), 3)
    judged = len(runs) >= overhead.MIN_RUNS
    return {"routine": slug, "median": median, "recipe_commit": commit,
            "runs": [{"run_id": r["run_id"], "process_share": r["process_share"]} for r in runs],
            "threshold": overhead.THRESHOLD, "judged": judged,
            "over": judged and median >= overhead.THRESHOLD}


def describe(r: dict) -> str:
    """The reading in one clause — the words the surface row, the alarm and `validate` share."""
    return (f"the median of its last {len(r['runs'])} runs under its current recipe spent "
            f"{round(100 * r['median'])}% of their actions on its own process and harness "
            f"friction (the fleet bar is {round(100 * r['threshold'])}%)")
