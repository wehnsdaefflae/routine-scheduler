"""F758: what a COST BUDGET meters — notional dollars, not only a provider's reported figure.

`budgets.max_cost` was metered off `usage["cost"]`, which exactly ONE adapter ever writes
(`endpoints/openai_compat.py`, OpenRouter's credit accounting). No `anthropic` endpoint reports
a cost at all, so a ceiling set on a routine whose model resolves there bounded NOTHING — and
when this was found 17 of 30 enabled routines inherited such an endpoint through `system_model`
(self-audit F758). The ceiling is now metered off `usage["notional"]`: the dollar figure
`endpoints/prices.py` stamps on EVERY completion whatever transport carried it, and which IS the
provider's own reported figure wherever one is reported (`prices.price_usage` takes a reported
`cost` at its word before it prices anything).

So a cost cap is a cap on NOTIONAL dollars. On a metered route that is real spend; on a
subscription route it is what those tokens would have cost bought at list price — which is the
only number that exists there, and a ceiling that bounds something beats one that bounds nothing.
"""

from __future__ import annotations

import time

from rsched.engine.budgets_config import Budgets
from rsched.engine.run_context import RunContext


def _ctx(usage: dict, *, max_cost: int) -> RunContext:
    """A RunContext carrying just enough state for the budget checks (as test_loop.py does)."""
    ctx = RunContext.__new__(RunContext)
    ctx._budget_warned = set()
    ctx.budgets = Budgets(max_turns=100, max_wall_clock_min=-1, max_total_tokens=-1,
                          max_subruns=4, max_subrun_depth=2, ask_timeout_min=5,
                          max_cost=max_cost)
    ctx.usage = {"in": 0, "out": 0, **usage}
    ctx.turn = 1
    ctx.budget_base_turn = 0
    ctx._started_mono = time.monotonic()
    ctx._suspended_s = 0.0
    return ctx


def test_a_cost_cap_trips_on_an_endpoint_that_reports_no_cost():
    """THE F758 DEFECT: an anthropic route reports no `cost`, only `notional`. A $5 cap has to
    stop a run that has burned $6 there — it used to bound nothing at all.
    """
    spent = _ctx({"notional": 6.0}, max_cost=5).budget_spent()
    assert spent is not None, "a $5 cost cap did not trip at $6 of notional spend"
    assert spent["resource"] == "cost"
    assert spent["message"] == "cost budget exhausted ($5)"


def test_the_warning_lines_fire_on_notional_spend_too():
    """The 85% line is the run's cue to wind down deliberately; metered off a figure the
    transport never writes, it never arrived either.
    """
    ctx = _ctx({"notional": 4.5}, max_cost=5)
    assert ctx.budget_spent() is None                      # under the cap
    assert "of budget left" in (ctx.budget_warning() or "")


def test_a_reported_provider_cost_is_still_metered():
    """A metered adapter (OpenRouter) writes `cost`, and `prices.price_usage` stamps the SAME
    number as `notional`, so both keys agree. A reading carrying only `cost` — one that never
    passed the pricing seam — stays metered rather than silently free.
    """
    assert _ctx({"cost": 6.0}, max_cost=5).budget_spent() is not None
    both = _ctx({"cost": 6.0, "notional": 6.0}, max_cost=5).budget_spent()
    assert both is not None and both["resource"] == "cost"


def test_unlimited_is_still_unlimited_on_notional_spend():
    ctx = _ctx({"notional": 999.0}, max_cost=-1)
    assert ctx.budget_spent() is None and ctx.budget_warning() is None


def test_a_child_is_allocated_half_the_remaining_notional_dollars():
    """A child's ceiling is derived from the parent's REMAINDER, so a cost-blind parent handed
    out a slice computed from spend it never saw: $5 cap, $3 spent, child gets $1 — not $2.
    """
    child = _ctx({"notional": 3.0}, max_cost=5).child_budgets()
    assert child.max_cost == 1        # max(floor 1, (5 - 3) // 2)


def test_dollars_spent_prefers_notional_and_falls_back_to_a_reported_cost():
    """The one function that says what a dollar budget meters (endpoints/usage.py)."""
    from rsched.endpoints.usage import dollars_spent

    assert dollars_spent({"notional": 2.5}) == 2.5
    assert dollars_spent({"cost": 1.25}) == 1.25              # billed, never priced
    assert dollars_spent({"cost": 1.25, "notional": 1.25}) == 1.25
    assert dollars_spent({"in": 10, "out": 5}) == 0.0         # unpriced and unbilled
    assert dollars_spent({}) == 0.0
    assert dollars_spent(None) == 0.0
