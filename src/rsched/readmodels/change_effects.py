"""Did a change help? — every change to a routine FOUND from its own runs and JUDGED by them.

Nothing declares a change. Every depth-0 run's usage record carries a fingerprint
(engine/runrecord.py) and the recipe commit; a CHANGE is a boundary between consecutive runs of a
routine whose BEHAVIOUR KEY differs — the recipe, the behaviour-relevant config, the main model,
its effort, the deliberation level, a held rule's text, a model trial. The runs just before it
and the runs just after it (at most `WINDOW` each, each side only runs of ONE key) are compared
signal by signal (readmodels/change_signals.py) on correctness, completeness and effectiveness,
and the change gets one verdict:

- `improved` / `regressed` — some dimension moved one way and none the other;
- `mixed` — both ways (cheaper but less complete, say): a person weighs it;
- `no effect` — nothing moved past its noise threshold;
- `measuring` — the newest change, its after-window still filling;
- `too few runs` — another change came before either side reached `MIN_RUNS`. That is a finding
  too: a routine whose recipe changes every run cannot be told to have improved.

ENGINE RELEASES are not part of a routine's key: several land a day, and as a key they would cut
every routine's history into windows too short to judge. They are judged across the FLEET
instead (`releases`): for every routine whose behaviour key held still on both sides of the run
that first ran a release, its runs before against its runs after, pooled by release. A release
that lands inside a routine change's windows is named beside it (`engines`) — the confounder a
reader must see. Model and rule changes are rolled up across routines the same way (`fleet`).

Flag-first, like the recipe-health regression flag: nothing here reverts or proposes anything.
The config-optimizer reads it to propose a pending change; a person reads it on the Changes page.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from . import memo
from .change_signals import judge, run_signals
from .usage_stream import stream_path, usage_runs

WINDOW = 5
MIN_RUNS = 3
_KEYS = ("recipe", "config", "model", "effort", "deliberation", "trial")
RELEASES_SHOWN = 12


def components(rec: dict) -> dict[str, str]:
    """The behaviour key of one run, flat: its own components plus `rule:<slug>` per held rule."""
    fp = rec.get("fingerprint") or {}
    out = {"recipe": str(rec.get("recipe_commit") or ""),
           **{k: str(fp.get(k) or "") for k in _KEYS[1:]}}
    out.update({f"rule:{slug}": str(h) for slug, h in (fp.get("rules") or {}).items()})
    return out


def diff(old: dict[str, str], new: dict[str, str]) -> list[dict]:
    out = []
    for key in sorted({*old, *new}):
        if old.get(key, "") != new.get(key, ""):
            kind, _, name = key.partition(":")
            out.append({"kind": kind, "name": name, "from": old.get(key, ""),
                        "to": new.get(key, "")})
    return out


def overall(dimensions: dict[str, str]) -> str:
    moved = set(dimensions.values()) - {"same"}
    if not moved:
        return "no effect"
    if moved == {"better"}:
        return "improved"
    if moved == {"worse"}:
        return "regressed"
    return "mixed"


def routine_runs(routines_home: Path) -> dict[str, list[dict]]:
    """Fingerprinted depth-0 runs per ROUTINE (not a conversation or a background task),
    oldest first — the order the stream wrote them in.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for rec in usage_runs(routines_home):
        slug = str(rec.get("routine") or "")
        if rec.get("depth") == 0 and rec.get("fingerprint") and slug \
                and (routines_home / slug / "routine.yaml").is_file():
            out[slug].append(rec)
    return out


def _segments(runs: list[dict]) -> list[list[dict]]:
    segs: list[list[dict]] = []
    for rec in runs:
        if segs and components(segs[-1][0]) == components(rec):
            segs[-1].append(rec)
        else:
            segs.append([rec])
    return segs


def _evaluate(before: list[dict], after: list[dict], *, current: bool) -> dict:
    judged = judge([run_signals(r) for r in before], [run_signals(r) for r in after])
    if len(before) >= MIN_RUNS and len(after) >= MIN_RUNS:
        verdict = overall(judged["dimensions"])
    else:
        verdict = "measuring" if current and len(before) >= MIN_RUNS else "too few runs"
    return {"verdict": verdict, "runs_before": len(before), "runs_after": len(after), **judged}


def _engines(runs: list[dict]) -> list[str]:
    return sorted({str((r.get("fingerprint") or {}).get("engine") or "") for r in runs} - {""},
                  key=_version)


def _version(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def _changes_of(runs: list[dict]) -> list[dict]:
    segs = _segments(runs)
    out = []
    for i in range(1, len(segs)):
        before, after = segs[i - 1][-WINDOW:], segs[i][:WINDOW]
        out.append({"at": str(segs[i][0].get("ts") or ""),
                    "run_id": str(segs[i][0].get("run_id") or ""),
                    "what": diff(components(segs[i - 1][0]), components(segs[i][0])),
                    "engines": _engines([*before, *after]),
                    **_evaluate(before, after, current=i == len(segs) - 1)})
    return out[::-1]                                           # newest first


def _releases(by_routine: dict[str, list[dict]]) -> list[dict]:
    """Per engine release: each routine whose behaviour key held still around the run that
    first ran it, judged on its own runs before against after; pooled by release.
    """
    pooled: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    first_seen: dict[str, str] = {}
    for slug, runs in by_routine.items():
        for i in range(1, len(runs)):
            old = str(runs[i - 1]["fingerprint"].get("engine") or "")
            new = str(runs[i]["fingerprint"].get("engine") or "")
            if not new or new == old:
                continue
            first_seen[new] = min(first_seen.get(new, "~"), str(runs[i].get("ts") or ""))
            before, after = runs[max(0, i - WINDOW):i], runs[i:i + WINDOW]
            key = components(runs[i])
            if all(components(r) == key for r in [*before, *after]):
                pooled[new].append((slug, _evaluate(before, after, current=False)))
    out = []
    for release in sorted(pooled, key=_version, reverse=True)[:RELEASES_SHOWN]:
        judged = [ev for _slug, ev in pooled[release] if ev["verdict"] != "too few runs"]
        out.append({"release": release, "first_seen": first_seen.get(release, ""),
                    "routines": [slug for slug, _ev in pooled[release]],
                    "verdicts": dict(Counter(ev["verdict"] for _s, ev in pooled[release])),
                    "tokens_ratio": _median_ratio(judged, "tokens"),
                    "met_rate_delta": _median_delta(judged, "met_rate")})
    return out


def _signal(ev: dict, name: str) -> dict:
    return next(s for s in ev["signals"] if s["name"] == name)


def _median_ratio(evs: list[dict], name: str) -> float | None:
    ratios = [s["after"] / s["before"] for ev in evs
              if (s := _signal(ev, name))["before"] and s["after"] is not None]
    return round(median(ratios), 2) if ratios else None


def _median_delta(evs: list[dict], name: str) -> float | None:
    deltas = [s["after"] - s["before"] for ev in evs
              if (s := _signal(ev, name))["before"] is not None and s["after"] is not None]
    return round(median(deltas), 2) if deltas else None


def _fleet(changes: dict[str, list[dict]]) -> list[dict]:
    """Model and rule changes rolled up across the routines that met them: one row per
    (kind, name, from → to), with every routine's verdict.
    """
    rows: dict[tuple, dict] = {}
    for slug, routine_changes in changes.items():
        for ch in routine_changes:
            for w in ch["what"]:
                if w["kind"] not in ("rule", "model", "effort"):
                    continue
                key = (w["kind"], w["name"], w["from"], w["to"])
                row = rows.setdefault(key, {**w, "at": ch["at"], "routines": {}})
                row["routines"][slug] = ch["verdict"]
                row["at"] = min(row["at"], ch["at"])
    return sorted(rows.values(), key=lambda r: r["at"], reverse=True)


def changes(routines_home: Path) -> dict:
    """The whole read model: per-routine changes (newest first), engine releases and the
    fleet roll-up. Memoized on the usage stream.
    """
    path = stream_path(routines_home)

    def compute() -> dict:
        by_routine = routine_runs(routines_home)
        per = {slug: _changes_of(runs) for slug, runs in by_routine.items()}
        return {"routines": per, "releases": _releases(by_routine), "fleet": _fleet(per),
                "measured_runs": sum(len(r) for r in by_routine.values())}

    return memo.memoized_shared(f"change-effects:{path}", [path], compute)


def summary(routines_home: Path) -> list[dict]:
    """One line per routine that has a measured change: how many, the newest verdict."""
    per = changes(routines_home)["routines"]
    return sorted(({"routine": slug, "changes": len(chs), "latest": chs[0]["verdict"],
                    "at": chs[0]["at"]} for slug, chs in per.items() if chs),
                  key=lambda r: r["at"], reverse=True)
