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
`MIN_RUNS` runs under the new recipe. An unversioned dir compares as `None` to `None`. The one
exception is PERSISTENCE: when the newest `MIN_RUNS` measured runs of any recipe are ALL over the
bar, the routine is over whatever its recipe history (`persistent`, 2026-11-19) — otherwise a
routine revised more often than every third run is never judged.

ONE SOURCE per value. The usage stream is the only thing read: a run whose row carries no
`overhead` (its transcript was never measured) is UNMEASURED and left out of the window, never
recomputed here from a transcript and never read as zero. Five readers ask it — the setup
surface's `recipe:overhead` row, `rsched validate` through that row, the run-end alarm
(`engine/overhead_alarm.py`), the boot digest's FINISH block and the blocked-work badge's
"does it still hold?" (`web/operator_conditions`, once per `process_overhead_high` row) — and
the surface rides bus-event paths, so the stream fold is memoized on its stat fingerprint and the
recipe version on the routine repo's HEAD and reflog (`readmodels/memo`).

The fold is the WHOLE FLEET's measured history and every reading wants one routine's slice of
it, so it is memoized SHARED (`memo.memoized_shared`): the cached value itself is handed out and
`reading` only filters it into fresh dicts, never writing into it. Through `memo.memoized` every
hit deep-copied all of it — 907 runs over 35 routines, 2.6-4.1 ms each on 2026-11-21, once per
badge row per request, under the GIL — and `/api/health/blocked`'s slow-samples caught the request
thread inside that copy. Anything that reads `_measured` through the memo must keep it read-only.
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


def _landed(rec: dict) -> int | None:
    """The run's landed-action total — its three counts summed — or None when it carries none.

    None is UNKNOWN, never zero: a row written before the counts existed must not read as a run
    that landed nothing (all 797 measured rows on this instance carry them, so this is the
    guard, not the case). A total is the denominator `process_share` was taken over, so summing
    the counts cannot disagree with the share the way a recomputation from a transcript could.
    """
    q = rec.get("quality")
    o = q.get("overhead") if isinstance(q, dict) else None
    if not isinstance(o, dict):
        return None
    counts = [o.get(k) for k in (overhead.TASK, overhead.PROCESS, overhead.FRICTION)]
    if not any(isinstance(c, int) and not isinstance(c, bool) for c in counts):
        return None
    return sum(c for c in counts if isinstance(c, int) and not isinstance(c, bool))


def _measured(routines_home: Path) -> dict[str, list[dict]]:
    """`{routine: [run, …]}` — every measured depth-0 run, oldest first, with its recipe."""
    out: dict[str, list[dict]] = {}
    for rec in usage_runs(routines_home):
        if rec.get("depth") != 0 or (share := _share(rec)) is None:
            continue
        out.setdefault(str(rec.get("routine") or ""), []).append(
            {"run_id": str(rec.get("run_id") or ""), "process_share": share,
             "recipe_commit": rec.get("recipe_commit") or None,
             "landed": _landed(rec)})
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
    recipe to say anything), `min_actions` and `too_short` (runs of this recipe set aside as too
    short to be evidence) and `over` (judged and at or above the bar) — or None when no run of
    this recipe was measured, or every one of them was too short to judge. `routine_dir`
    defaults to `<routines_home>/<slug>`.
    """
    home = Path(routines_home)
    path = stream_path(home)
    # SHARED, not copied: see the module docstring — read-only below this line
    measured = memo.memoized_shared(f"overhead-measured:{path}", [path],
                                    lambda: _measured(home))
    commit = current_recipe(routine_dir or home / slug)
    of_recipe = [r for r in measured.get(slug, []) if r["recipe_commit"] == commit]
    # A run too SHORT to be evidence is set aside, not counted as healthy and not recomputed:
    # two of its actions are process by rule, so the bar is unreachable from below (see
    # `overhead.MIN_ACTIONS`). An unknown total predates the counts and stays in the window.
    def short(r: dict) -> bool:
        return r.get("landed") is not None and int(r["landed"]) < overhead.MIN_ACTIONS

    too_short = sum(1 for r in of_recipe if short(r))
    runs = [r for r in of_recipe if not short(r)][-overhead.WINDOW:]
    # PERSISTENT across recipe changes (2026-11-19): the newest MIN_RUNS measured runs of ANY
    # recipe, every one over the bar. A recipe change resets the window, and a routine whose
    # recipe changes more often than every third run — freelance-hunter, nine recipe commits in
    # eight days — was never judged at all, whatever its runs read: a correctness revision
    # silenced the invariant exactly as an overhead cut would. A real cut breaks the streak with
    # its first run under the bar, so this never outlives the fix the reset exists to honour.
    # It needs one run of the CURRENT recipe in that streak: a recipe nothing has run yet has
    # not shown it is still over, and judging it on its predecessor's runs is the false alarm
    # the reset exists to prevent (the 11-06 sweep's repaired routines).
    latest = [r for r in measured.get(slug, []) if not short(r)][-overhead.MIN_RUNS:]
    persistent = len(latest) >= overhead.MIN_RUNS and any(
        r["recipe_commit"] == commit for r in latest) and all(
        r["process_share"] >= overhead.THRESHOLD for r in latest)
    across = persistent and len(runs) < overhead.MIN_RUNS
    if across:
        runs = latest
    if not runs:
        return None
    median = round(statistics.median(r["process_share"] for r in runs), 3)
    judged = len(runs) >= overhead.MIN_RUNS
    return {"routine": slug, "median": median, "recipe_commit": commit,
            "runs": [{"run_id": r["run_id"], "process_share": r["process_share"]} for r in runs],
            "threshold": overhead.THRESHOLD, "judged": judged,
            "min_actions": overhead.MIN_ACTIONS, "too_short": too_short,
            "persistent": persistent, "across_recipes": across,
            "over": (judged and median >= overhead.THRESHOLD) or persistent}


def describe(r: dict) -> str:
    """The reading in one clause — the words the surface row, the alarm and `validate` share."""
    if r.get("across_recipes"):
        return (f"each of its last {len(r['runs'])} runs spent at least "
                f"{round(100 * r['threshold'])}% of its actions on its own process and harness "
                f"friction (median {round(100 * r['median'])}%), across recipe changes that did "
                "not bring it under the bar")
    return (f"the median of its last {len(r['runs'])} runs under its current recipe spent "
            f"{round(100 * r['median'])}% of their actions on its own process and harness "
            f"friction (the fleet bar is {round(100 * r['threshold'])}%)")
