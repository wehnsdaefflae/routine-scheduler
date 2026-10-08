"""How each MODEL did on a routine — its runs grouped by what served them: the main model by
catalog name, its effort, the deliberation level (a model trial's runs are their own group).

The question it answers is the operator's (2026-10-08): is this routine's model overkill, or not
enough? One routine's runs on one model say how THAT model did and nothing about another, so the
groups are only comparable once a routine has run on more than one — which is what a model TRIAL
is for (routine.yaml `trial:`, a pending change the config-optimizer proposes and a person
accepts). Read by the config-optimizer through the API and shown beside the routine's changes.

Every number is the same per-run signal the change verdicts use (readmodels/change_signals.py),
so a group's "met 0.92" and a change's "met_rate before 0.92" mean one thing.
"""

from __future__ import annotations

from pathlib import Path

from .change_effects import routine_runs
from .change_signals import SIGNALS, aggregate, run_signals

#: The signals a model's fit is read on — correctness and completeness first, then cost.
FIT_SIGNALS = ("failed", "met_rate", "unmet", "disputed", "interventions", "tokens", "turns",
               "elapsed_s")


def model_fit(routines_home: Path, slug: str) -> list[dict]:
    """`[{model, effort, deliberation, trial, runs, last, <signal>: value, …}]`, most runs first."""
    groups: dict[tuple, list[dict]] = {}
    for rec in routine_runs(routines_home).get(slug, []):
        fp = rec["fingerprint"]
        key = (str(fp.get("model") or ""), str(fp.get("effort") or ""),
               str(fp.get("deliberation") or ""), str(fp.get("trial") or ""))
        groups.setdefault(key, []).append(rec)
    sigs = {s.name: s for s in SIGNALS}
    out = []
    for (model, effort, deliberation, trial), recs in groups.items():
        values = [run_signals(r) for r in recs]
        out.append({"model": model, "effort": effort, "deliberation": deliberation,
                    "trial": trial, "runs": len(recs), "last": str(recs[-1].get("ts") or ""),
                    **{name: aggregate(values, sigs[name]) for name in FIT_SIGNALS}})
    return sorted(out, key=lambda g: (-g["runs"], g["model"]))
