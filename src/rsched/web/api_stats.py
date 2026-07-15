"""Usage statistics endpoint — time, tokens, cost rolled up across every run in the
routines and conversations homes (see rsched.stats.aggregate). Read-only; the filesystem
is the source of truth, so every call reflects the live state with no cache.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..stats import aggregate, monthly_spend

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(request: Request) -> dict:
    """Full usage roll-up for the Stats tab: totals plus by_routine / by_model /
    by_endpoint / by_day / by_kind / by_state slices, and the durable `monthly`
    per-routine spend series (workflow-usage stream — survives run retention).
    """
    server = request.app.state.server
    return {**aggregate(server), "monthly": monthly_spend(server)}
