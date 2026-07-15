"""Engine subprocess management: spawn, track, abort, reap, retention, orphan recovery.

One engine process per run (`python -m rsched.cli engine-run <slug> --run-ts <ts>` in this
venv), its own process group. The global semaphore counts starting+running processes; a
run parked in waiting_user releases its slot (the daemon polls status.json cheaply).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import RoutineConfig, ServerConfig
from ..ids import now_iso, run_ts as make_run_ts
from ..paths import atomic_write_json, read_json
from ..health_events import log_health_event
from . import registry
from .events import EventBus
from .llm_tailer import tail_llm_sidecar

log = logging.getLogger("rsched.runner")

KILL_GRACE_S = 10
STATUS_POLL_S = 2.0
# Conversations (interactive replies) get their own slot pool: cron load can never queue a
# chat reply, and a long agentic reply never starves the schedule.
INTERACTIVE_SLOTS = 3
# Detached background tasks (dirs under background_home) get a THIRD pool so a couple of
# long fire-and-forget jobs starve neither the schedule nor chat replies.
BACKGROUND_SLOTS = 2


def engine_cmd(target: str, run_ts: str, *, resume: bool = False) -> list[str]:
    """`target` is a routine slug (resolved under routines_home) or a directory path —
    conversations live under their own home, so the runner always passes cfg.dir."""
    cmd = [sys.executable, "-m", "rsched.cli", "engine-run", target, "--run-ts", run_ts]
    if resume:
        cmd.append("--resume")
    return cmd


@dataclass
class ActiveRun:
    """A run the daemon tracks: queued for a slot, running as a subprocess, or parked on
    a user question (a parked run releases its slot — `holds_slot`)."""

    slug: str
    run_id: str
    run_ts: str
    run_dir: Path
    proc: asyncio.subprocess.Process | None = None  # None while queued for a slot
    holds_slot: bool = False
    sem: asyncio.Semaphore | None = None  # the pool this run draws from (cron vs interactive)
    background: bool = False  # a detached background task — excluded from the self-update drain gate


class Runner:
    """Spawns and supervises one `engine-run` subprocess per firing routine — never two
    of the same routine at once, `max_concurrent_runs` slots overall (conversations draw
    from their own INTERACTIVE_SLOTS pool instead), plus the drain mode a self-update
    restart uses to quiesce without killing active runs."""

    def __init__(self, server: ServerConfig, bus: EventBus, center=None):
        self.server = server
        self.bus = bus
        self.center = center   # llm_tasks.TaskCenter — each run is a process; its calls are children
        self.semaphore = asyncio.Semaphore(server.max_concurrent_runs)
        self.interactive_semaphore = asyncio.Semaphore(INTERACTIVE_SLOTS)
        self.background_semaphore = asyncio.Semaphore(BACKGROUND_SLOTS)
        self.active: dict[str, ActiveRun] = {}  # slug → run
        self.draining = False  # set while quiescing for a self-update restart: no new runs fire

    def _under_home(self, cfg: RoutineConfig, home_attr: str) -> bool:
        """True if the run's dir is a direct child of the named server home. Run kind is
        discriminated by HOME everywhere (cfg.kind is dropped by pydantic)."""
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
        conversations_home) from the reserved interactive pool; everything else from cron."""
        if self.is_background(cfg):
            return self.background_semaphore
        if self._under_home(cfg, "conversations_home"):
            return self.interactive_semaphore
        return self.semaphore

    def is_active(self, slug: str) -> bool:
        return slug in self.active

    def is_busy(self, slug: str) -> bool:
        """A run is active — blocks config/file edits and new fires."""
        return slug in self.active

    def active_states(self) -> list[str]:
        """Current state of each active run (read from status.json) — for the drain check.
        Detached background tasks are EXCLUDED: a self-update restart must not block on a
        long fire-and-forget job. Its engine child spawns start_new_session=True, so it
        survives the daemon's SIGTERM regardless; the DetachedManager's disk-poll delivers
        it after the restart."""
        states: list[str] = []
        for run in self.active.values():
            if run.background:
                continue
            st = read_json(run.run_dir / "status.json")
            states.append(st.get("state", "unknown") if isinstance(st, dict) else "unknown")
        return states

    async def fire(self, cfg: RoutineConfig, *, reason: str = "schedule") -> str | None:
        """Queue a run unless one is already active for this routine. The subprocess is
        spawned only once a concurrency slot is held. Returns the run_id."""
        if self.draining:
            log.info("fire_refused_draining routine=%s reason=%s", cfg.slug, reason)
            return None
        if cfg.slug in self.active:
            log.info("overrun_skipped routine=%s reason=%s", cfg.slug, reason)
            return None
        ts = make_run_ts()
        run_dir = cfg.dir / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        run = ActiveRun(slug=cfg.slug, run_id=f"{cfg.slug}:{ts}", run_ts=ts, run_dir=run_dir,
                        sem=self._sem_for(cfg), background=self.is_background(cfg))
        atomic_write_json(run_dir / "status.json",
                          {"run_id": run.run_id, "state": "queued", "started": ts,
                           "updated": now_iso(), "turn": 0, "question": None,
                           "usage": {"in": 0, "out": 0}})
        self.active[cfg.slug] = run
        asyncio.create_task(self._supervise(run, cfg, reason))
        return run.run_id

    async def resume(self, cfg: RoutineConfig, ts: str, *, reason: str = "resume") -> str | None:
        """Re-run an interrupted (terminal) run in place, rehydrating its transcript so it continues
        where it left off. Refuses if draining, the routine already has an active run, or the run
        dir is gone."""
        if self.draining or cfg.slug in self.active:
            return None
        run_dir = cfg.dir / "runs" / ts
        if not run_dir.is_dir():
            return None
        run = ActiveRun(slug=cfg.slug, run_id=f"{cfg.slug}:{ts}", run_ts=ts, run_dir=run_dir,
                        sem=self._sem_for(cfg), background=self.is_background(cfg))
        atomic_write_json(run_dir / "status.json",
                          {"run_id": run.run_id, "state": "queued", "started": ts,
                           "updated": now_iso(), "turn": 0, "question": None, "usage": {"in": 0, "out": 0}})
        self.active[cfg.slug] = run
        asyncio.create_task(self._supervise(run, cfg, reason, resume=True))
        return run.run_id

    async def _supervise(self, run: ActiveRun, cfg: RoutineConfig, reason: str,
                         resume: bool = False) -> None:
        sem = run.sem or self.semaphore
        await sem.acquire()
        run.holds_slot = True
        stderr = b""
        try:
            run.proc = await asyncio.create_subprocess_exec(
                *engine_cmd(str(cfg.dir), run.run_ts, resume=resume),
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
        self._reap(run, cfg, stderr)

    async def _watch_waiting(self, run: ActiveRun) -> None:
        """A run parked on a blocking question releases its concurrency slot (an idle
        2s-polling process is free); it re-acquires lazily on resume — brief
        oversubscription is accepted, the engine never blocks on it."""
        sem = run.sem or self.semaphore
        while True:
            await asyncio.sleep(STATUS_POLL_S)
            st = read_json(run.run_dir / "status.json")
            state = st.get("state") if isinstance(st, dict) else None
            if state == "waiting_user" and run.holds_slot:
                run.holds_slot = False
                sem.release()
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})
            elif state not in ("waiting_user", None) and not run.holds_slot:
                await sem.acquire()  # cancellation-safe: waiter is discarded
                run.holds_slot = True
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})

    def _llm_recorder(self, run: ActiveRun):
        """Callback for this run's sidecar tailer: attribute each engine LLM record to the run
        (which is its own process in the task manager) and fold it into the center."""
        def _on(rec: dict) -> None:
            rec["run_id"] = run.run_id
            rec.setdefault("process_id", run.run_id)   # engine calls have no scope → the run IS the process
            self.center.ingest(rec)
        return _on

    def _reap(self, run: ActiveRun, cfg: RoutineConfig, stderr: bytes) -> None:
        self.active.pop(run.slug, None)
        rc = run.proc.returncode if run.proc else None
        info = registry.read_run(run.run_dir, run.slug)
        if info.state in ("queued", "running", "waiting_user", "paused", "starting", "unknown"):
            # engine died without closing out (SIGKILL, crash) — the daemon finalizes
            self._close_out(run.run_dir, run.run_id,
                            f"engine exited rc={rc} without a finish "
                            f"({stderr.decode('utf-8', 'replace')[-400:].strip() or 'no stderr'})")
            info = registry.read_run(run.run_dir, run.slug)
        if self.center is not None:
            self.center.close_process(
                run.run_id,
                error=(info.summary[:200] if info.state in ("failed", "aborted") else None))
        self.bus.publish({"event": "run_finished", "routine": run.slug, "run_id": run.run_id,
                          "state": info.state, "summary": info.summary[:300]})
        log.info("run_finished routine=%s run=%s rc=%s state=%s", run.slug, run.run_id, rc, info.state)
        try:
            registry.apply_retention(cfg.dir, cfg.slug, cfg.keep_runs)
        except OSError as exc:
            log.warning("retention failed for %s: %s", cfg.slug, exc)

    def _close_out(self, run_dir: Path, run_id: str, message: str) -> None:
        """Append a synthetic finish to a dead run (single writer: the engine is gone)."""
        try:
            with open(run_dir / "transcript.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": now_iso(), "type": "finish",
                                     "payload": {"status": "failed", "summary": message,
                                                 "authored": False}}) + "\n")
        except OSError:
            pass
        st = read_json(run_dir / "status.json")
        st = st if isinstance(st, dict) else {"run_id": run_id}
        st.update(state="failed", updated=now_iso(), question=None)
        atomic_write_json(run_dir / "status.json", st)
        (run_dir / "result.md").write_text(message + "\n", encoding="utf-8")
        log_health_event(self.server.routines_home, "orphaned_run",
                         routine=run_id.split(":")[0] if ":" in run_id else run_id,
                         run_id=run_id, detail=message[:500])

    async def abort(self, slug: str) -> bool:
        run = self.active.get(slug)
        if not run:
            return False
        if run.proc is None:
            return False  # still queued for a slot — the brief window is not abortable in v1
        return await abort_process(run.proc.pid, run.run_dir, run.run_id)

    def recover_orphans(self, catalog: dict[str, registry.RoutineInfo]) -> int:
        """At boot: any run dir claiming to be alive whose pid is dead gets closed out."""
        fixed = 0
        for info in catalog.values():
            for r in info.runs:
                if r.state in ("queued", "running", "waiting_user", "paused", "starting") \
                        and not _pid_alive(r.pid):
                    self._close_out(r.dir, r.run_id, "orphaned by daemon restart")
                    fixed += 1
                    log.warning("orphan closed: %s", r.run_id)
        return fixed


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def abort_process(pid: int | None, run_dir: Path, run_id: str) -> bool:
    """SIGTERM the engine's process group; SIGKILL stragglers after the grace period."""
    if not pid or not _pid_alive(pid):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    for _ in range(int(KILL_GRACE_S / 0.5)):
        await asyncio.sleep(0.5)
        if not _pid_alive(pid):
            return True
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return True
