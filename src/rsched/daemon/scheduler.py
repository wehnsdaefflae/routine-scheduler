"""The cron scheduler: derives its fire table live from the routine catalog.

Every tick (5s) it checks due fires; every registry_rescan_s it rescans ~/routines (so
edits to routine.yaml — schedule changes, enable/disable — take effect without restarts).
Catch-up (`run_once`) is evaluated exactly once, at daemon boot. A fire that finds its
routine still running is skipped and logged (`overrun_skipped`, inside Runner.fire).
Event triggers ride the same tick: spooled webhook events become coalesced fires — the
trigger analog of the overrun rule is that events QUEUE instead of being skipped
(daemon/triggers.py, docs/triggers.md).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .. import library_sync, registry
from ..config import ServerConfig
from ..ids import now_iso
from . import pause, restart
from .detached import DetachedManager
from .events import EventBus
from .oauth_refresh import OAuthRefreshManager
from .runner import Runner
from .schedule_once import OneShotManager
from .triggers import TriggerManager

log = logging.getLogger("rsched.scheduler")

TICK_S = 5.0


class _TickSkip(Exception):  # noqa: N818 — a control-flow signal, not an error condition
    """Control flow only: the restart state machine says fire nothing this tick."""


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
        # Event triggers (webhooks today): the web layer only spools events durably; this
        # manager turns them into coalesced fires at the tick (see daemon/triggers.py).
        self.triggers = TriggerManager(server, runner)
        # One-shot time triggers: a request spool the web layer / the schedule_run action arm;
        # this manager fires each due request ONCE then consumes it (see daemon/schedule_once.py).
        self.oneshots = OneShotManager(server, runner)
        # OAuth token upkeep: refresh expiring connections before they lapse so a run always
        # reads a live token (a no-op for non-expiring providers). See daemon/oauth_refresh.py.
        self.oauth = OAuthRefreshManager(server)
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
        nf = registry.next_fire(self.server.library_sync, now)
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
        # crashed runs leave sshfs key dirs behind (clean exits remove their own)
        from ..machines import sweep_stale_mount_keys
        sweep_stale_mount_keys()
        if fixed:
            self.rescan()
        await self.boot_catchup()
        loop = asyncio.get_event_loop()
        self._last_scan = loop.time()
        log.info("scheduler up: %d routines, next fires: %s", len(self.catalog),
                 {s: t.isoformat(timespec="minutes") for s, t in self.next_fires.items()})
        while True:
            await asyncio.sleep(TICK_S)
            # One bad tick must never kill scheduling for good: an exception anywhere in
            # the tick body (a tz typo surfacing in next_fire, a disk-full stat, an sshfs
            # blip) used to unwind run_forever silently while the web UI kept serving —
            # the daemon looked alive with its heart stopped. Log it, flag it, keep
            # ticking. (CancelledError is a BaseException and still propagates.)
            try:
                self._tick_once(loop)
                now = _now()
                # global pause (D34): skip our own fires, but keep ADVANCING the fire
                # table — resuming must not backlog-fire everything that came due.
                is_paused = pause.paused(self.server)
                for slug, due in list(self.next_fires.items()):
                    if now < due:
                        continue
                    info = self.catalog.get(slug)
                    if info is None:
                        self.next_fires.pop(slug, None)
                        continue
                    self.next_fires[slug] = registry.next_fire(info.cfg, now) or due
                    if is_paused:
                        log.info("scheduling paused — skipped due fire of %r", slug)
                        continue
                    await self.runner.fire(info.cfg, reason="schedule")
                if self.sync_next is not None and now >= self.sync_next:
                    self.sync_next = registry.next_fire(self.server.library_sync, now)
                    self._fire_library_sync()
                # detached background tasks: intake requests, deliver results, wake owners
                await self.detached.tick(now)
                if not is_paused:
                    # event triggers: spooled webhook events → coalesced fires
                    await self.triggers.tick(self.catalog)
                    # one-shot time triggers: due requests → a single fire, then consumed
                    # (paused: intake deferred, so nothing is consumed unfired)
                    await self.oneshots.tick(self.catalog)
                # OAuth token upkeep: refresh expiring connections nearing their deadline
                await self.oauth.tick()
            except _TickSkip:
                continue  # draining / shutting down: fire nothing this tick
            except Exception:
                log.exception("scheduler tick failed — continuing")
                try:
                    from ..health_events import log_health_event
                    log_health_event(self.server.routines_home, "scheduler_tick_error",
                                     routine="(daemon)", run_id="",
                                     detail="scheduler tick raised; see daemon log")
                except Exception:  # the guard itself must never take the loop down
                    pass

    def _tick_once(self, loop) -> None:
        """The tick preamble: restart state machine, then a due registry rescan. Raises
        _TickSkip when the restart machine says to fire nothing this tick.
        """
        if self._maybe_restart():
            raise _TickSkip
        if loop.time() - self._last_scan >= self.server.registry_rescan_s:
            self.rescan()
            self._last_scan = loop.time()

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
            "paused": pause.paused(self.server),
            "library_sync_next": self.sync_next.isoformat() if self.sync_next else None,
        }
