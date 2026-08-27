"""Engine subprocess management: spawn, track, abort, reap, retention, orphan recovery.

One engine process per run (`python -m rsched.cli engine-run <dir> --run-ts <ts> --config
<path> --homes <fingerprint>` in this venv), its own process group. The child inherits no
configuration, so the command NAMES the config and the homes it must resolve to and the
child refuses a mismatch (`runner_state.engine_cmd`, F394). The global semaphore counts
starting+running processes; a run parked in waiting_user releases its slot (the daemon
polls status.json cheaply).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from .. import registry
from ..config import RoutineConfig, ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..ids import run_ts as make_run_ts
from ..paths import atomic_write_json, read_json
from . import runner_reap, runner_state
from .events import EventBus
from .llm_tailer import tail_llm_sidecar
from .runner_state import (
    BACKGROUND_SLOTS,
    INTERACTIVE_SLOTS,
    ActiveRun,
    _queued_status,
    abort_process,
)

log = logging.getLogger("rsched.runner")


# The injection channels that count as "a user is talking to this run" for the
# post-finish sweep (R108/F268): the conversation composer and the run page. Everything
# else that lands in an inbox — report deliveries, trigger events, one-shot provenance,
# background results, audit feedback — has its own wake policy and must never re-open a
# finished run from the reap. The tuple itself lives with the engine's inbox
# (engine.inbox.USER_MESSAGE_VIAS): the resume-boot drain (F359) keys on the same
# channels, so wake policy and consumption policy stay ONE vocabulary.


                               # kill leaves no engine finish, _reap logs run_canceled —
                               # a deliberate cancel must not masquerade as orphaned_run


class Runner:
    """Spawns and supervises one `engine-run` subprocess per firing routine — never two
    of the same routine at once, `max_concurrent_runs` slots overall (conversations draw
    from their own INTERACTIVE_SLOTS pool instead), plus the drain mode a self-update
    restart uses to quiesce without killing active runs.
    """

    def __init__(self, server: ServerConfig, bus: EventBus, center=None):
        self.server = server
        self.bus = bus
        self.center = center   # llm_tasks.TaskCenter — a run is a process; its calls are children
        self.semaphore = asyncio.Semaphore(server.max_concurrent_runs)
        # strong refs to the supervise tasks (RUF006: a bare create_task can be GC'd mid-flight)
        self._supervisors: set[asyncio.Task] = set()
        self.interactive_semaphore = asyncio.Semaphore(INTERACTIVE_SLOTS)
        self.background_semaphore = asyncio.Semaphore(BACKGROUND_SLOTS)
        self.active: dict[str, ActiveRun] = {}  # slug → run
        self.draining = False  # set while quiescing for a self-update restart: no new runs fire

    def _under_home(self, cfg: RoutineConfig, home_attr: str) -> bool:
        """True if the run's dir is a direct child of the named server home. Run kind is
        discriminated by HOME everywhere (cfg.kind is dropped by pydantic).
        """
        home = getattr(self.server, home_attr, None)
        try:
            return home is not None and cfg.dir.resolve().parent == Path(home).resolve()
        except OSError:
            return False

    def is_background(self, cfg: RoutineConfig) -> bool:
        """A detached background task — its dir sits directly under background_home."""
        return self._under_home(cfg, "background_home")

    def _sem_for(self, cfg: RoutineConfig) -> asyncio.Semaphore:
        """Detached background tasks draw from their own pool; conversations (dirs under
        conversations_home) from the reserved interactive pool; everything else from cron.
        """
        if self.is_background(cfg):
            return self.background_semaphore
        if self._under_home(cfg, "conversations_home"):
            return self.interactive_semaphore
        return self.semaphore

    def is_active(self, slug: str) -> bool:
        return slug in self.active

    def active_states(self) -> list[str]:
        """Current state of each active run (read from status.json) — for the drain check.
        Detached background tasks are EXCLUDED: a self-update restart must not block on a
        long fire-and-forget job. Its engine child spawns start_new_session=True, so it
        survives the daemon's SIGTERM regardless; the DetachedManager's disk-poll delivers
        it after the restart.
        """
        states: list[str] = []
        for run in self.active.values():
            if run.background:
                continue
            st = read_json(run.run_dir / "status.json")
            states.append(st.get("state", "unknown") if isinstance(st, dict) else "unknown")
        return states

    def _log_refused_scheduled_fire(self, cfg: RoutineConfig, reason: str, cause: str) -> None:
        """A DUE cron fire that produced no run is otherwise invisible: fire() only log.info's
        the refusal when a routine is still active from a prior run (overrun) or the daemon is
        draining for a self-update restart, so a routine chronically un-fired for one of those
        reasons leaves no trace in the health-events audit stream. Emit a health event for the
        SCHEDULED path only; resume, trigger and manual fires overrun legitimately and must not
        spam the stream. NB: a deliberate global PAUSE skips due fires at the SCHEDULER level
        (scheduler.py, before fire() is called) and is intentional — it is not a refusal and is
        not logged here; a pause is the operator's own known action, not a silent drop.
        """
        if reason != "schedule":
            return
        log_health_event(
            self.server.routines_home, "fire_refused",
            routine=cfg.slug, run_id="",
            detail=f"scheduled fire refused ({cause}) — no run started this fire; "
                   f"a routine refused across several fires is going dark")

    async def fire(self, cfg: RoutineConfig, *, reason: str = "schedule") -> str | None:
        """Queue a run unless one is already active for this routine. The subprocess is
        spawned only once a concurrency slot is held. Returns the run_id.
        """
        if self.draining:
            log.info("fire_refused_draining routine=%s reason=%s", cfg.slug, reason)
            self._log_refused_scheduled_fire(cfg, reason, "draining")
            return None
        if cfg.slug in self.active:
            log.info("overrun_skipped routine=%s reason=%s", cfg.slug, reason)
            self._log_refused_scheduled_fire(cfg, reason, "overrun")
            return None
        ts = make_run_ts()
        run_dir = cfg.dir / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        run = ActiveRun(slug=cfg.slug, run_id=f"{cfg.slug}:{ts}", run_ts=ts, run_dir=run_dir,
                        sem=self._sem_for(cfg), background=self.is_background(cfg))
        atomic_write_json(run_dir / "status.json", _queued_status(run.run_id, ts))
        self.active[cfg.slug] = run
        self._spawn_supervisor(run, cfg, reason)
        return run.run_id

    async def resume(self, cfg: RoutineConfig, ts: str, *, reason: str = "resume") -> str | None:
        """Re-run an interrupted (terminal) run in place, rehydrating its transcript so it continues
        where it left off. Refuses if draining, the routine already has an active run, or the run
        dir is gone.
        """
        if self.draining or cfg.slug in self.active:
            return None
        run_dir = cfg.dir / "runs" / ts
        if not run_dir.is_dir():
            return None
        run = ActiveRun(slug=cfg.slug, run_id=f"{cfg.slug}:{ts}", run_ts=ts, run_dir=run_dir,
                        sem=self._sem_for(cfg), background=self.is_background(cfg))
        # RESUME reuses the run dir: status.json still holds the prior leg's cumulative
        # telemetry. Carry it forward (F140) so the boot-time prior_counters reseed sees it
        # instead of the clobbered file — otherwise a finish->reopen drops the pre-finish
        # leg's util histogram and integer counters.
        atomic_write_json(run_dir / "status.json",
                          _queued_status(run.run_id, ts, read_json(run_dir / "status.json")))
        self.active[cfg.slug] = run
        self._spawn_supervisor(run, cfg, reason, resume=True)
        return run.run_id

    async def resume_terminal(self, cfg: RoutineConfig, ts: str | None = None, *,
                              reason: str = "resume") -> str | None:
        """Resume cfg's LAST (or the given ts's) run in place only if that run is TERMINAL —
        the shared "wake a finished conversation" core (the message endpoint, converse on a
        run, answering a finished conversation, detached-result delivery). Returns the new
        run_id, or None when there is nothing terminal to resume (or resume() itself
        refuses: active / draining / run dir gone).
        """
        runs = registry.run_index(cfg.dir, cfg.slug)
        run = (next((r for r in runs if r.ts == ts), None) if ts
               else (runs[0] if runs else None))
        if run is None or run.state not in registry.TERMINAL_STATES:
            return None
        return await self.resume(cfg, run.ts, reason=reason)

    def _spawn_supervisor(self, run: ActiveRun, cfg: RoutineConfig, reason: str,
                          resume: bool = False) -> None:
        task = asyncio.create_task(self._supervise(run, cfg, reason, resume=resume))
        self._supervisors.add(task)
        task.add_done_callback(self._supervisors.discard)

    async def _supervise(self, run: ActiveRun, cfg: RoutineConfig, reason: str,
                         resume: bool = False) -> None:
        sem = run.sem or self.semaphore
        await sem.acquire()
        run.holds_slot = True
        stderr = b""
        try:
            if run.cancelled:   # aborted while queued — never spawn
                return
            run.proc = await asyncio.create_subprocess_exec(
                *runner_state.engine_cmd(self.server, str(cfg.dir), run.run_ts, resume=resume),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                cwd=str(cfg.dir),
            )
            self.bus.publish({"event": "run_started", "routine": cfg.slug,
                              "run_id": run.run_id, "reason": reason})
            if self.center is not None:
                self.center.open_process(run.run_id, kind="run", label=run.slug, run_id=run.run_id)
            log.info("run_started routine=%s run=%s pid=%s reason=%s",
                     cfg.slug, run.run_id, run.proc.pid, reason)
            waiter = asyncio.create_task(self._watch_waiting(run))
            tailer = (asyncio.create_task(tail_llm_sidecar(run.run_dir, self._llm_recorder(run)))
                      if self.center is not None else None)
            try:
                _, err = await run.proc.communicate()
                stderr = err or b""
            finally:
                waiter.cancel()
                if tailer is not None:
                    tailer.cancel()   # its finally drains any last-moment records before reap
                    with contextlib.suppress(asyncio.CancelledError):
                        await tailer
        finally:
            if run.holds_slot:
                sem.release()
                run.holds_slot = False
        runner_reap.reap(self, run, cfg, stderr)

    async def _watch_waiting(self, run: ActiveRun) -> None:
        """A run parked on a blocking question releases its concurrency slot (an idle
        2s-polling process is free); it re-acquires lazily on resume — brief
        oversubscription is accepted, the engine never blocks on it.
        """
        sem = run.sem or self.semaphore
        while True:
            await asyncio.sleep(runner_state.STATUS_POLL_S)
            st = read_json(run.run_dir / "status.json")
            state = st.get("state") if isinstance(st, dict) else None
            if state in ("waiting_user", "paused") and run.holds_slot:
                run.holds_slot = False
                sem.release()
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})
            elif state not in ("waiting_user", "paused", None) and not run.holds_slot:
                await sem.acquire()  # cancellation-safe: waiter is discarded
                run.holds_slot = True
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})

    def _llm_recorder(self, run: ActiveRun):
        """Callback for this run's sidecar tailer: attribute each engine LLM record to the run
        (which is its own process in the task manager) and fold it into the center.
        """
        def _on(rec: dict) -> None:
            rec["run_id"] = run.run_id
            rec.setdefault("process_id", run.run_id)   # no engine-call scope: run = process
            self.center.ingest(rec)
        return _on


    async def abort(self, slug: str) -> bool:
        run = self.active.get(slug)
        if not run:
            return False
        if run.proc is None:
            # still queued for a slot: flag it — the supervisor sees the flag right after
            # its slot acquire (same event loop) and spawns nothing; close the status out
            # here so the run reads aborted, not stuck queued
            run.cancelled = True
            raw = read_json(run.run_dir / "status.json")
            st: dict = raw if isinstance(raw, dict) else {"run_id": run.run_id}
            st.update(state="aborted", updated=now_iso(), question=None)
            atomic_write_json(run.run_dir / "status.json", st)
            return True
        # mark BEFORE killing: if the engine needs SIGKILL and dies without a finish,
        # _reap must attribute the close-out to the user's cancel (F188)
        run.user_cancel = True
        return await abort_process(run.proc.pid)


