"""RunContext: everything one (sub)run needs — config, dirs, budgets ledger, status writer.

status.json is engine-owned and written atomically; the daemon and web UI only read it.
Wall-clock accrual excludes time spent paused or waiting for a blocking answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import RoutineConfig, ServerConfig
from ..endpoints import EndpointRegistry
from ..ids import now_iso, run_id as make_run_id
from ..paths import atomic_write_json
from .budget import Budget, BudgetLedger
from .transcript import Transcript


@dataclass
class Budgets:
    """A run's hard ceilings (turns, wall clock, tokens, subruns, ask timeout) — checked
    at every turn boundary; children get half the parent's remainder."""

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
    def from_config(cls, budgets: dict) -> "Budgets":
        return cls(**budgets)

    def ledger(self) -> BudgetLedger:
        """This run's stop conditions as the unified primitive (engine/budget.py). The ONE
        place the run/window/subtask/subrun checks all share — turns, the conversation-life
        cap, wall clock, tokens, cost — in the order they are checked. The structural knobs
        (max_subruns/max_subrun_depth/ask_timeout_min) are not stop-over-time budgets and stay
        plain fields."""
        return BudgetLedger([
            Budget("turns", self.max_turns),
            Budget("total_turns", self.max_total_turns),
            Budget("wall_clock", self.max_wall_clock_min),
            Budget("tokens", self.max_total_tokens),
            Budget("cost", self.max_cost),
        ])


@dataclass
class RunContext:
    """Everything one run carries: identity (routine, ts, dir), collaborators (registry,
    transcript), budgets, and live state mirrored to `status.json` (single writer: the
    engine process)."""

    routine: RoutineConfig
    server: ServerConfig
    registry: EndpointRegistry
    run_ts: str
    run_dir: Path
    transcript: Transcript
    budgets: Budgets
    depth: int = 0
    parent_run_id: str | None = None
    sub_counter: list[int] = field(default_factory=lambda: [0])  # shared across the whole tree
    # The run's grant policy (grants.GrantPolicy), set by EngineLoop from the routine's
    # capabilities mapping (+ the library's requires: index for denial wording).
    # None (direct construction) = unrestricted.
    grants: object | None = None

    turn: int = 0
    phase: str = ""
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    # Spend recorded by EARLIER legs of this run (set on resume from the transcript).
    # Budgets deliberately ignore it — a resume gets a fresh window — but reporting must
    # not: status.json and the finish event carry usage_total() = base + this window.
    usage_base: dict = field(default_factory=dict)
    state: str = "starting"
    question: dict | None = None
    main_model: str = ""              # "<endpoint>/<model>" resolved each turn (surfaced in status.json)
    budget_base_turn: int = 0         # turns before this count against a prior budget window (resume)
    schema_retries: int = 0           # cumulative schema-violation retries this run (telemetry)
    schema_forcefails: int = 0        # turns that exhausted every schema attempt (telemetry)
    _started_mono: float = field(default_factory=time.monotonic)
    _suspended_s: float = 0.0

    @property
    def run_id(self) -> str:
        return make_run_id(self.routine.slug, self.run_ts)

    @property
    def root_run_dir(self) -> Path:
        """The top-level run dir (status/control/inbox live there, even for subruns)."""
        d = self.run_dir
        while d.name.isdigit() and d.parent.name == "sub":
            d = d.parent.parent
        return d

    def elapsed_s(self) -> float:
        return time.monotonic() - self._started_mono - self._suspended_s

    def credit_suspended(self, seconds: float) -> None:
        self._suspended_s += seconds

    def add_usage(self, usage: dict) -> None:
        self.usage["in"] += int(usage.get("in") or 0)
        self.usage["out"] += int(usage.get("out") or 0)
        # Cache traffic (adapters report it when the provider does): cached_in = input
        # served from the provider's prompt cache (~0.1x price), cache_write = input
        # written into it (~1.25x). Kept OUT of "in" so token budgets keep their meaning.
        for key in ("cached_in", "cache_write"):
            if usage.get(key):
                self.usage[key] = self.usage.get(key, 0) + int(usage[key])
        if usage.get("cost"):   # real $ cost, when the provider reports it (OpenRouter)
            self.usage["cost"] = round(self.usage.get("cost", 0.0) + float(usage["cost"]), 6)

    def usage_total(self) -> dict:
        """This window's usage plus earlier legs' (usage_base) — what reporting shows."""
        if not self.usage_base:
            return dict(self.usage)
        total = dict(self.usage_base)
        for key, val in self.usage.items():
            if key == "cost":
                total["cost"] = round(total.get("cost", 0.0) + float(val), 6)
            else:
                total[key] = total.get(key, 0) + int(val)
        return total

    def note_schema_retry(self) -> None:
        """Telemetry: one schema-violation retry occurred this turn."""
        self.schema_retries += 1

    def note_schema_forcefail(self) -> None:
        """Telemetry: a turn exhausted every schema attempt and force-failed."""
        self.schema_forcefails += 1

    def meter(self) -> dict:
        """A snapshot of live consumption per budgeted resource, in each limit's own unit —
        the input to every ledger check. Consumption lives HERE (single writer), never in the
        ledger. `turns` is window-scoped (a resume gets a fresh window via budget_base_turn);
        `total_turns` is the cumulative whole-conversation count."""
        return {
            "turns": self.turn - self.budget_base_turn,
            "total_turns": self.turn,
            "wall_clock": self.elapsed_s() / 60.0,
            "tokens": self.usage["in"] + self.usage["out"],
            "cost": self.usage.get("cost", 0.0),
        }

    def budget_violation(self) -> str | None:
        return self.budgets.ledger().violation(self.meter())

    def budget_warning(self) -> str | None:
        """The 85% line on any budget — the run's cue to wind down DELIBERATELY (record, then
        an authored finish) instead of being cut off mid-work by budget_violation."""
        return self.budgets.ledger().warning(self.meter())

    def tokens_remaining(self) -> int | None:
        """Tokens left in the budget; None = unlimited."""
        left = self.budgets.ledger().remaining("tokens", self.meter())
        return None if left is None else int(left)

    def child_budgets(self, *, overrides: dict | None = None) -> Budgets:
        """A subrun/subtask's budgets: each consumable resource is HALF the parent's remainder
        (an unlimited time/token/cost budget stays unlimited); the conversation-life cap
        (max_total_turns) is NOT inherited, structural knobs are copied. `overrides` pins a
        resource to an absolute limit — a subtask's explicit `turns` cap. Uses the unified
        allocator (BudgetLedger.allocate)."""
        alloc = self.budgets.ledger().allocate(self.meter(), fraction=0.5, overrides=overrides)
        lim = {b.resource: int(b.limit) for b in alloc.budgets}
        b = self.budgets
        return Budgets(
            max_turns=lim["turns"],
            max_wall_clock_min=lim["wall_clock"],
            max_total_tokens=lim["tokens"],
            max_subruns=b.max_subruns,
            max_subrun_depth=b.max_subrun_depth,
            ask_timeout_min=b.ask_timeout_min,
            max_cost=lim["cost"],
        )

    def write_status(self, state: str | None = None, question: dict | None = "\0") -> None:
        """Update status.json (root runs only — subruns report through the parent transcript)."""
        if state is not None:
            self.state = state
        if question != "\0":
            self.question = question
        if self.depth > 0:
            return
        ledger, meter = self.budgets.ledger(), self.meter()
        turns_left = ledger.remaining("turns", meter)
        wall_left_min = ledger.remaining("wall_clock", meter)
        atomic_write_json(self.run_dir / "status.json", {
            "run_id": self.run_id,
            "pid": __import__("os").getpid(),
            "state": self.state,
            "started": self.run_ts,
            "updated": now_iso(),
            "turn": self.turn,
            # active wall-clock so far (paused/waiting time credited back) — the final
            # write at run end freezes it as the run's duration
            "elapsed_s": int(self.elapsed_s()),
            "phase": self.phase,
            "question": self.question,
            "usage": self.usage_total(),
            "model": self.main_model,
            "schema_retries": self.schema_retries,
            "schema_forcefails": self.schema_forcefails,
            "budgets": {
                "turns_left": None if turns_left is None else int(turns_left),
                "wall_clock_left_s": None if wall_left_min is None else int(wall_left_min * 60),
            },
        })
