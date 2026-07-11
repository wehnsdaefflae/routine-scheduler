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
from .transcript import Transcript


@dataclass
class Budgets:
    max_turns: int
    max_wall_clock_min: int
    max_total_tokens: int
    max_subruns: int
    max_subrun_depth: int
    ask_timeout_h: int

    @classmethod
    def from_config(cls, budgets: dict) -> "Budgets":
        return cls(**budgets)


@dataclass
class RunContext:
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

    turn: int = 0
    phase: str = ""
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
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

    def note_schema_retry(self) -> None:
        """Telemetry: one schema-violation retry occurred this turn."""
        self.schema_retries += 1

    def note_schema_forcefail(self) -> None:
        """Telemetry: a turn exhausted every schema attempt and force-failed."""
        self.schema_forcefails += 1

    def budget_violation(self) -> str | None:
        b = self.budgets
        if self.turn - self.budget_base_turn >= b.max_turns:
            return f"turn budget exhausted ({b.max_turns})"
        if self.elapsed_s() > b.max_wall_clock_min * 60:
            return f"wall-clock budget exhausted ({b.max_wall_clock_min} min)"
        if self.usage["in"] + self.usage["out"] >= b.max_total_tokens:
            return f"token budget exhausted ({b.max_total_tokens})"
        return None

    def child_budgets(self) -> Budgets:
        """Remaining budgets ÷ 2 for a subrun."""
        b = self.budgets
        return Budgets(
            max_turns=max(1, (b.max_turns - self.turn) // 2),
            max_wall_clock_min=max(1, int((b.max_wall_clock_min - self.elapsed_s() / 60) // 2)),
            max_total_tokens=max(1000, (b.max_total_tokens - self.usage["in"] - self.usage["out"]) // 2),
            max_subruns=b.max_subruns,
            max_subrun_depth=b.max_subrun_depth,
            ask_timeout_h=b.ask_timeout_h,
        )

    def write_status(self, state: str | None = None, question: dict | None = "\0") -> None:
        """Update status.json (root runs only — subruns report through the parent transcript)."""
        if state is not None:
            self.state = state
        if question != "\0":
            self.question = question
        if self.depth > 0:
            return
        b = self.budgets
        atomic_write_json(self.run_dir / "status.json", {
            "run_id": self.run_id,
            "pid": __import__("os").getpid(),
            "state": self.state,
            "started": self.run_ts,
            "updated": now_iso(),
            "turn": self.turn,
            "phase": self.phase,
            "question": self.question,
            "usage": dict(self.usage),
            "model": self.main_model,
            "schema_retries": self.schema_retries,
            "schema_forcefails": self.schema_forcefails,
            "budgets": {
                "turns_left": max(0, b.max_turns - (self.turn - self.budget_base_turn)),
                "wall_clock_left_s": max(0, int(b.max_wall_clock_min * 60 - self.elapsed_s())),
            },
        })
