"""The cron scheduler: derives its fire table live from the routine catalog.

Every tick (5s) it checks due fires; every registry_rescan_s it rescans ~/routines (so
edits to routine.yaml — schedule changes, enable/disable — take effect without restarts).
Catch-up (`run_once`) is evaluated exactly once, at daemon boot. A fire that finds its
routine still running is skipped and logged (`overrun_skipped`, inside Runner.fire).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .. import library_sync
from ..config import ServerConfig
from ..ids import now_iso
from . import registry, restart
from .detached import DetachedManager
from .events import EventBus
from .runner import Runner

log = logging.getLogger("rsched.scheduler")

TICK_S = 5.0


def _now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    """The cron heart: rescans the catalog, computes next fires (croniter, per-routine
    tz + catchup policy), hands due routines to the Runner, and snapshots its state for
    the UI.
    """

    def __init__(self, server: ServerConfig, runner: Runner, bus: EventBus):
        self.server = server
        self.runner = runner
        self.bus = bus
        # Detached background tasks (the `detach` action): daemon-managed processes that outlive
        # a conversation reply and report back on completion. The manager is the single writer of
        # background_home; it is ticked after the cron-fire loop (paused during a restart drain).
        self.detached = DetachedManager(server, runner)
        self.catalog: dict[str, registry.RoutineInfo] = {}
        self.next_fires: dict[str, datetime] = {}
        # In-flight new-routine wizard builds (wids), registered by api_wizard.finalize and
        # cleared when the background build ends. The restart drain waits for these to empty
        # too, so a self-restart never strands a half-scaffolded routine (see restart.py).
        self.wizard_builds: set[str] = set()
        # the library-sync job (plain, not a routine) rides the same cron machinery
        self.sync_next: datetime | None = None
        self._sync_task: asyncio.Task | None = None
        self._last_scan = 0.0
        self._shutting_down = False
        self._deferred_logged = False
        self.started = now_iso()   # process birth — a restart is visible as a changed value

    def rescan(self) -> None:
        self.catalog = registry.scan(self.server)
        now = _now()
        fires: dict[str, datetime] = {}
        for slug, info in self.catalog.items():
            nf = registry.next_fire(info.cfg, now)
            if nf is None:
                continue
            prev = self.next_fires.get(slug)
            # a fire that came due since the last tick is still owed — don't recompute past it
            fires[slug] = prev if (prev is not None and prev <= now) else nf
        self.next_fires = fires
        # library sync: LibrarySyncConfig carries the same enabled/cron/tz trio next_fire reads
        nf = registry.next_fire(self.server.library_sync, now)  # type: ignore[arg-type]
        self.sync_next = self.sync_next if (nf is not None and self.sync_next is not None
                                            and self.sync_next <= now) else nf

    async def boot_catchup(self) -> None:
        for slug, info in self.catalog.items():
            missed = registry.missed_fire(info.cfg, info.runs, _now())
            if missed is not None:
                log.info("catchup routine=%s missed_fire=%s → one make-up run", slug, missed)
                await self.runner.fire(info.cfg, reason="catchup")

    async def run_forever(self) -> None:
        self.rescan()
        fixed = self.runner.recover_orphans(self.catalog)
        # conversations live outside the schedule but their runs can be orphaned all the same
        self.runner.recover_orphans(registry.scan(self.server, self.server.conversations_home))
        # detached background tasks too — then the manager re-attempts any undelivered results
        self.runner.recover_orphans(registry.scan(self.server, self.server.background_home))
        await self.detached.reconcile()
        if fixed:
            self.rescan()
        await self.boot_catchup()
        loop = asyncio.get_event_loop()
        self._last_scan = loop.time()
        log.info("scheduler up: %d routines, next fires: %s", len(self.catalog),
                 {s: t.isoformat(timespec="minutes") for s, t in self.next_fires.items()})
        while True:
            await asyncio.sleep(TICK_S)
            if self._maybe_restart():
                continue  # draining / shutting down: fire nothing this tick
            if loop.time() - self._last_scan >= self.server.registry_rescan_s:
                self.rescan()
                self._last_scan = loop.time()
            now = _now()
            for slug, due in list(self.next_fires.items()):
                if now < due:
                    continue
                info = self.catalog.get(slug)
                if info is None:
                    self.next_fires.pop(slug, None)
                    continue
                self.next_fires[slug] = registry.next_fire(info.cfg, now) or due
                await self.runner.fire(info.cfg, reason="schedule")
            if self.sync_next is not None and now >= self.sync_next:
                self.sync_next = registry.next_fire(self.server.library_sync, now)  # type: ignore[arg-type]
                self._fire_library_sync()
            # detached background tasks: intake new requests, deliver finished ones, wake owners
            await self.detached.tick(now)

    def _fire_library_sync(self) -> None:
        """Run the sync off-loop (git talks to the network); one at a time — an overrun
        skips the fire like a still-running routine does.
        """
        if self._sync_task is not None and not self._sync_task.done():
            log.info("library sync still running — skipping this fire")
            return

        async def _job() -> None:
            result = await asyncio.to_thread(library_sync.run_sync, self.server)
            self.bus.publish({"event": "library_sync", "status": result.get("status", "error")})

        self._sync_task = asyncio.create_task(_job())

    def _maybe_restart(self) -> bool:
        """Drive the graceful self-restart state machine. Returns True when the scheduler
        should fire nothing this tick (draining or shutting down).
        """
        if self._shutting_down:
            return True
        requested = restart.restart_requested(self.server)
        active = self.runner.active_states()
        if requested:
            # in-flight clarify runs (dot-hidden, invisible to the runner) hold the restart
            # exactly like ordinary runs: waiting_user defers it, running drains it
            active = active + restart.clarify_states(self.server)
        action = restart.restart_action(
            requested, active, self.runner.draining, len(self.wizard_builds))
        if action == "idle":
            if self.runner.draining:
                log.info("restart request withdrawn — resuming normal scheduling")
                self.runner.draining = False
            self._deferred_logged = False
            return False
        if action == "defer":
            if not self._deferred_logged:
                log.info("restart requested, but a run is parked (waiting_user/paused) — deferring")
                self._deferred_logged = True
            return False  # not draining: keep scheduling normally until cleanly drainable
        if action == "drain":
            if not self.runner.draining:
                log.warning("restart requested — draining: no new runs will start "
                            "until active ones finish")
                self.runner.draining = True
            return True
        # action == "restart": drained, nothing active
        self.runner.draining = True
        self._shutting_down = True
        restart.clear_request(self.server)
        restart.trigger_shutdown()
        return True

    def snapshot(self) -> dict:
        """For /api/status and the dashboard."""
        return {
            "routines": len(self.catalog),
            "active_runs": {slug: run.run_id for slug, run in self.runner.active.items()},
            "next_fires": {s: t.isoformat() for s, t in sorted(self.next_fires.items())},
            "draining": self.runner.draining,
            "started": self.started,
            "restart_requested": restart.restart_requested(self.server),
            "library_sync_next": self.sync_next.isoformat() if self.sync_next else None,
        }
