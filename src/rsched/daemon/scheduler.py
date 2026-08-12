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
from types import SimpleNamespace

from .. import group_runs as group_runs_store
from .. import groups, registry
from ..config import ServerConfig
from ..ids import now_iso
from ..schedule import server_tz
from . import pause, restart
from .detached import DetachedManager
from .events import EventBus
from .group_runs import GroupRunManager
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
        # Sequential group fires (D53 Phase B): the web layer arms an ordered group's chain
        # durably; this manager advances it one member per tick — fire, wait for terminal,
        # apply the on_failure policy, fire the next (see daemon/group_runs.py).
        self.group_runs = GroupRunManager(server, runner)
        # OAuth token upkeep: refresh expiring connections before they lapse so a run always
        # reads a live token (a no-op for non-expiring providers). See daemon/oauth_refresh.py.
        self.oauth = OAuthRefreshManager(server)
        self.catalog: dict[str, registry.RoutineInfo] = {}
        self.next_fires: dict[str, datetime] = {}
        # D71: groups with a cron of their own. A due group fire ARMS the sequential
        # chain (member 0 fires, the rest chain on completion — GroupRunManager);
        # meanwhile every member of a scheduled group is SUPPRESSED from the routine
        # fire table above, so one fire path exists and nothing double-fires.
        self.scheduled_groups: list[dict] = []
        self.suppressed_members: set[str] = set()
        self.group_next_fires: dict[str, datetime] = {}
        self._last_scan = 0.0
        self._shutting_down = False
        self._deferred_logged = False
        self.started = now_iso()   # process birth — a restart is visible as a changed value

    def rescan(self) -> None:
        self.catalog = registry.scan(self.server)
        now = _now()
        # D71: a member of a SCHEDULED group fires only through the group's chain — its
        # own cron is suppressed while the membership lasts (removing the schedule or
        # leaving the group restores it on the next rescan). Unscheduled groups suppress
        # nothing. Read fresh each rescan, like the catalog.
        self.scheduled_groups = [g for g in groups.list_groups(self.server.routines_home)
                                 if g["cron"]]
        self.suppressed_members = {m for g in self.scheduled_groups
                                   for m in groups.member_slugs(g)}
        fires: dict[str, datetime] = {}
        for slug, info in self.catalog.items():
            if slug in self.suppressed_members:
                continue
            nf = registry.next_fire(info.cfg, now)
            if nf is None:
                continue
            prev = self.next_fires.get(slug)
            # a fire that came due since the last tick is still owed — don't recompute past it
            fires[slug] = prev if (prev is not None and prev <= now) else nf
        self.next_fires = fires
        gfires: dict[str, datetime] = {}
        for g in self.scheduled_groups:
            nf = registry.next_fire(self._group_schedulable(g), now)
            if nf is None:
                continue
            prev = self.group_next_fires.get(g["id"])
            gfires[g["id"]] = prev if (prev is not None and prev <= now) else nf
        self.group_next_fires = gfires

    @staticmethod
    def _group_schedulable(g: dict) -> SimpleNamespace:
        """A group's cron/tz as the Schedulable shape next_fire reads. tz falls back to
        the server zone — groups are saved with the server tz by the web layer, but an
        older or hand-edited row must still fire somewhere sensible.
        """
        # A PAUSED group reads as a disabled schedulable: next_fire yields None, so the
        # group simply leaves the fire table — nothing to skip in the loop, and resuming
        # recomputes a FUTURE fire on rescan (never a backlog of missed ones). An explicit
        # "Run now" / manage_group run still arms the chain: pause gates the cron only.
        return SimpleNamespace(cron=g["cron"], tz=g.get("tz") or server_tz(),
                               enabled=not g.get("paused"))

    async def boot_catchup(self) -> None:
        for slug, info in self.catalog.items():
            if slug in self.suppressed_members:
                # D71: a group-managed member's own cron never fires, catch-up included
                continue
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
                # D71: due GROUP fires — arm the sequential chain; GroupRunManager
                # (ticked below) fires member 0 and chains the rest on completion.
                for gid, due in list(self.group_next_fires.items()):
                    if now < due:
                        continue
                    group = next((g for g in self.scheduled_groups if g["id"] == gid), None)
                    if group is None:
                        self.group_next_fires.pop(gid, None)
                        continue
                    self.group_next_fires[gid] = (
                        registry.next_fire(self._group_schedulable(group), now) or due)
                    if is_paused:
                        log.info("scheduling paused — skipped due group fire of %r", gid)
                        continue
                    rec = group_runs_store.arm(
                        self.server.routines_home, group,
                        default_on_failure=groups.default_on_failure(
                            self.server.routines_home),
                        armed_by="schedule")
                    if rec is None:
                        # the chain overrun rule: a group still mid-chain skips this
                        # fire (the routine analog is Runner.fire's overrun_skipped)
                        log.info("group fire skipped — chain still in flight group=%s", gid)
                    else:
                        log.info("group fire armed group=%s members=%d", gid,
                                 len(rec.get("members") or []))
                # detached background tasks: intake requests, deliver results, wake owners
                await self.detached.tick(now)
                if not is_paused:
                    # event triggers: spooled webhook events → coalesced fires
                    await self.triggers.tick(self.catalog)
                    # one-shot time triggers: due requests → a single fire, then consumed
                    # (paused: intake deferred, so nothing is consumed unfired)
                    await self.oneshots.tick(self.catalog)
                    # sequential group fires: advance each armed chain one member per tick
                    await self.group_runs.tick(self.catalog)
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

    def _maybe_restart(self) -> bool:
        """Drive the graceful self-restart state machine. Returns True when the scheduler
        should fire nothing this tick (draining or shutting down).
        """
        if self._shutting_down:
            return True
        requested = restart.restart_requested(self.server)
        active = self.runner.active_states()
        action = restart.restart_action(requested, active, self.runner.draining)
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
            "group_next_fires": {g: t.isoformat()
                                 for g, t in sorted(self.group_next_fires.items())},
            "draining": self.runner.draining,
            "started": self.started,
            "restart_requested": restart.restart_requested(self.server),
            "paused": pause.paused(self.server),
        }
