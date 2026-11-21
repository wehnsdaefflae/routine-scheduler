"""The unified budget primitive — ONE definition of "when to stop" and "how much is left".

A run, a conversation reply window, a subtask and a subroutine are all budgeted the same way;
the turn loop's stop/warn checks, the child-task allocator and status.json share this one
implementation. A budget is a **stop condition** (a limit) over a **resource** being consumed.

The LIMITS are pure (a `Budget` per resource; a `BudgetLedger` over them); the live CONSUMPTION
stays on `RunContext` and is passed in as a `meter` dict `{resource: current_value}`
(`RunContext.meter`), which keeps status.json single-writer and never double-counts a resume
window.

Resources (each value in its limit's own unit; limit -1 = unlimited, which lifts the ceiling):
  turns        turns used in THIS budget window   (limit = max_turns)
  total_turns  turns used across ALL windows      (limit = max_total_turns — the RUN-SPANNING
                                                   cap: every resume leg of a run, every reply
                                                   of a conversation. Defaults to 3x max_turns,
                                                   engine/turnwindow)
  wall_clock   MINUTES elapsed                     (limit = max_wall_clock_min)
  tokens       in+out tokens                       (limit = max_total_tokens)
  cost         NOTIONAL $ spend (F758)             (limit = max_cost)

`cost` is metered off `endpoints/usage.dollars_spent` (the notional figure stamped on every
completion, which IS the billed figure wherever one is reported), not off a provider's reported
`cost` alone, which most adapters never write.

`_fmt_exhausted` / `_fmt_left` are the one place the warning/violation wording lives (finish
summaries and the loop's BUDGET tail quote it).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import turnwindow

#: The SECOND and last warning line, as a fraction of the limit — and NOTHING in between: a
#: warning repeated every turn becomes a pace runs converge on. Budgets are a runaway BACKSTOP.
FINAL_WARN_AT = 0.95

# Resources whose child allocation is a SHARE of the parent's remainder (halved by default);
# the others (a conversation-life cap, structural counts) are copied or dropped by the caller.
CONSUMABLE = ("turns", "wall_clock", "tokens", "cost")
# Per-resource floor so a nearly-spent parent still hands a child a usable slice.
FLOOR = {"turns": 1, "wall_clock": 1, "tokens": 1000, "cost": 1}


def _fmt_exhausted(resource: str, limit: float, window_limit: float = -1) -> str:
    return {
        "turns": f"turn budget exhausted ({int(limit)})",
        # distinct wording: after a resume the window cap and this one differ, and the run
        # must tell which stopped it. `window_limit` is the sibling `turns` budget's.
        "total_turns": turnwindow.exhausted(limit, window_limit),
        "wall_clock": f"wall-clock budget exhausted ({int(limit)} min)",
        "tokens": f"token budget exhausted ({int(limit)})",
        "cost": f"cost budget exhausted (${int(limit)})",
    }.get(resource, f"{resource} budget exhausted ({limit})")


def _fmt_left(resource: str, left: float, limit: float = -1,
              window_limit: float = -1) -> str:
    return {
        "turns": f"~{int(left)} turns left",
        "total_turns": turnwindow.left(left, limit, window_limit),
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
    exceeded is the violation, the FIRST past its warn line is the warning (construction
    order: turns, total_turns, wall_clock, tokens, cost).
    """

    budgets: list[Budget]

    def spent(self, meter: dict) -> dict | None:
        """The FIRST exceeded budget as `{resource, limit, message}`, or None.

        `message` says it in words, for the model and the finish summary; `resource`/`limit`
        say it in FIELDS, so the health stream and status.json record which budget ended a run.
        """
        for b in self.budgets:
            if b.exceeded(meter.get(b.resource, 0)):
                return {"resource": b.resource, "limit": b.limit,
                        "message": _fmt_exhausted(b.resource, b.limit, self._window_limit())}
        return None

    def _window_limit(self) -> float:
        """This ledger's per-WINDOW turn ceiling (`max_turns`), or -1 when unlimited — what the
        run-spanning cap's wording contrasts itself against (engine/turnwindow).
        """
        for b in self.budgets:
            if b.resource == "turns":
                return b.limit
        return -1

    def warnings(self, meter: dict) -> list[tuple[str, str]]:
        """Every budget past a warning line, in check order, as `(line-id, text)` — e.g.
        `("turns@0.85", "~5 turns left")`.

        The id names the resource AND the line, so a caller can say each crossing exactly
        once (the text moves every turn and cannot serve as identity).
        """
        out: list[tuple[str, str]] = []
        for b in self.budgets:
            current = meter.get(b.resource, 0)
            line = b.warn_line(current)
            left = b.left(current)
            if line is not None and left is not None:   # a warn line implies a finite limit
                out.append((f"{b.resource}@{line:g}",
                            _fmt_left(b.resource, left, b.limit, self._window_limit())))
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
