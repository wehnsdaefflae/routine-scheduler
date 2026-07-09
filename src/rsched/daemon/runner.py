"""Engine subprocess management: spawn, track, abort, reap, retention, orphan recovery.

One engine process per run (`python -m rsched.cli engine-run <slug> --run-ts <ts>` in this
venv), its own process group. The global semaphore counts starting+running processes; a
run parked in waiting_user releases its slot (the daemon polls status.json cheaply).
"""

from __future__ import annotations

import asyncio
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
from . import registry
from .events import EventBus

log = logging.getLogger("rsched.runner")

KILL_GRACE_S = 10
STATUS_POLL_S = 2.0


def engine_cmd(slug: str, run_ts: str) -> list[str]:
    return [sys.executable, "-m", "rsched.cli", "engine-run", slug, "--run-ts", run_ts]


@dataclass
class ActiveRun:
    slug: str
    run_id: str
    run_ts: str
    run_dir: Path
    proc: asyncio.subprocess.Process | None = None  # None while queued for a slot
    holds_slot: bool = False


class Runner:
    def __init__(self, server: ServerConfig, bus: EventBus):
        self.server = server
        self.bus = bus
        self.semaphore = asyncio.Semaphore(server.max_concurrent_runs)
        self.active: dict[str, ActiveRun] = {}  # slug → run
        self.draining = False  # set while quiescing for a self-update restart: no new runs fire

    def is_active(self, slug: str) -> bool:
        return slug in self.active

    def active_states(self) -> list[str]:
        """Current state of each active run (read from status.json) — for the drain check."""
        states: list[str] = []
        for run in self.active.values():
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
        run = ActiveRun(slug=cfg.slug, run_id=f"{cfg.slug}:{ts}", run_ts=ts, run_dir=run_dir)
        atomic_write_json(run_dir / "status.json",
                          {"run_id": run.run_id, "state": "queued", "started": ts,
                           "updated": now_iso(), "turn": 0, "question": None,
                           "usage": {"in": 0, "out": 0}})
        self.active[cfg.slug] = run
        asyncio.create_task(self._supervise(run, cfg, reason))
        return run.run_id

    async def _supervise(self, run: ActiveRun, cfg: RoutineConfig, reason: str) -> None:
        await self.semaphore.acquire()
        run.holds_slot = True
        stderr = b""
        try:
            run.proc = await asyncio.create_subprocess_exec(
                *engine_cmd(cfg.slug, run.run_ts),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                cwd=str(cfg.dir),
            )
            self.bus.publish({"event": "run_started", "routine": cfg.slug,
                              "run_id": run.run_id, "reason": reason})
            log.info("run_started routine=%s run=%s pid=%s reason=%s",
                     cfg.slug, run.run_id, run.proc.pid, reason)
            waiter = asyncio.create_task(self._watch_waiting(run))
            try:
                _, err = await run.proc.communicate()
                stderr = err or b""
            finally:
                waiter.cancel()
        finally:
            if run.holds_slot:
                self.semaphore.release()
                run.holds_slot = False
        self._reap(run, cfg, stderr)

    async def _watch_waiting(self, run: ActiveRun) -> None:
        """A run parked on a blocking question releases its concurrency slot (an idle
        2s-polling process is free); it re-acquires lazily on resume — brief
        oversubscription is accepted, the engine never blocks on it."""
        while True:
            await asyncio.sleep(STATUS_POLL_S)
            st = read_json(run.run_dir / "status.json")
            state = st.get("state") if isinstance(st, dict) else None
            if state == "waiting_user" and run.holds_slot:
                run.holds_slot = False
                self.semaphore.release()
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})
            elif state not in ("waiting_user", None) and not run.holds_slot:
                await self.semaphore.acquire()  # cancellation-safe: waiter is discarded
                run.holds_slot = True
                self.bus.publish({"event": "run_state", "routine": run.slug,
                                  "run_id": run.run_id, "state": state})

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
