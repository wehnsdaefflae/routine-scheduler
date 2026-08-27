"""A routine's configured CEILINGS, and the memory high-water mark recorded beside them.

Split out of `run_context.py` (F393): `RunContext` is live run state; this is the static
configuration it was started with, plus the one process fact worth sampling next to it.

`Budgets` holds the numbers exactly as `routine.yaml` writes them and turns them into the
unified stop-condition primitive on demand (`ledger()` -> `BudgetLedger`). Budgets are a runaway
BACKSTOP, never a pace — what a job is FOR is `engine/stopping.py`.

`_vm_hwm_kb` exists because an OOM kill used to leave no trace: rc=-9 with nothing to say how
close to host RAM the engine got (F348). Every status write samples it, so the last one before
a kill is the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import Budget, BudgetLedger


def _vm_hwm_kb() -> int | None:
    """Peak resident memory (VmHWM, kB) of THIS engine process, from /proc/self/status.

    F348: rc=-9 post-mortems were blind — a run the kernel OOM-killed left no memory
    trace. write_status samples this every update, so the run's LAST status write tells
    the daemon's close-out (and D99's auto-resume note) how close to host RAM the engine
    got before it died. None on non-Linux or a degraded /proc — absence is a platform
    fact, never an error.
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
    max_subruns: int
    max_subrun_depth: int
    ask_timeout_min: int
    max_cost: int = -1        # -1 = unlimited: whole-dollar ceiling on real provider $ spend
    max_total_turns: int = -1  # -1 = unlimited: cumulative turn cap across ALL resume windows
                               # (a conversation's whole life); max_turns bounds one window

    @classmethod
    def from_config(cls, budgets: dict) -> Budgets:
        return cls(**budgets)

    def ledger(self) -> BudgetLedger:
        """This run's stop conditions as the unified primitive (engine/budget.py). The ONE
        place the run/window/subtask/subrun checks all share — turns, the conversation-life
        cap, wall clock, tokens, cost — in the order they are checked. The structural knobs
        (max_subruns/max_subrun_depth/ask_timeout_min) are not stop-over-time budgets and stay
        plain fields.
        """
        return BudgetLedger([
            Budget("turns", self.max_turns),
            Budget("total_turns", self.max_total_turns),
            Budget("wall_clock", self.max_wall_clock_min),
            Budget("tokens", self.max_total_tokens),
            Budget("cost", self.max_cost),
        ])
