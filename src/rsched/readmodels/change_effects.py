"""Did a change help? — every change to a routine FOUND from its own runs and JUDGED by them.

Nothing declares a change. Every depth-0 run's usage record carries a fingerprint
(engine/runrecord.py) and the recipe commit; a CHANGE is a boundary between consecutive runs of a
routine whose BEHAVIOUR KEY differs (readmodels/change_keys.py) — the recipe, the
behaviour-relevant config, the main model, its effort, the deliberation level, a held rule's
text, a model trial. The runs just before it and the runs just after it (at most `WINDOW` each,
each side only runs of ONE key) are compared signal by signal (readmodels/change_signals.py) on
correctness, completeness and effectiveness, and the change gets one verdict:

- `improved` / `regressed` — some dimension moved one way and none the other;
- `mixed` — both ways (cheaper but less complete, say): a person weighs it;
- `no effect` — nothing moved past its noise threshold;
- `measuring` — the newest change, its after-window still filling;
- `too few runs` — another change came before either side reached `MIN_RUNS`. That is a finding
  too: a routine whose recipe changes every run cannot be told to have improved.

What reached many routines at once — an ENGINE RELEASE (never part of a routine's key: several
land a day), a rule's revision, a model switch — is judged across the fleet as well, its holders'
windows POOLED (readmodels/change_fleet.py), and the weekly timeline (change_timeline.py) reads
the fleet over a stretch of releases. A release that lands inside a routine change's windows is
named beside it (`engines`) — the confounder a reader must see.

The history reaches back past the record: runs from before fingerprints existed were REBUILT
from git (migrate_runrecords.py). What could not be recovered is UNKNOWN, and an unknown
component is a wildcard — it never makes a change, and a change's `what` names only components
known on both sides; a verdict drawn on rebuilt runs carries `reconstructed`. A slug that was
archived and created again names two routines, so runs are grouped by INCARNATION
(readmodels/incarnations.py): the per-routine changes are the live routines', the fleet readings
take the archived ones too.

Flag-first, like the recipe-health regression flag: nothing here reverts or proposes anything.
The config-optimizer reads it to propose a pending change; a person reads it on the Changes page.
"""

from __future__ import annotations

from pathlib import Path

from . import memo
from .change_fleet import MIN_RUNS, WINDOW, fleet, releases
from .change_keys import diff, engines, reconstructed, segments
from .change_signals import baseline, judge, overall, run_signals
from .change_timeline import weekly
from .incarnations import ARCHIVE, Incarnation, archived_dirs, incarnation_of, incarnations
from .usage_stream import stream_path, usage_runs


def incarnation_runs(routines_home: Path) -> dict[str, tuple[Incarnation, list[dict]]]:
    """Fingerprinted depth-0 runs per routine INCARNATION (readmodels/incarnations.py), keyed by
    its name — a live routine's slug, an archived one's dir name — oldest first, the order the
    stream wrote them in. A conversation or a background task belongs to no routine.
    """
    archived = archived_dirs(routines_home)
    incs: dict[str, list[Incarnation]] = {}
    out: dict[str, tuple[Incarnation, list[dict]]] = {}
    for rec in usage_runs(routines_home):
        slug = str(rec.get("routine") or "")
        if rec.get("depth") != 0 or not rec.get("fingerprint") or not slug:
            continue
        if slug not in incs:
            incs[slug] = incarnations(routines_home, slug, archived)
        inc = incarnation_of(incs[slug], str(rec.get("run_id") or "").rsplit(":", 1)[-1])
        if inc is not None:
            out.setdefault(inc.name, (inc, []))[1].append(rec)
    return out


def routine_runs(routines_home: Path) -> dict[str, list[dict]]:
    """The LIVE routines' runs — an archived routine's runs under the same slug are another
    routine's.
    """
    return {name: runs for name, (inc, runs) in incarnation_runs(routines_home).items()
            if inc.live}


def _evaluate(before: list[dict], after: list[dict], *, current: bool) -> dict:
    judged = judge([run_signals(r) for r in before], [run_signals(r) for r in after])
    if len(before) >= MIN_RUNS and len(after) >= MIN_RUNS:
        verdict = overall(judged["dimensions"])
    else:
        verdict = "measuring" if current and len(before) >= MIN_RUNS else "too few runs"
    return {"verdict": verdict, "runs_before": len(before), "runs_after": len(after),
            "reconstructed": reconstructed([*before, *after]), **judged}


def _changes_of(runs: list[dict]) -> list[dict]:
    segs = segments(runs)
    out = []
    for i in range(1, len(segs)):
        (old_key, old_runs), (new_key, new_runs) = segs[i - 1], segs[i]
        before, after = old_runs[-WINDOW:], new_runs[:WINDOW]
        out.append({"at": str(new_runs[0].get("ts") or ""),
                    "run_id": str(new_runs[0].get("run_id") or ""),
                    "what": diff(old_key, new_key),
                    "engines": engines([*before, *after]),
                    **_evaluate(before, after, current=i == len(segs) - 1)})
    return out[::-1]                                           # newest first


def changes(routines_home: Path) -> dict:
    """The whole read model: per-routine changes (newest first) of the LIVE routines, and the
    readings across the fleet — engine releases, the model and rule roll-up, the weekly
    timeline — over every incarnation, the archived ones (`archived`) included: a routine that
    is gone still ran on those releases. Memoized on the usage stream and on the two dirs whose
    entries say which routines exist.
    """
    path = stream_path(routines_home)

    def compute() -> dict:
        everything = incarnation_runs(routines_home)
        by_name = {name: runs for name, (_inc, runs) in everything.items()}
        bases = {name: baseline(runs) for name, runs in by_name.items()}
        per = {name: _changes_of(runs) for name, runs in by_name.items()}
        verdicts = {(name, ch["run_id"]): ch["verdict"] for name, chs in per.items()
                    for ch in chs}
        live = {name for name, (inc, _runs) in everything.items() if inc.live}
        return {"routines": {name: chs for name, chs in per.items() if name in live},
                "releases": releases(by_name, bases), "fleet": fleet(by_name, bases, verdicts),
                "timeline": weekly(by_name, bases), "archived": sorted(set(by_name) - live),
                "measured_runs": sum(len(r) for r in by_name.values())}

    return memo.memoized_shared(f"change-effects:{path}",
                                [path, routines_home, routines_home / ARCHIVE], compute)


def routine_timeline(routines_home: Path, slug: str) -> list[dict]:
    """One live routine's own weeks — the timeline's reading with the routine as its fleet."""
    runs = routine_runs(routines_home).get(slug, [])
    return weekly({slug: runs}, {slug: baseline(runs)}) if runs else []


def summary(routines_home: Path) -> list[dict]:
    """One line per routine that has a measured change: how many, the newest verdict."""
    per = changes(routines_home)["routines"]
    return sorted(({"routine": slug, "changes": len(chs), "latest": chs[0]["verdict"],
                    "at": chs[0]["at"]} for slug, chs in per.items() if chs),
                  key=lambda r: r["at"], reverse=True)
