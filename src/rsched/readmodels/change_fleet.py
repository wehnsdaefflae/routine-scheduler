"""Changes that reach MANY routines at once — an engine release, a rule's revision, a model
switch — judged on the runs of every routine they reached, POOLED.

One routine's runs around such a change are few. Releases land several a day and the library's
rules are revised often, so a routine that runs daily meets its next change before it has run
three times on this one, and its own verdict is `too few runs` — on this instance, rebuilt back
to July, for 96% of all routine changes. The same change reached every holder at once, though,
so the holders' windows are POOLED: each routine adds the runs on either side of its boundary
that carry its own key unchanged (at most `WINDOW` a side), its cost signals divided by its own
median (change_signals.normalized) so a routine of a million tokens and one of ten thousand
weigh alike, and the pool is judged like one routine's windows — `MIN_RUNS` a side, on the
POOLED signals.

What a pooled verdict rests on is said beside it:

- a RELEASE — the `routines` that met it while nothing else of theirs changed at that run (one
  whose recipe, config or rules changed at the same run is counted `confounded` and left out),
  and `smeared`: the median number of OTHER releases inside their windows, counting those a
  routine skipped between two of its runs — 0 judges this release alone, 7 a stretch of eight,
  which the weekly timeline (change_timeline.py) reads directly;
- a rule revision or a model switch — every routine it reached with that routine's own verdict,
  and `alone`: at how many of those runs it was the ONLY change.

A row carries its verdict, its dimensions and the two numbers a reader scans for (the cost ratio,
the change in lines met) — not every signal: the fleet has hundreds of rows, and a routine's own
change list is where a single change is read signal by signal.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import pairwise
from statistics import median

from .change_keys import diff, engine, engines, reconstructed, segments, version_key
from .change_signals import POOLED, judge, normalized, overall, run_signals

WINDOW = 5
MIN_RUNS = 3
#: The components a fleet row is kept for: those that mean the same thing in every routine (a
#: recipe or a config hash is one routine's own).
FLEET_KINDS = ("rule", "model", "model_id", "effort", "deliberation")
#: The components one catalog-model switch moves together: the name, the provider id behind it,
#: its effort. A change of the three at one run is ONE change for `alone`, or no model row could
#: ever be judged on its own.
_MODEL_FAMILY = frozenset({"model", "model_id", "effort"})


def _family(w: dict) -> tuple:
    return ("model",) if w["kind"] in _MODEL_FAMILY else (w["kind"], w["name"])


class _Pool:
    def __init__(self) -> None:
        self.before: list[dict] = []
        self.after: list[dict] = []
        self.runs: list[dict] = []
        self.boundaries = 0

    def add(self, before: list[dict], after: list[dict], base: dict) -> None:
        self.boundaries += 1
        self.before += [normalized(run_signals(r), base) for r in before]
        self.after += [normalized(run_signals(r), base) for r in after]
        self.runs += [*before, *after]

    def evaluate(self) -> dict:
        judged = judge(self.before, self.after, POOLED)
        enough = len(self.before) >= MIN_RUNS and len(self.after) >= MIN_RUNS
        sig = {s["name"]: s for s in judged["signals"]}
        tokens, met = sig["tokens"], sig["met_rate"]
        return {"verdict": overall(judged["dimensions"]) if enough else "too few runs",
                "runs_before": len(self.before), "runs_after": len(self.after),
                "dimensions": judged["dimensions"], "reconstructed": reconstructed(self.runs),
                "tokens_ratio": (round(tokens["after"] / tokens["before"], 2)
                                 if tokens["before"] and tokens["after"] is not None else None),
                "met_rate_delta": (round(met["after"] - met["before"], 2)
                                   if met["before"] is not None and met["after"] is not None
                                   else None)}


def releases(by_name: dict[str, list[dict]], bases: dict[str, dict]) -> list[dict]:
    """Every release a measured run first ran, newest first, judged on the pool (module
    docstring).
    """
    pools: dict[str, _Pool] = defaultdict(_Pool)
    reached: dict[str, list[str]] = defaultdict(list)
    smear: dict[str, list[int]] = defaultdict(list)
    confounded: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    order = {v: version_key(v) for v in engines([r for runs in by_name.values() for r in runs])}
    for name, runs in by_name.items():
        seg_of = [i for i, (_key, seg) in enumerate(segments(runs)) for _run in seg]
        for i in range(1, len(runs)):
            old, new = engine(runs[i - 1]), engine(runs[i])
            if not old or not new or old == new:
                continue
            first_seen[new] = min(first_seen.get(new, "~"), str(runs[i].get("ts") or ""))
            if seg_of[i] != seg_of[i - 1]:
                confounded[new] += 1
                continue
            lo, hi = i, i
            while lo > 0 and seg_of[lo - 1] == seg_of[i] and i - lo < WINDOW:
                lo -= 1
            while hi < len(runs) and seg_of[hi] == seg_of[i] and hi - i < WINDOW:
                hi += 1
            pools[new].add(runs[lo:i], runs[i:hi], bases[name])
            reached[new].append(name)
            # a routine that skipped releases met them all at this run: 0.380 → 0.385 also
            # carries 0.381-0.384, which no run of its own ever ran
            skipped = {v for v, k in order.items() if order[old] < k < order[new]}
            smear[new].append(len((skipped | set(engines(runs[lo:hi]))) - {old, new}))
    return [{"release": release, "first_seen": first_seen[release],
             "routines": sorted(set(reached[release])), "confounded": confounded[release],
             "smeared": int(median(smear[release])) if smear[release] else 0,
             **pools[release].evaluate()}
            for release in sorted(first_seen, key=version_key, reverse=True)]


def fleet(by_name: dict[str, list[dict]], bases: dict[str, dict],
          verdicts: dict[tuple[str, str], str]) -> list[dict]:
    """Model and rule changes rolled up across the routines that met them: one row per
    (kind, name, from → to), newest first, with every routine's own verdict (`verdicts`, by
    routine and the run that began the change).

    The row's verdict is pooled over the routines where this was the ONLY change at that run
    (`alone` of them) — the only runs that can say what THIS change did. A library migration
    that rewrites thirty rules at once otherwise hands each of them the whole batch's verdict.
    Where it also changed together with other things, `together` is the pool over every routine
    it reached: what the batch did, labelled as the batch.
    """
    rows: dict[tuple, dict] = {}
    alone: dict[tuple, _Pool] = defaultdict(_Pool)
    together: dict[tuple, _Pool] = defaultdict(_Pool)
    for name, runs in by_name.items():
        segs = segments(runs)
        for (old_key, old_runs), (new_key, new_runs) in pairwise(segs):
            what = diff(old_key, new_key)
            at, run_id = str(new_runs[0].get("ts") or ""), str(new_runs[0].get("run_id") or "")
            for w in what:
                if w["kind"] not in FLEET_KINDS:
                    continue
                key = (w["kind"], w["name"], w["from"], w["to"])
                row = rows.setdefault(key, {**w, "at": at, "routines": {}, "alone": 0})
                row["routines"][name] = verdicts.get((name, run_id), "")
                row["at"] = min(row["at"], at)
                window = (old_runs[-WINDOW:], new_runs[:WINDOW], bases[name])
                together[key].add(*window)
                if {_family(x) for x in what} == {_family(w)}:
                    row["alone"] += 1
                    alone[key].add(*window)
    return sorted(({**row, **alone[key].evaluate(),
                    "together": (together[key].evaluate()
                                 if alone[key].boundaries < together[key].boundaries else None)}
                   for key, row in rows.items()),
                  key=lambda r: r["at"], reverse=True)
