"""Usage statistics endpoint — time, tokens, cost rolled up across every run in the
routines and conversations homes (see rsched.stats.aggregate). Read-only; the filesystem
is the source of truth, so every call reflects the live state with no cache.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..readmodels.claude_usage import claude_usage
from ..readmodels.stats import aggregate, monthly_spend
from ..readmodels.util_stats import util_stats

router = APIRouter(tags=["stats"])


@router.get("/stats/claude-usage")
def claude_usage_stats(request: Request) -> dict:
    """Local Claude-subscription usage in the rolling 5h/7d windows (D33) — the
    Settings endpoint card's widget; no official balance API exists for subscriptions.
    """
    return claude_usage(request.app.state.server)


@router.get("/stats")
def stats(request: Request) -> dict:
    """Full usage roll-up for the Stats tab: totals plus by_routine / by_model /
    by_endpoint / by_day / by_kind / by_state slices, the durable `monthly`
    per-routine spend series (workflow-usage stream — survives run retention), and
    `utils` — per-util execution stats (library git dates + the stream's per-run
    outcome breakdowns + a memoized transcript backfill for pre-stream history).
    """
    server = request.app.state.server
    return {**aggregate(server), "monthly": monthly_spend(server),
            "utils": util_stats(server)}
