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
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from .. import lane_runs as lane_runs_store
from .. import lanes, registry
from ..config import ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..schedule import server_tz
from . import pause, restart, runner_reap
from .detached import DetachedManager
from .events import EventBus
from .lane_runs import LaneRunManager
from .library_watch import LibraryWatch
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
        # Sequential lane fires (D53 Phase B): the web layer arms an ordered lane's chain
        # durably; this manager advances it one member per tick — fire, wait for terminal,
        # apply the on_failure policy, fire the next (see daemon/lane_runs.py).
        self.lane_runs = LaneRunManager(server, runner)
        # OAuth token upkeep: refresh expiring connections before they lapse so a run always
        # reads a live token (a no-op for non-expiring providers). See daemon/oauth_refresh.py.
        self.oauth = OAuthRefreshManager(server)
        # The library MOVES under its holders — a sync pull, a hand edit, a restored
        # bundle — with no writer to gate. This notices and files the breakage as a
        # decision (daemon/library_watch.py).
        self.library = LibraryWatch(server)
        self.catalog: dict[str, registry.RoutineInfo] = {}
        self.next_fires: dict[str, datetime] = {}
        # D71: lanes with a cron of their own. A due lane fire ARMS the sequential
        # chain (member 0 fires, the rest chain on completion — LaneRunManager);
        # meanwhile every member of a scheduled lane is SUPPRESSED from the routine
        # fire table above, so one fire path exists and nothing double-fires. A routine
        # belongs to at most one lane (rsched.lanes), which is what makes "one fire path"
        # a fact rather than a hope.
        self.scheduled_lanes: list[dict] = []
        self.suppressed_members: set[str] = set()
        self.lane_next_fires: dict[str, datetime] = {}
        self._last_scan = 0.0
        self._shutting_down = False
        self._deferred_logged = False
        # A pending restart waits for a quiet gap instead of blocking new runs (restart.py): the
        # monotonic stamp of when runner.active last emptied, or None while a run is active.
        self._idle_since: float | None = None
        self.started = now_iso()   # process birth — a restart is visible as a changed value

    def rescan(self) -> None:
        self.catalog = registry.scan(self.server)
        now = _now()
        # D71: a member of a SCHEDULED lane fires only through the lane's chain — its
        # own cron is suppressed while the membership lasts (removing the schedule or
        # leaving the lane restores it on the next rescan). Unscheduled lanes suppress
        # nothing. Read fresh each rescan, like the catalog.
        self.scheduled_lanes = [lane for lane in lanes.list_lanes(self.server.routines_home)
                                if lane["cron"]]
        self.suppressed_members = {slug for lane in self.scheduled_lanes
                                   for slug in lanes.member_slugs(lane)}
        fires: dict[str, datetime] = {}
        for slug, info in self.catalog.items():
            if slug in self.suppressed_members:
                continue
            if info.retired:
                # Every goal-scoped stopping condition is met: the routine is finished, so it
                # gets no fire table entry at all. Derived from its own goal document, never
                # written — clearing a goal condition puts it back on the next rescan, and
                # `enabled` is untouched. A retirement proposal is waiting on the Decisions
                # page to make it permanent (engine/goalreached.py).
                continue
            nf = registry.next_fire(info.cfg, now)
            if nf is None:
                continue
            prev = self.next_fires.get(slug)
            # a fire that came due since the last tick is still owed — don't recompute past it
            fires[slug] = prev if (prev is not None and prev <= now) else nf
        self.next_fires = fires
        lane_fires: dict[str, datetime] = {}
        for lane in self.scheduled_lanes:
            nf = registry.next_fire(self._lane_schedulable(lane), now)
            if nf is None:
                continue
            prev = self.lane_next_fires.get(lane["id"])
            lane_fires[lane["id"]] = prev if (prev is not None and prev <= now) else nf
        self.lane_next_fires = lane_fires

    @staticmethod
    def _lane_schedulable(lane: dict) -> SimpleNamespace:
        """A lane's cron/tz as the Schedulable shape next_fire reads. tz falls back to
        the server zone — lanes are saved with the server tz by the web layer, but an
        older or hand-edited row must still fire somewhere sensible.
        """
        # A PAUSED lane reads as a disabled schedulable: next_fire yields None, so the
        # lane simply leaves the fire table — nothing to skip in the loop. Resuming
        # recomputes a FUTURE fire on rescan (never a backlog of missed ones). An explicit
        # "Run now" / manage_lane run still arms the chain: pause gates the cron only.
        return SimpleNamespace(cron=lane["cron"], tz=lane.get("tz") or server_tz(),
                               enabled=not lane.get("paused"))

    async def boot_catchup(self) -> None:
        for slug, info in self.catalog.items():
            if slug in self.suppressed_members:
                # D71: a lane-managed member's own cron never fires, catch-up included
                continue
            if info.retired:
                continue     # a finished routine has no missed fire worth making up
            missed = registry.missed_fire(info.cfg, info.runs, _now())
            if missed is not None:
                log.info("catchup routine=%s missed_fire=%s → one make-up run", slug, missed)
                await self.runner.fire(info.cfg, reason="catchup")

    async def run_forever(self) -> None:
        self.rescan()
        fixed = runner_reap.recover_orphans(self.runner, self.catalog)
        # conversations live outside the schedule but their runs can be orphaned all the same
        runner_reap.recover_orphans(
            self.runner, registry.scan(self.server, self.server.conversations_home))
        # detached background tasks too — then the manager re-attempts any undelivered results
        runner_reap.recover_orphans(
            self.runner, registry.scan(self.server, self.server.background_home))
        await self.detached.reconcile()
        # crashed runs leave sshfs key dirs behind (clean exits remove their own)
        from ..machine_mounts import sweep_stale_mount_keys
        sweep_stale_mount_keys()
        self._refresh_limits()
        self._refresh_machine_queues()
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
                # D71: due LANE fires — arm the sequential chain; LaneRunManager
                # (ticked below) fires member 0 and chains the rest on completion.
                for lane_id, due in list(self.lane_next_fires.items()):
                    if now < due:
                        continue
                    lane = next((x for x in self.scheduled_lanes if x["id"] == lane_id), None)
                    if lane is None:
                        self.lane_next_fires.pop(lane_id, None)
                        continue
                    self.lane_next_fires[lane_id] = (
                        registry.next_fire(self._lane_schedulable(lane), now) or due)
                    if is_paused:
                        log.info("scheduling paused — skipped due lane fire of %r", lane_id)
                        continue
                    rec = lane_runs_store.arm(
                        self.server.routines_home, lane,
                        default_on_failure=lanes.default_on_failure(
                            self.server.routines_home),
                        armed_by="schedule")
                    if rec is None:
                        # the chain overrun rule: a lane still mid-chain skips this
                        # fire (the routine analog is Runner.fire's overrun_skipped)
                        log.info("lane fire skipped — chain still in flight lane=%s", lane_id)
                        log_health_event(
                            self.server.routines_home, "lane_fire_refused",
                            routine=lane_id, run_id="",
                            detail="due scheduled lane fire skipped - previous chain "
                                   "still in flight (a wedged chain starves every later fire)")
                    else:
                        log.info("lane fire armed lane=%s members=%d", lane_id,
                                 len(rec.get("members") or []))
                self._refresh_limits()
                self._refresh_machine_queues()
                # detached background tasks: intake requests, deliver results, wake owners
                await self.detached.tick(now)
                if not is_paused:
                    # event triggers: spooled webhook events → coalesced fires
                    await self.triggers.tick(self.catalog)
                    # one-shot time triggers: due requests → a single fire, then consumed
                    # (paused: intake deferred, so nothing is consumed unfired)
                    await self.oneshots.tick(self.catalog)
                    # sequential lane fires: advance each armed chain one member per tick
                    await self.lane_runs.tick(self.catalog)
                # OAuth token upkeep: refresh expiring connections nearing their deadline
                await self.oauth.tick()
                await self.library.tick()
            except _TickSkip:
                continue  # draining / shutting down: fire nothing this tick
            except Exception:
                log.exception("scheduler tick failed — continuing")
                try:
                    log_health_event(self.server.routines_home, "scheduler_tick_error",
                                     routine="(daemon)", run_id="",
                                     detail="scheduler tick raised; see daemon log")
                except Exception:  # the guard itself must never take the loop down
                    pass

    def _refresh_machine_queues(self) -> None:
        """Mirror every EXCLUSIVE machine's job queue (rsched/machine_queue.py) so the prompt and
        the console can read it without an SSH round-trip per reader. Off the tick in a thread and
        never fatal: a GPU box being down must not stop the scheduler, and a stale mirror reads as
        UNKNOWN rather than as a free machine — which is the one failure mode that would cause the
        collision this mechanism exists to prevent.
        """
        from ..machine_queue import refresh as refresh_queues

        if not any(m.exclusive for m in (self.server.machines or {}).values()):
            return

        async def go() -> None:
            try:
                await asyncio.to_thread(refresh_queues, self.server)
            except Exception as exc:
                log.warning("machine queue refresh failed: %s", exc)

        asyncio.ensure_future(go())   # noqa: RUF006 — fire-and-forget by design

    def _refresh_limits(self) -> None:
        """Re-ask each provider what its models' real limits are, behind a 24h TTL
        (endpoints/limits.py). Off the tick's critical path in a thread, and never fatal: this
        is a convenience that replaces hand-entered guesses, so a provider being down means the
        previous figures stand.

        Reading provider METADATA is not an outbound message, so the 0.230.0 ban on
        engine/daemon-implicit sends does not reach it — nothing is told anything, and the
        result is derived state under `.control/`, never config.
        """
        from ..endpoints import limits

        if not limits.stale(self.server):
            return

        async def go() -> None:
            try:
                out = await asyncio.to_thread(limits.refresh, self.server)
                log.info("model limits refreshed: %d known, %d not listed by their provider",
                         out["written"], len(out["misses"]))
            except Exception as exc:
                log.warning("model limits refresh failed: %s", exc)

        asyncio.ensure_future(go())   # noqa: RUF006 — fire-and-forget by design

    def _tick_once(self, loop) -> None:
        """The tick preamble: restart state machine, then a due registry rescan. Raises
        _TickSkip when the restart machine says to fire nothing this tick.
        """
        if self._maybe_restart():
            raise _TickSkip
        if loop.time() - self._last_scan >= self.server.registry_rescan_s:
            self.rescan()
            self._last_scan = loop.time()

    def _maybe_restart(self) -> bool:
        """Drive the graceful self-restart state machine. Returns True only when the scheduler
        should fire nothing this tick — i.e. it is shutting down. A pending restart NEVER blocks
        starting a run or conversation while it waits (operator, 2026-09-03): it keeps scheduling
        normally and fires only once the system has been idle for restart.RESTART_IDLE_S.
        """
        if self._shutting_down:
            return True
        requested = restart.restart_requested(self.server)
        active = self.runner.active_states()
        # How long nothing has been active — the quiet gap a pending restart waits for.
        now = time.monotonic()
        if active:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now
        idle_long_enough = (self._idle_since is not None
                            and now - self._idle_since >= restart.RESTART_IDLE_S)
        action = restart.restart_action(requested, active, idle_long_enough)
        if action == "idle":
            if self.runner.draining:   # a request withdrawn in the SIGTERM window — undo the gate
                self.runner.draining = False
            self._deferred_logged = False
            return False
        if action in ("defer", "wait"):
            if not self._deferred_logged:
                log.info("restart requested — scheduling normally until idle for %ds, then "
                         "restarting (a restart never blocks starting a run or conversation)",
                         restart.RESTART_IDLE_S)
                self._deferred_logged = True
            return False   # keep firing: a pending restart never blocks a start
        # action == "restart": idle long enough — shut down so the supervisor relaunches new code
        self.runner.draining = True   # refuse a fire racing the SIGTERM window
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
            "lane_next_fires": {lane_id: t.isoformat()
                                for lane_id, t in sorted(self.lane_next_fires.items())},
            "draining": self.runner.draining,
            "started": self.started,
            "restart_requested": restart.restart_requested(self.server),
            "paused": pause.paused(self.server),
        }
