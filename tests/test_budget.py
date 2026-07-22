"""The unified budget primitive (engine/budget.py): Budget + BudgetLedger + allocate.

The RunContext integration (budget_violation/warning/tokens_remaining/child_budgets) is
exercised end-to-end in test_loop.py; here we pin the primitive itself and the exact wording
the loop's BUDGET tail and finish summaries quote."""

from rsched.engine.budget import Budget, BudgetLedger


def test_budget_limit_semantics():
    b = Budget("turns", limit=10)
    assert not b.unlimited
    assert not b.exceeded(9) and b.exceeded(10) and b.exceeded(11)
    assert not b.warns(8) and b.warns(9)          # warn at 0.85 * 10 = 8.5
    assert b.left(3) == 7 and b.left(20) == 0     # never negative


def test_unlimited_never_trips():
    b = Budget("tokens", limit=-1)
    assert b.unlimited
    assert not b.exceeded(10**9)
    assert not b.warns(10**9)
    assert b.left(10**9) is None


def _run_ledger():
    return BudgetLedger([
        Budget("turns", 60),
        Budget("total_turns", -1),
        Budget("wall_clock", 45),
        Budget("tokens", 1000),
        Budget("cost", 5),
    ])


def test_violation_is_first_hard_budget_exceeded_in_order():
    led = _run_ledger()
    # turns checked before tokens: a turns overflow wins even though tokens also overflow
    v = led.violation({"turns": 60, "tokens": 5000, "cost": 0})
    assert v == "turn budget exhausted (60)"
    # only tokens over
    assert led.violation({"turns": 1, "tokens": 1000, "cost": 0}) == "token budget exhausted (1000)"
    # cost wording carries the dollar sign
    assert led.violation({"turns": 1, "tokens": 1, "cost": 5}) == "cost budget exhausted ($5)"
    # nothing over
    assert led.violation({"turns": 1, "tokens": 1, "cost": 0}) is None


def test_warning_wording_per_resource():
    led = _run_ledger()
    assert led.warning({"turns": 55}) == "~5 turns left"                 # 55 >= 0.85*60
    assert led.warning({"wall_clock": 40}) == "~5 minutes left"          # 40 >= 0.85*45
    assert led.warning({"cost": 4.5}) == "~$0.5 of budget left"          # 4.5 >= 0.85*5
    # total_turns has its own phrasing (shows turns LEFT: 40 - 34 = 6)
    led2 = BudgetLedger([Budget("total_turns", 40)])
    assert led2.warning({"total_turns": 34}) == "~6 turns left in this conversation"


def test_remaining():
    led = _run_ledger()
    assert led.remaining("tokens", {"tokens": 250}) == 750
    assert led.remaining("total_turns", {"total_turns": 999}) is None   # unlimited
    assert led.remaining("nope", {}) is None


def test_allocate_halves_consumables_floors_and_keeps_unlimited():
    led = BudgetLedger([
        Budget("turns", 60), Budget("total_turns", -1), Budget("wall_clock", 45),
        Budget("tokens", 10000), Budget("cost", 5),
    ])
    meter = {"turns": 20, "wall_clock": 5, "tokens": 2000, "cost": 1}
    child = led.allocate(meter, fraction=0.5)
    lim = {b.resource: b.limit for b in child.budgets}
    assert lim["turns"] == 20       # (60-20)//2
    assert lim["wall_clock"] == 20  # (45-5)//2
    assert lim["tokens"] == 4000    # (10000-2000)//2, above the 1000 floor
    assert lim["cost"] == 2         # (5-1)//2
    assert lim["total_turns"] == -1  # unlimited copied, never allocated


def test_allocate_floors_a_nearly_spent_parent():
    led = BudgetLedger([Budget("turns", 3), Budget("tokens", 100)])
    child = led.allocate({"turns": 3, "tokens": 100})   # nothing left
    lim = {b.resource: b.limit for b in child.budgets}
    assert lim["turns"] == 1        # floor 1, never 0
    assert lim["tokens"] == 1000    # floor 1000


def test_allocate_override_pins_absolute_limit():
    led = _run_ledger()
    child = led.allocate({"turns": 0}, overrides={"turns": 8})
    lim = {b.resource: b.limit for b in child.budgets}
    assert lim["turns"] == 8   # a subtask's explicit turn cap
