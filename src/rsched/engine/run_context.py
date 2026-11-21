"""RunContext: everything one (sub)run needs — config, dirs, budgets ledger, status writer.

status.json is engine-owned and written atomically; the daemon and web UI only read it.
Wall-clock accrual excludes time spent paused or waiting for a blocking answer.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import RoutineConfig, ServerConfig
from ..endpoints import EndpointRegistry
from ..endpoints.usage import dollars_spent, fold_usage
from ..ids import now_iso
from ..ids import run_id as make_run_id
from ..paths import atomic_write_json, read_json, resolve_rel
from . import turnwindow
from .budgets_config import Budgets, _vm_hwm_kb
from .transcript import Transcript

if TYPE_CHECKING:
    from ..grantpolicy import GrantPolicy

# Sentinel default for "argument not passed" where None is a meaningful value.
_UNSET: Any = object()

#: The prefix `engine/background.py` names its threads with (`background-<handle>`). Not
#: imported: `add_usage` is on every model call's hot path and must not pull the executor.
#: A test pins the two together.
BACKGROUND_THREAD_PREFIX = "background-"

def background_parking_key() -> str | None:
    """The key a BACKGROUND thread's spend parks under, or None on any other thread.

    Background spend books on collection (D167); the handler cannot know it is backgrounded,
    so the thread name (set by `background.start`) is what tells.
    """
    name = threading.current_thread().name
    return name if name.startswith(BACKGROUND_THREAD_PREFIX) else None

def _never_aborted() -> bool:
    """`RunContext.aborted` until a loop installs its own: a context no loop drives (a test,
    a direct dispatch) is never stopping.
    """
    return False


def work_dir(ctx: RunContext) -> Path:
    """Where a run's RELATIVE paths resolve right now: the OPEN task's workspace
    (engine/jobops.py), or the routine's own dir. Seals stay anchored on the routine's dir.
    A free function so lighter context stand-ins work too.
    """
    return getattr(ctx, "job_dir", None) or ctx.routine.dir


def resolve_action_path(ctx: RunContext, rel: str, *, write: bool = False) -> Path:
    """An action's path, resolved where the run's relative paths resolve (`work_dir`), or for a
    relative one the working directory does not hold, in the first allowed root that does
    (`prefer_existing`) — and held to its roots, the routine's own dir always among them, so a
    routine file stays reachable. Raises PermissionError outside them (paths.resolve_rel).
    """
    roots = ctx.write_roots() if write else ctx.read_roots()
    return resolve_rel(work_dir(ctx), rel, [*roots, ctx.routine.dir], prefer_existing=True)


@dataclass
class RunContext:
    """Everything one run carries: identity (routine, ts, dir), collaborators (registry,
    transcript), budgets, and live state mirrored to `status.json` (single writer: the
    engine process).
    """

    routine: RoutineConfig
    server: ServerConfig
    registry: EndpointRegistry
    run_ts: str
    run_dir: Path
    transcript: Transcript
    budgets: Budgets
    depth: int = 0
    # WHICH child this is (engine/child.py's n and label), so routine-wide surfaces (a deferred
    # ask's decision record) can say who asked. Zero/empty at depth 0.
    sub_n: int = 0
    sub_label: str = ""
    sub_counter: list[int] = field(default_factory=lambda: [0])  # shared across the whole tree
    # Guards sub_counter: parallel spawn threads allocate child numbers concurrently.
    # Shared tree-wide like the counter itself (childrun passes both to every child ctx).
    sub_lock: threading.Lock = field(default_factory=threading.Lock)
    # The run's grant policy, set by EngineLoop from the routine's capabilities mapping
    # (+ the library's requires: index for denial wording). None (constructed directly) = open.
    grants: GrantPolicy | None = None
    # Whether this run is being stopped (installed by loopsetup.configure). The jailed runner
    # polls it so a util/script/shell command ends with the run (utils_run.run_jailed).
    aborted: Callable[[], bool] = _never_aborted

    turn: int = 0
    phase: str = ""
    # Every stage module this run has ENTERED, in entry order, deduped — the path taken, where
    # `phase` is the current node. Stamped at the same seam as `phase` (engine/fileops.py); a
    # resumed leg starts from the prior leg's (history.prior_counters).
    phases_entered: list[str] = field(default_factory=list)
    # The phases the run RECORDED for itself in state/phase.json — `stage_coverage`'s second,
    # stronger evidence source: `phases_entered` misses a stage whose module the run did not
    # re-read.
    phases_recorded: list[str] = field(default_factory=list)
    # The recipe version (recipes.current_recipe_commit) that produced this run; None for
    # unversioned dirs. In status.json and the usage record so outcomes stay attributable.
    recipe_commit: str | None = None
    # Live deliberation level (config.DELIBERATION_LEVELS); a control.json switch mutates it.
    deliberation: str = ""
    # Rule modules the user added mid-run (control.json `add_rules`) already delivered — so a
    # repeated signal does not re-append them. `read_rule` needs no such guard.
    consulted_rules: set[str] = field(default_factory=set)
    # Resolved paths this run has read, viewed, or written — write_file's grounding set
    # (rebuilt from the transcript on resume).
    seen_paths: set[str] = field(default_factory=set)
    # The run's ONE-TIME grant overlay (the "now" half of the grant model, entities.py),
    # folded over the config GrantPolicy by requests.rebuild_policy. In-memory only: a resumed
    # leg starts empty and the model re-asks. grant_args carries a decision's extra datum.
    granted_now: set[str] = field(default_factory=set)
    denied_now: set[str] = field(default_factory=set)
    grant_args: dict = field(default_factory=dict)
    # The subset of granted_now armed as allow-once (D65): revoked from BOTH sets once the next
    # matching action lands (requests.consume_once_grants). Only TURN_ACTION_CLASSES.
    granted_once: set[str] = field(default_factory=set)
    # Machine shares actually mounted under mnt/<name>/ (proven at provisioning) and why each
    # missing one is missing — CAPABILITIES reports these, never config alone.
    mounted_shares: set[str] = field(default_factory=set)
    unavailable_shares: dict[str, str] = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    # Background spend PARKED per thread, booked by `background.collect` at the turn boundary
    # (D167): folding from a thread races the loop's fold and makes the budget check depend on
    # thread timing.
    _deferred_usage: dict = field(default_factory=dict)
    # Guards `usage`: the main thread books while a background thread may be parking.
    _usage_lock: threading.Lock = field(default_factory=threading.Lock, repr=False,
                                        compare=False)
    # Spend by EARLIER legs (set on resume). Budgets ignore it — a resume gets a fresh window —
    # but reporting carries usage_total() = base + this window.
    usage_base: dict = field(default_factory=dict)
    # Active wall clock by EARLIER legs (seeded in __post_init__). Budgets ignore it like
    # usage_base; reporting carries elapsed_total_s() = base + window.
    elapsed_base_s: float = 0.0
    state: str = "starting"
    # The finish outcome (ok|partial|failed|aborted), stamped once at run end: status.json's
    # `state` folds partial into "finished". None while live.
    outcome: str | None = None
    question: dict | None = None
    main_model: str = ""              # "<endpoint>/<model>" resolved each turn (in status.json)
    # The model the ROUTINE WAS GIVEN — its chain head, never re-pointed. `main_model` follows
    # failover; any verdict that indicts a model must name this one (D146).
    configured_model: str = ""
    failover_rungs: int = 0           # how many hard switches down the chain this run took
    # The FIRST rung's failure ("<endpoint>/<model>: <error>"), never overwritten: when the whole
    # chain dies, the last rung's error is the least informative.
    first_failover_cause: str = ""
    budget_base_turn: int = 0         # turns before this count against a prior window (resume)
    # Budget warning LINES already given (budget_warning): said once per line, never a countdown
    _budget_warned: set = field(default_factory=set)
    prompt_chars: dict = field(default_factory=dict)  # the system prompt by section (boot.py)
    schema_retries: int = 0           # cumulative schema-violation retries this run (telemetry)
    referrals: int = 0                # turns/llm-calls answered by the `uncensored` model (audit)
    refusal_level: int = 0    # the uncensored back-off (engine/refusal.py, 2026-10-16)
    takeover_left: int = 0
    schema_forcefails: int = 0        # turns that exhausted every schema attempt (telemetry)
    # Per-util telemetry: util name → {outcome: n} (ok / error / usage_error / missing / denied /
    # rejected), ticked by count_util, folded into the usage record (Stats per-util reliability).
    util_stats: dict = field(default_factory=dict)
    # Output-compression telemetry: a count per outcome, `tokens_saved` (APPLIED previews only)
    # and `ms` (paid whatever the outcome). Ticked by note_compression.
    compression_stats: dict = field(default_factory=dict)
    # Decisions this run deferred to the user (a deferred ask; a blocking ask timed out/parked).
    asks_deferred: int = 0
    # Reports this run RECEIVED and has not answered (D131). Carried across a resume; read only
    # by the `unclosed-delivered-report` assist.
    reports_open: list[str] = field(default_factory=list)
    # The turn a permission refusal cost (a refused action is no turn) — for the
    # `capability-denied` assist.
    last_denial_turn: int = 0
    # The accounting the run's finish carried, set by the finish gate once it stands.
    accounting: list[str] = field(default_factory=list)
    # The operator's one-line JOB BRIEF for a hand-started run (engine/brief.py), else "".
    # Seeds goal b1.
    brief: str = ""
    # The run's GOALS (engine/goals.py), replayed on resume; published in status.json.
    goals: list[dict] = field(default_factory=list)
    # A conversation reply's own declaration (finish `final`): None until a finish declares it.
    final: bool | None = None
    # What scripts/outcome.py measured at the run's end (engine/outcome.py), None = undeclared;
    outcome_measures: dict | None = None  # and its actions by task/process/friction (overhead.py)
    overhead: dict | None = None
    notes_read: set[str] = field(default_factory=set)   # memory notes read (notereads.py)
    review: dict | None = None   # what the post-run review leg did (engine/reviewleg.py)
    # The run's QUALITY counters (engine/runrecord.py), run-cumulative (reseeded on resume).
    claims_challenged: int = 0
    claims_disputed: int = 0
    holds: int = 0
    interventions: int = 0
    # The OPEN task (engine/jobops.py) and its workspace (`work_dir`). Empty / None otherwise.
    job_id: str = ""
    job_dir: Path | None = None
    # User UTTERANCES this leg; NOT carried across legs — `create_routine` reads it to tell
    # whether the user has spoken since the draft.
    user_replies: int = 0
    _started_mono: float = field(default_factory=time.monotonic)
    #: memo for `stage_coverage`, keyed on the stages entered so far (see there)
    _stage_coverage: dict = field(default_factory=dict)
    _stage_coverage_key: tuple = ("\0",)
    _suspended_s: float = 0.0

    def __post_init__(self) -> None:
        # A resumed leg is a fresh RunContext: seed the base from the prior leg's cumulative
        # elapsed_s still in status.json (a fresh run's queued status has none → 0.0).
        if self.depth == 0 and not self.elapsed_base_s:
            prior = read_json(self.run_dir / "status.json")
            if isinstance(prior, dict):
                try:
                    self.elapsed_base_s = float(prior.get("elapsed_s") or 0.0)
                except (TypeError, ValueError):
                    self.elapsed_base_s = 0.0

    @property
    def run_id(self) -> str:
        return make_run_id(self.routine.slug, self.run_ts)

    def _granted_paths(self, cls: str) -> list[Path]:
        prefix = f"{cls}:"
        return [Path(e[len(prefix):]) for e in sorted(self.granted_now)
                if e.startswith(prefix)]

    def read_roots(self) -> list[Path]:
        """The run's EFFECTIVE readable roots: config fs_read_roots plus one-time fs-read
        grants plus every WRITABLE root (a write grant implies read). Every consumer resolves
        against this, so a granted root behaves exactly like a configured one.
        """
        return [*self.routine.fs_read_roots, *self._granted_paths("fs-read"),
                *self.write_roots()]

    def write_roots(self) -> list[Path]:
        """The effective writable roots — write_roots' counterpart of read_roots()."""
        return [*self.routine.fs_write_roots, *self._granted_paths("fs-write")]

    @property
    def root_run_dir(self) -> Path:
        """The top-level run dir (status/control/inbox live there, even for subruns)."""
        d = self.run_dir
        while d.name.isdigit() and d.parent.name == "sub":
            d = d.parent.parent
        return d

    @property
    def root_routine_dir(self) -> Path:
        """The ROUTINE's own dir, where `questions/`, `inbox/` and every surface a person reads
        live. A CHILD's `routine.dir` is its `runs/<ts>/sub/<n>/` workspace, which no surface
        scans.
        """
        return self.root_run_dir.parent.parent

    def elapsed_s(self) -> float:
        """THIS window's active wall clock — what budgets meter (a resume gets a fresh
        wall-clock window, mirroring usage vs usage_base).
        """
        return time.monotonic() - self._started_mono - self._suspended_s

    def elapsed_total_s(self) -> float:
        """Cumulative active wall clock across ALL legs — what reporting shows."""
        return self.elapsed_base_s + self.elapsed_s()

    def credit_suspended(self, seconds: float) -> None:
        self._suspended_s += seconds

    def add_usage(self, usage: dict) -> None:
        """Book one usage reading against this run — the single writer of `usage`.

        The one place that enforces D167 for every handler: on a background thread the reading
        is PARKED under the thread's name for `background.collect`; otherwise it folds straight
        in, under the lock.
        """
        if (parked := background_parking_key()) is not None:
            with self._booking_lock():
                fold_usage(self._parking().setdefault(parked, {}), usage)
            return
        with self._booking_lock():
            fold_usage(self.usage, usage)

    def take_deferred_usage(self, key: str) -> dict:
        """The spend one background call parked, removed from the parking area — for the
        collector to book. Empty for a call that spent no tokens (a `util`, a `read_file`),
        which is the common case and books nothing.
        """
        with self._booking_lock():
            return self._parking().pop(key, {})

    # These accessors DEFAULT their attribute into existence: some callers build a RunContext
    # via `RunContext.__new__` with only the fields they need, and booking must still work.

    def _booking_lock(self) -> threading.Lock:
        lock = getattr(self, "_usage_lock", None)
        if lock is None:
            lock = self._usage_lock = threading.Lock()
        return lock

    def _parking(self) -> dict:
        parked = getattr(self, "_deferred_usage", None)
        if parked is None:
            parked = self._deferred_usage = {}
        return parked

    def usage_total(self) -> dict:
        """This window's usage plus earlier legs' (usage_base) — what reporting shows.

        Seeded with in/out at zero: a fold adds no key for a zero reading, and status.json
        must carry both even for a run that spent nothing.
        """
        total: dict = {"in": 0, "out": 0, **self.usage_base}
        fold_usage(total, self.usage)
        return total

    def count_util(self, name: str, outcome: str) -> None:
        """One per-util telemetry tick (see util_stats). The catalog pseudo-utils
        (`list`, `show`) are discovery, not execution — callers skip them.
        """
        cell = self.util_stats.setdefault(name, {})
        cell[outcome] = cell.get(outcome, 0) + 1

    def note_compression(self, metrics: dict) -> None:
        """One output-compression tick (see compression_stats), from the metadata the
        compression seam attached to the observation.

        Savings accrue for an APPLIED preview only; time accrues for every outcome,
        because a rejected compression costs the run exactly what a successful one does.
        """
        tally = self.compression_stats
        status = str(metrics.get("status") or "unknown")
        tally[status] = int(tally.get(status, 0)) + 1
        if status == "applied":
            tally["tokens_saved"] = (int(tally.get("tokens_saved", 0))
                                     + int(metrics.get("estimated_tokens_saved") or 0))
        tally["ms"] = round(float(tally.get("ms", 0.0))
                            + float(metrics.get("elapsed_ms") or 0.0), 2)

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
        `total_turns` is cumulative; `cost` is NOTIONAL $ (endpoints/usage.dollars_spent, F758).
        """
        return {
            "turns": self.turn - self.budget_base_turn,
            "total_turns": self.turn,
            "wall_clock": self.elapsed_s() / 60.0,
            "tokens": self.usage["in"] + self.usage["out"],
            "cost": dollars_spent(self.usage),
        }

    def budget_spent(self) -> dict | None:
        """The budget that stopped this run: `{resource, limit, message}`, or None."""
        return self.budgets.ledger().spent(self.meter())

    def budget_warning(self) -> str | None:
        """The next budget warning this run has not been given yet — its cue to wind down
        DELIBERATELY (record, then an authored finish) instead of meeting the spent budget
        mid-work. None once every crossed line has been said.

        A budget warning is an EVENT, not a state — repeating it makes the backstop a
        countdown. Each line (85%, then 95%, per resource) is announced exactly once.
        """
        for line, text in self.budgets.ledger().warnings(self.meter()):
            if line not in self._budget_warned:
                self._budget_warned.add(line)
                return text
        return None

    def tokens_remaining(self) -> int | None:
        """Tokens left in the budget; None = unlimited."""
        left = self.budgets.ledger().remaining("tokens", self.meter())
        return None if left is None else int(left)

    def turns_remaining(self) -> int | None:
        """Turns left in this leg's budget window; None = unlimited."""
        left = self.budgets.ledger().remaining("turns", self.meter())
        return None if left is None else int(left)

    def child_budgets(self, *, overrides: dict | None = None) -> Budgets:
        """A subrun/subtask's budgets: each consumable resource is HALF the parent's remainder
        (an unlimited time/token/cost budget stays unlimited); the conversation-life cap
        (max_total_turns) is NOT inherited, structural knobs are copied. `overrides` pins a
        resource to an absolute limit — a subtask's explicit `turns` cap. Uses the unified
        allocator (BudgetLedger.allocate).
        """
        alloc = self.budgets.ledger().allocate(self.meter(), fraction=0.5, overrides=overrides)
        lim = {b.resource: int(b.limit) for b in alloc.budgets}
        b = self.budgets
        return Budgets(
            max_turns=lim["turns"],
            max_wall_clock_min=lim["wall_clock"],
            max_total_tokens=lim["tokens"],
            max_child_runs=b.max_child_runs,
            max_child_depth=b.max_child_depth,
            ask_timeout_min=b.ask_timeout_min,
            max_cost=lim["cost"],
        )

    def stage_coverage(self) -> dict:
        """`{declared, entered, skipped}` for this run — the recipe's stages against the
        ones it actually entered. Empty lists for a single-file recipe.

        Memoized on the entered/recorded stages — the only inputs that move within a run —
        since the derivation reads every module and `write_status` asks twice a turn.
        """
        key = (tuple(self.phases_entered), tuple(self.phases_recorded))
        if self._stage_coverage_key != key:
            from ..readmodels.statemap import stage_coverage
            self._stage_coverage = stage_coverage(self.routine.dir, self.phases_entered,
                                                 recorded=self.phases_recorded)
            self._stage_coverage_key = key
        return self._stage_coverage

    def write_status(self, state: str | None = None, question: dict | None = _UNSET) -> None:
        """Update status.json (root runs only — subruns report through the parent transcript)."""
        if state is not None:
            self.state = state
        if question is not _UNSET:
            self.question = question
        if self.depth > 0:
            return
        atomic_write_json(self.run_dir / "status.json", {
            "run_id": self.run_id,
            "pid": os.getpid(),
            "state": self.state,
            "outcome": self.outcome,
            "started": self.run_ts,
            "updated": now_iso(),
            "turn": self.turn,
            # active wall clock across ALL legs; the final write freezes the run's duration
            "elapsed_s": int(self.elapsed_total_s()),
            "phase": self.phase,
            # the PATH through the recipe: declared stages entered and never entered
            "stages": self.stage_coverage(),
            "question": self.question,
            "usage": self.usage_total(),
            "model": self.main_model,
            "deliberation": self.deliberation,
            "schema_retries": self.schema_retries,
            "schema_forcefails": self.schema_forcefails,
            "referrals": self.referrals,
            "refusal_level": self.refusal_level, "takeover_left": self.takeover_left,
            # the recipe version that produced this run (null: unversioned dir)
            "recipe_commit": self.recipe_commit,
            "utils": self.util_stats, "prompt_segments": getattr(self, "prompt_segments", {}),
            "asks_deferred": self.asks_deferred,
            "claims_challenged": self.claims_challenged,
            "claims_disputed": self.claims_disputed,
            "holds": self.holds,
            "interventions": self.interventions,
            "reports_open": self.reports_open,
            # the finish's own accounting (engine/accounting.py)
            "accounting": self.accounting,
            **({"brief": self.brief} if self.brief else {}),
            **({"goals": self.goals} if self.goals else {}),
            **({"final": self.final} if self.final is not None else {}),
            **({"outcome_measures": self.outcome_measures} if self.outcome_measures else {}),
            **({"overhead": self.overhead} if self.overhead else {}),
            # the job being worked on right now (engine/jobops.py): the live tail's "where is it"
            **({"job": self.job_id} if self.job_id else {}),
            # peak resident memory of the engine process (kB), read at close-out for an OOM
            "vm_hwm_kb": _vm_hwm_kb(),
            # THIS WINDOW's remainders (`turn` above is cumulative). The same reading renders
            # the per-turn BUDGET LEFT tail (D227), so prompt and file cannot disagree.
            "budgets": turnwindow.budget_block(self),
        })
