"""A routine's configured CEILINGS, and the memory high-water mark recorded beside them.

`RunContext` is live run state; this is the static configuration it was started with, plus
the one process fact worth sampling next to it.

`Budgets` holds the numbers exactly as `routine.yaml` writes them and turns them into the
unified stop-condition primitive on demand (`ledger()` -> `BudgetLedger`). Budgets are a runaway
BACKSTOP, never a pace — what a run is FOR is its recipe's `## Done when` (`engine/donewhen.py`).

`_vm_hwm_kb` is sampled by every status write, so the last one before an OOM kill (rc=-9)
says how close to host RAM the engine got.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import Budget, BudgetLedger
from .turnwindow import DERIVE, total_turn_cap


def _vm_hwm_kb() -> int | None:
    """Peak resident memory (VmHWM, kB) of THIS engine process, from /proc/self/status.

    write_status samples this every update, so the LAST status write tells the daemon's
    close-out how close to host RAM a killed engine got. None on non-Linux or a degraded
    /proc — absence is a platform fact, never an error.
    """
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None

@dataclass
class Budgets:
    """A run's hard ceilings (turns, wall clock, tokens, subruns, ask timeout) — checked
    at every turn boundary; children get half the parent's remainder.
    """

    max_turns: int
    max_wall_clock_min: int   # -1 = unlimited: lifts the wall-clock ceiling (turns still bound)
    max_total_tokens: int     # -1 = unlimited (the default): turns + wall clock bound the run
    max_child_runs: int
    max_child_depth: int
    ask_timeout_min: int
    max_cost: int = -1        # -1 = unlimited: whole-$ ceiling on NOTIONAL spend
                              # (endpoints/usage.dollars_spent)
    max_total_turns: int = -1  # the RESOLVED cumulative cap across ALL resume windows
                               # (max_turns bounds ONE window). -1 = unlimited; the config's
                               # `0` means DERIVE and is resolved in from_config, never here —
                               # a run's ceilings are read off this field by every check.

    @classmethod
    def from_config(cls, budgets: dict) -> Budgets:
        """A routine's configured numbers as this run's ceilings.

        The ONE place the run-spanning cap is resolved (`engine/turnwindow`): `max_total_turns`
        defaults to 3x `max_turns`, because `max_turns` bounds only one resume WINDOW. An
        explicit value wins, -1 included.
        """
        resolved = dict(budgets)
        resolved["max_total_turns"] = total_turn_cap(
            int(resolved.get("max_turns", 0)), int(resolved.get("max_total_turns", DERIVE)))
        return cls(**resolved)

    def ledger(self) -> BudgetLedger:
        """This run's stop conditions as the unified primitive (engine/budget.py). The ONE
        place the run/window/subtask/subrun checks all share — turns, the conversation-life
        cap, wall clock, tokens, cost — in the order they are checked. The structural knobs
        (max_child_runs/max_child_depth/ask_timeout_min) are not stop-over-time budgets and stay
        plain fields.
        """
        return BudgetLedger([
            Budget("turns", self.max_turns),
            Budget("total_turns", self.max_total_turns),
            Budget("wall_clock", self.max_wall_clock_min),
            Budget("tokens", self.max_total_tokens),
            Budget("cost", self.max_cost),
        ])
