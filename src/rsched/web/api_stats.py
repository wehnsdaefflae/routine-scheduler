"""Usage statistics endpoint — time, tokens, cost rolled up across every run in the
routines and conversations homes (see rsched.stats.aggregate). Read-only; the filesystem is
the source of truth and the roll-up is recomputed per call, with every input cached only
behind a stat fingerprint of the file it comes from — so a run that just finished shows up
on the next call, and the git subprocesses behind the recipe-size trend do not run again
until a routine commits.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..readmodels.compression_stats import compression_stats
from ..readmodels.recipe_size import recipe_sizes
from ..readmodels.stats import aggregate, monthly_spend
from ..readmodels.util_stats import util_stats

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(request: Request) -> dict:
    """Full usage roll-up for the Stats tab: totals plus by_routine / by_model /
    by_endpoint / by_day / by_kind / by_state slices, the durable `monthly`
    per-routine spend series (workflow-usage stream — survives run retention), and
    `utils` — per-util execution stats (library git dates + the stream's per-run
    outcome breakdowns + a memoized transcript backfill for pre-stream history),
    `recipes` — per-routine recipe length with its ~30-day git baseline (F371), and
    `compression` — the per-routine output-compression roll-up (same durable stream:
    what the optional compressor applied, saved, and had refused).
    """
    server = request.app.state.server
    return {**aggregate(server), "monthly": monthly_spend(server),
            "utils": util_stats(server), "recipes": recipe_sizes(server),
            "compression": compression_stats(server)}
