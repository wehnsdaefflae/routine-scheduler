"""The fleet WEEK by week — how the system as a whole ran while releases kept landing.

A release is judged on the runs around the first one that ran it (change_fleet.releases), but
releases land several a day: a routine that runs daily sees ten of them inside one after-window,
and the verdict on any single one is a verdict on the stretch. This reads the stretch directly,
one ISO week at a time, on the same per-run signals (readmodels/change_signals.py):

- every routine counts ONCE per week, whatever its run count — the week's reading is not
  whichever routine happened to run most;
- a cost signal (tokens, turns, wall time — the `median` ones) is read against the routine's own
  median over its whole history, so `1.2` is "a fifth dearer than usual" for a routine of ten
  thousand tokens and for one of a million alike; the fleet's value is the median of those;
- a rate or a count (the `mean` ones) is read as it is, the fleet's value the mean of the
  routines' — they are comparable across routines already.

Each week also names the releases first run in it, the main models its runs ran on (`model_id`),
and how many of its runs were REBUILT from history (migrate_runrecords.py) or carry no quality at
all, so a reader can tell a reading from a gap. Weeks without a run are kept, empty, so the
sequence has no holes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean, median

from .change_keys import engine, version_key
from .change_signals import SIGNALS, aggregate, normalized, run_signals


def _week(rec: dict) -> date | None:
    try:
        when = datetime.fromisoformat(str(rec.get("ts") or ""))
    except ValueError:
        return None
    day = when.date()
    return day - timedelta(days=day.weekday())


def _label(monday: date) -> str:
    year, week, _day = monday.isocalendar()
    return f"{year}-W{week:02d}"


def _fleet_value(per_routine: list[float], how: str) -> float | None:
    if not per_routine:
        return None
    return round(median(per_routine) if how == "median" else mean(per_routine), 3)


def weekly(by_routine: dict[str, list[dict]], bases: dict[str, dict]) -> list[dict]:
    """`[{week, start, runs, routines, rebuilt, without_quality, releases, models, signals}]`,
    oldest first — `signals` maps every signal to the fleet's value that week (None: no
    reading). `bases` is each routine's own `change_signals.baseline`.
    """
    weeks: dict[date, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    first_run: dict[str, date] = {}
    for name, runs in by_routine.items():
        for rec in runs:
            monday = _week(rec)
            if monday is None:
                continue
            weeks[monday][name].append(rec)
            release = engine(rec)
            if release and (release not in first_run or monday < first_run[release]):
                first_run[release] = monday
    if not weeks:
        return []
    out = []
    monday, last = min(weeks), max(weeks)
    while monday <= last:
        out.append(_one_week(monday, weeks.get(monday, {}), bases, first_run))
        monday += timedelta(days=7)
    return out


def _one_week(monday: date, routines: dict[str, list[dict]], bases: dict[str, dict],
              first_run: dict[str, date]) -> dict:
    runs = [r for recs in routines.values() for r in recs]
    values = {name: [normalized(run_signals(r), bases[name]) for r in recs]
              for name, recs in routines.items()}
    signals = {sig.name: _fleet_value([v for vals in values.values()
                                       if (v := aggregate(vals, sig)) is not None], sig.how)
               for sig in SIGNALS}
    models = Counter(str(fp.get("model_id")) for r in runs
                     if (fp := r.get("fingerprint") or {}).get("model_id")
                     and "model_id" not in (fp.get("unknown") or ()))
    return {"week": _label(monday), "start": monday.isoformat(), "runs": len(runs),
            "routines": len(routines),
            "rebuilt": sum(1 for r in runs
                           if (r.get("fingerprint") or {}).get("source") == "reconstructed"),
            "without_quality": sum(1 for r in runs if not r.get("quality")),
            "releases": sorted((v for v, w in first_run.items() if w == monday), key=version_key),
            "models": dict(models.most_common()), "signals": signals}
