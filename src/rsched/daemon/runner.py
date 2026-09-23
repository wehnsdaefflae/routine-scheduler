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
from . import run_gate, runner_reap, runner_state
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

        EVERY active run counts, a detached background task included. The exclusion that used
        to sit here rested on `start_new_session=True` letting the task outlive the daemon's
        SIGTERM, and no deployment honours that: under Docker the daemon's exit ends tini
        (PID 1) and the kernel SIGKILLs the whole PID namespace; under the systemd unit the
        default KillMode=control-group kills every process left in the cgroup. A new session
        changes the session, not the container and not the cgroup. So the task did not survive
        — it died at rc=-9, the boot reap closed it `aborted`/`daemon_restart`, and its owner
        was handed "[background task was cancelled]": the one class of run the drain exists to
        protect, restarted out from under precisely because it was excluded. The wait it costs
        is bounded by the task's own 60-minute budget (daemon/detached.py), and a background
        task can never park on a user, so the gap always comes.
        """
        states: list[str] = []
        for run in self.active.values():
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
        if not cfg.enabled:
            log.info("fire_refused_disabled routine=%s reason=%s", cfg.slug, reason)
            return None
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

    def resume_blocker(self, cfg: RoutineConfig, ts: str) -> str | None:
        """Why `resume` would refuse, in the operator's words — or None when it would proceed.

        ONE source of truth for the decision AND the wording: `resume` consults this rather
        than re-testing the conditions, so a refusal can never name a cause that is not the
        real one. The old message listed all three at once ("already running, draining, or
        run dir gone"); on 2026-09-14 it was shown for a routine that was none of them, and
        the false leads cost more time than the failure did.
        """
        if self.draining:
            return "the daemon is draining for a restart"
        if cfg.slug in self.active:
            return f"another run of {cfg.slug} is already active ({self.active[cfg.slug].run_id})"
        if not (cfg.dir / "runs" / ts).is_dir():
            return f"run directory for {ts} is gone"
        return None

    async def resume(self, cfg: RoutineConfig, ts: str, *, reason: str = "resume") -> str | None:
        """Re-run an interrupted (terminal) run in place, rehydrating its transcript so it continues
        where it left off. Returns None when `resume_blocker` names a reason it cannot.
        """
        if self.resume_blocker(cfg, ts) is not None:
            return None
        run_dir = cfg.dir / "runs" / ts
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

    def spawn(self, coro) -> asyncio.Task:
        """Run `coro` as a tracked background task.

        ONE place holds the strong reference (RUF006: a bare create_task can be garbage
        collected mid-flight, which on this loop means a supervised run that silently stops
        being supervised). The supervisor, the sigkill auto-resume, the post-finish inbox
        sweep and off-loop retention all go through here.
        """
        task = asyncio.create_task(coro)
        self._supervisors.add(task)
        task.add_done_callback(self._supervisors.discard)
        return task

    def _spawn_supervisor(self, run: ActiveRun, cfg: RoutineConfig, reason: str,
                          resume: bool = False) -> None:
        self.spawn(self._supervise(run, cfg, reason, resume=resume))

    async def _supervise(self, run: ActiveRun, cfg: RoutineConfig, reason: str,
                         resume: bool = False) -> None:
        sem = run.sem or self.semaphore
        stderr = b""
        spawn: asyncio.Task | None = None
        try:
            await sem.acquire()
            run.holds_slot = True
            # `run.cancelled` = aborted while still queued: never spawn, but FALL THROUGH to
            # the reap. An early `return` here skipped it, and reap() is the only caller of
            # `runner.active.pop(run.slug)` — so the slug stayed registered forever. That
            # leak is worse than a stale dict entry, because two other checks read the same
            # dict: `resume()` refuses on `cfg.slug in self.active` (so the routine could
            # never be resumed or fired again), and `active_states()` feeds the restart
            # drain (so the daemon believed runs were live and never restarted). The only
            # way out was the restart the leak itself prevented. reap() has always handled
            # this case — it pops the slug, then returns early on `cancelled and proc is
            # None` — it was simply never reached.
            if (not run.cancelled
                    and await run_gate.admit(run, cfg, self.server, reason, resume)
                    and not run.cancelled and not run.user_cancel):
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    *runner_state.engine_cmd(self.server, str(cfg.dir), run.run_ts,
                                             resume=resume),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    cwd=str(cfg.dir),
                ))
                # Cancellation must not discard a process created before its handle arrives.
                proc = await asyncio.shield(spawn)
                run.proc = proc
                if self._launch_cancelled(run):
                    await self._kill_and_reap(run)
                    run_gate.terminal(run, "aborted", "failed", "Run aborted during launch")
                    return
                self.bus.publish({"event": "run_started", "routine": cfg.slug,
                                  "run_id": run.run_id, "reason": reason})
                if self.center is not None:
                    self.center.open_process(run.run_id, kind="run", label=run.slug,
                                             run_id=run.run_id)
                log.info("run_started routine=%s run=%s pid=%s reason=%s",
                         cfg.slug, run.run_id, proc.pid, reason)
                waiter = asyncio.create_task(self._watch_waiting(run))
                tailer = (asyncio.create_task(
                    tail_llm_sidecar(run.run_dir, self._llm_recorder(run)))
                    if self.center is not None else None)
                try:
                    _, err = await proc.communicate()
                    stderr = err or b""
                finally:
                    waiter.cancel()
                    if tailer is not None:
                        tailer.cancel()   # its finally drains last-moment records before reap
                        with contextlib.suppress(asyncio.CancelledError):
                            await tailer
        except asyncio.CancelledError:
            run.user_cancel = True
            # Keep ownership even if cancellation arrives again while acquiring/reaping.
            async def cleanup() -> None:
                if spawn is not None and run.proc is None:
                    with contextlib.suppress(Exception):
                        run.proc = await spawn
                await self._kill_and_reap(run)
                run_gate.terminal(run, "aborted", "failed", "Run supervisor cancelled")
            cleanup_task = asyncio.create_task(cleanup())
            while not cleanup_task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(cleanup_task)
            cleanup_task.result()
            raise
        except Exception as exc:
            run_gate.terminal(run, "failed", "failed", f"Run launch failed: {exc}")
        finally:
            if run.holds_slot:
                sem.release()
                run.holds_slot = False
            runner_reap.reap(self, run, cfg, stderr)

    @staticmethod
    def _launch_cancelled(run: ActiveRun) -> bool:
        """Re-read flags that may change while the process handshake is awaited."""
        return run.cancelled or run.user_cancel

    @staticmethod
    async def _kill_and_reap(run: ActiveRun) -> None:
        """Kill the owned session even if its leader already exited; reap the leader."""
        if run.proc is not None:
            import os
            import signal
            with contextlib.suppress(ProcessLookupError):
                os.killpg(run.proc.pid, signal.SIGKILL)
            await run.proc.communicate()

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


