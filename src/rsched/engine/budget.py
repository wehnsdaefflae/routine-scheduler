"""The unified budget primitive — ONE definition of "when to stop" and "how much is left".

A run, a conversation reply window, a subtask, and a subroutine are all budgeted the same
way. This module is the single implementation that the turn loop's stop/warn checks, the
child-task allocator, and status.json all share — instead of the per-resource `if` ladders
that grew up in run_context.py. A budget is exactly what the user framed it as: a **stop
condition** (a limit + whether tripping it is hard) over **something being consumed** (a
resource).

Split of concerns, on purpose: the LIMITS are pure (a `Budget` per resource; a
`BudgetLedger` over them); the live CONSUMPTION stays on `RunContext` and is passed in as a
plain `meter` dict `{resource: current_value}` (see `RunContext.meter`). Keeping consumption
out of the ledger is what preserves the single-writer status.json contract and never
double-counts a resume window.

Resources (each value in its limit's own unit; limit -1 = unlimited, which lifts the ceiling):
  turns        turns used in THIS budget window   (limit = max_turns)
  total_turns  turns used across ALL windows      (limit = max_total_turns — a conversation's life)
  wall_clock   MINUTES elapsed                     (limit = max_wall_clock_min)
  tokens       in+out tokens                       (limit = max_total_tokens)
  cost         real provider $ spend               (limit = max_cost)

The warning/violation wording is kept byte-identical to the pre-refactor strings (finish
summaries and the loop's BUDGET tail quote them); `_fmt_exhausted` / `_fmt_left` are the one
place that wording lives now.
"""

from __future__ import annotations

from dataclasses import dataclass

# Resources whose child allocation is a SHARE of the parent's remainder (halved by default);
# the others (a conversation-life cap, structural counts) are copied or dropped by the caller.
CONSUMABLE = ("turns", "wall_clock", "tokens", "cost")
# Per-resource floor so a nearly-spent parent still hands a child a usable slice (matches the
# pre-refactor child_budgets floors exactly).
FLOOR = {"turns": 1, "wall_clock": 1, "tokens": 1000, "cost": 1}


def _fmt_exhausted(resource: str, limit: float) -> str:
    return {
        "turns": f"turn budget exhausted ({int(limit)})",
        "total_turns": f"conversation turn budget exhausted ({int(limit)} total turns)",
        "wall_clock": f"wall-clock budget exhausted ({int(limit)} min)",
        "tokens": f"token budget exhausted ({int(limit)})",
        "cost": f"cost budget exhausted (${int(limit)})",
    }.get(resource, f"{resource} budget exhausted ({limit})")


def _fmt_left(resource: str, left: float) -> str:
    return {
        "turns": f"~{int(left)} turns left",
        "total_turns": f"~{int(left)} turns left in this conversation",
        "wall_clock": f"~{max(0, int(left))} minutes left",
        "tokens": f"~{int(left)} tokens left",
        "cost": f"~${max(0.0, round(left, 2))} of budget left",
    }.get(resource, f"~{left} {resource} left")


@dataclass(frozen=True)
class Budget:
    """One resource's stop condition: a `limit` (-1 = unlimited), whether exceeding it is
    `hard` (stops the run) vs soft (warning only), and the fraction `warn_at` at which to warn.
    Pure — it never holds live consumption; a `current` value is passed to every method.
    """

    resource: str
    limit: float = -1
    hard: bool = True
    warn_at: float = 0.85

    @property
    def unlimited(self) -> bool:
        return self.limit is None or self.limit < 0

    def exceeded(self, current: float) -> bool:
        return not self.unlimited and current >= self.limit

    def warns(self, current: float) -> bool:
        return not self.unlimited and current >= self.warn_at * self.limit

    def left(self, current: float) -> float | None:
        """Amount remaining before the limit; None when unlimited."""
        return None if self.unlimited else max(0.0, self.limit - current)


@dataclass
class BudgetLedger:
    """An ordered set of `Budget`s — the run's (or a child's / a window's) stop conditions.
    Every check takes a `meter` snapshot `{resource: current_value}`; the FIRST hard budget
    exceeded is the violation, the FIRST past its warn line is the warning (order preserved
    from construction, matching the pre-refactor check order: turns, total_turns, wall_clock,
    tokens, cost).
    """

    budgets: list[Budget]

    def violation(self, meter: dict) -> str | None:
        for b in self.budgets:
            if b.hard and b.exceeded(meter.get(b.resource, 0)):
                return _fmt_exhausted(b.resource, b.limit)
        return None

    def warning(self, meter: dict) -> str | None:
        for b in self.budgets:
            if b.warns(meter.get(b.resource, 0)):
                left = b.left(meter.get(b.resource, 0))
                if left is not None:    # warns() implies a finite limit, so left is never None
                    return _fmt_left(b.resource, left)
        return None

    def remaining(self, resource: str, meter: dict) -> float | None:
        for b in self.budgets:
            if b.resource == resource:
                return b.left(meter.get(resource, 0))
        return None

    def allocate(self, meter: dict, *, fraction: float = 0.5,
                 overrides: dict[str, float] | None = None) -> BudgetLedger:
        """A child's ledger derived from this one: each CONSUMABLE resource gets `fraction` of
        the parent's REMAINING (floored per resource); unlimited stays unlimited; every other
        resource is copied unchanged. `overrides` pins a resource to an absolute limit — a
        subtask's explicit `turns` cap sets `{"turns": n}`.
        """
        overrides = overrides or {}
        out: list[Budget] = []
        for b in self.budgets:
            if b.resource in overrides:
                out.append(Budget(b.resource, float(overrides[b.resource]), b.hard, b.warn_at))
            elif b.resource in CONSUMABLE and not b.unlimited:
                left = b.left(meter.get(b.resource, 0)) or 0.0
                lim = max(FLOOR.get(b.resource, 1), int(left * fraction))
                out.append(Budget(b.resource, lim, b.hard, b.warn_at))
            else:
                out.append(Budget(b.resource, b.limit, b.hard, b.warn_at))
        return BudgetLedger(out)
