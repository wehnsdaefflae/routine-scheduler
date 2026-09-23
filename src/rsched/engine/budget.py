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

#: The SECOND and last warning line, as a fraction of the limit. A run that passed the first
#: one and kept working gets one more notice near the ceiling — and NOTHING in between: the
#: warning used to ride every turn past `warn_at`, so a run read "converge deliberately now"
#: fifteen times and wound up at the ceiling whether or not its stopping conditions were met
#: (nanogeofeld 8 of 10 runs at 94-100 turns of 100, freelance-radar 7 of 10 at 173-200 of
#: 200, none of them forced). Budgets are a runaway BACKSTOP, never a pace.
FINAL_WARN_AT = 0.95

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
    """One resource's stop condition: a `limit` (-1 = unlimited) and the fraction
    `warn_at` at which to warn. Pure — it never holds live consumption; a `current`
    value is passed to every method.
    """

    resource: str
    limit: float = -1
    warn_at: float = 0.85

    @property
    def unlimited(self) -> bool:
        return self.limit is None or self.limit < 0

    def exceeded(self, current: float) -> bool:
        return not self.unlimited and current >= self.limit

    def warns(self, current: float) -> bool:
        return self.warn_line(current) is not None

    def warn_line(self, current: float) -> float | None:
        """The HIGHEST warning line this reading has crossed (`warn_at`, then
        FINAL_WARN_AT), or None below the first. It is the IDENTITY of the warning: a
        caller says each line once instead of repeating one for every turn above it.
        """
        if self.unlimited:
            return None
        for line in (FINAL_WARN_AT, self.warn_at):
            if current >= line * self.limit:
                return line
        return None

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

    def spent(self, meter: dict) -> dict | None:
        """The FIRST exceeded budget as `{resource, limit, message}`, or None.

        `violation` says it in words, for the model. This says it in FIELDS, for the health
        stream and status.json: a fifth of this fleet's runs end against a budget and the
        cause was recorded nowhere, so reconstructing it afterwards worked for 22 of 90 — the
        turn and wall-clock caps only, because those are the two status.json carried.
        """
        for b in self.budgets:
            if b.exceeded(meter.get(b.resource, 0)):
                return {"resource": b.resource, "limit": b.limit,
                        "message": _fmt_exhausted(b.resource, b.limit)}
        return None

    def violation(self, meter: dict) -> str | None:
        s = self.spent(meter)
        return None if s is None else str(s["message"])

    def warnings(self, meter: dict) -> list[tuple[str, str]]:
        """Every budget past a warning line, in check order, as `(line-id, text)` — e.g.
        `("turns@0.85", "~5 turns left")`.

        The id names the resource AND the line, which is what lets a caller say each
        crossing exactly once: the text moves with every turn ("~5 turns left", "~4 turns
        left"), so nothing in it can serve as the identity of a warning already given.
        """
        out: list[tuple[str, str]] = []
        for b in self.budgets:
            current = meter.get(b.resource, 0)
            line = b.warn_line(current)
            left = b.left(current)
            if line is not None and left is not None:   # a warn line implies a finite limit
                out.append((f"{b.resource}@{line:g}", _fmt_left(b.resource, left)))
        return out

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
                out.append(Budget(b.resource, float(overrides[b.resource]), b.warn_at))
            elif b.resource in CONSUMABLE and not b.unlimited:
                left = b.left(meter.get(b.resource, 0)) or 0.0
                lim = max(FLOOR.get(b.resource, 1), int(left * fraction))
                out.append(Budget(b.resource, lim, b.warn_at))
            else:
                out.append(Budget(b.resource, b.limit, b.warn_at))
        return BudgetLedger(out)
