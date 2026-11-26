"""Serialize the root run's status in one atomic write."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..ids import now_iso
from ..paths import atomic_write_json
from . import turnwindow
from .budgets_config import _vm_hwm_kb

if TYPE_CHECKING:
    from .run_context import RunContext


def write(ctx: RunContext) -> None:
    budgets = turnwindow.budget_block(ctx)
    if ctx.finished_window is not None:
        budgets["finished_window"] = ctx.finished_window
    atomic_write_json(ctx.run_dir / "status.json", {
        "run_id": ctx.run_id,
        "pid": os.getpid(),
        "state": ctx.state,
        "outcome": ctx.outcome,
        "started": ctx.run_ts,
        "updated": now_iso(),
        "turn": ctx.turn,
        # active wall clock across ALL legs; the final write freezes the run's duration
        "elapsed_s": int(ctx.elapsed_total_s()),
        "phase": ctx.phase,
        # the PATH through the recipe: declared stages entered and never entered
        "stages": ctx.stage_coverage(),
        "question": ctx.question,
        "usage": ctx.usage_total(),
        "model": ctx.main_model,
        "deliberation": ctx.deliberation,
        "schema_retries": ctx.schema_retries,
        "schema_forcefails": ctx.schema_forcefails,
        "referrals": ctx.referrals,
        "refusal_level": ctx.refusal_level, "takeover_left": ctx.takeover_left,
        # the recipe version that produced this run (null: unversioned dir)
        "recipe_commit": ctx.recipe_commit,
        "utils": ctx.util_stats, "prompt_segments": getattr(ctx, "prompt_segments", {}),
        "asks_deferred": ctx.asks_deferred,
        "claims_challenged": ctx.claims_challenged,
        "claims_disputed": ctx.claims_disputed,
        "holds": ctx.holds,
        "interventions": ctx.interventions,
        "reports_open": ctx.reports_open,
        # the finish's own accounting (engine/accounting.py)
        "accounting": ctx.accounting,
        **({"brief": ctx.brief} if ctx.brief else {}),
        **({"goals": ctx.goals} if ctx.goals else {}),
        **({"final": ctx.final} if ctx.final is not None else {}),
        **({"outcome_measures": ctx.outcome_measures} if ctx.outcome_measures else {}),
        **({"overhead": ctx.overhead} if ctx.overhead else {}),
        # the job being worked on right now (engine/jobops.py): the live tail's "where is it"
        **({"job": ctx.job_id} if ctx.job_id else {}),
        # peak resident memory of the engine process (kB), read at close-out for an OOM
        "vm_hwm_kb": _vm_hwm_kb(),
        # THIS WINDOW's remainders (`turn` above is cumulative). The same reading renders
        # the per-turn BUDGET LEFT tail (D227), so prompt and file cannot disagree.
        "budgets": budgets,
    })
