"""How each MODEL did on a routine — its runs grouped by what served them: the main model by
catalog name and by provider id (`model_id`), its effort, the deliberation level (a model
trial's runs are their own group). A run rebuilt from history knows the provider id it ran on
but rarely the catalog name, so its group shows the id and counts it `rebuilt`.

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
from .change_keys import components
from .change_signals import SIGNALS, aggregate, run_signals

#: The signals a model's fit is read on — correctness and completeness first, then cost.
FIT_SIGNALS = ("failed", "met_rate", "unmet", "disputed", "interventions", "tokens", "turns",
               "elapsed_s")


def model_fit(routines_home: Path, slug: str) -> list[dict]:
    """`[{model, model_id, effort, deliberation, trial, runs, rebuilt, last, <signal>: value,
    …}]`, most runs first. A component a rebuilt run could not recover is None (unknown) — its
    runs group apart from the runs that know it, never into the "" a real value is.
    """
    groups: dict[tuple, list[dict]] = {}
    for rec in routine_runs(routines_home).get(slug, []):
        scalars, _rules = components(rec)
        key = tuple(scalars[k] for k in ("model", "model_id", "effort", "deliberation", "trial"))
        groups.setdefault(key, []).append(rec)
    sigs = {s.name: s for s in SIGNALS}
    out = []
    for (model, model_id, effort, deliberation, trial), recs in groups.items():
        values = [run_signals(r) for r in recs]
        out.append({"model": model, "model_id": model_id, "effort": effort,
                    "deliberation": deliberation, "trial": trial, "runs": len(recs),
                    "rebuilt": sum(1 for r in recs
                                   if r["fingerprint"].get("source") == "reconstructed"),
                    "last": str(recs[-1].get("ts") or ""),
                    **{name: aggregate(values, sigs[name]) for name in FIT_SIGNALS}})
    return sorted(out, key=lambda g: (-g["runs"], str(g["model"] or g["model_id"] or "")))
