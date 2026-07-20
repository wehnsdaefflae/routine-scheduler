"""OneShotManager — turns spooled one-shot requests into a single routine fire.

The web layer / the `schedule_run` engine action arm a one-shot durably (rsched.schedule_once
→ the `.control/schedule-once/<slug>/` request spool); this manager, ticked from the scheduler
beside TriggerManager after the cron-fire loop, is the ONLY thing that turns a due request
into a run — so run spawning, one-run-per-routine, max_concurrent_runs and the restart drain
stay the daemon's job, exactly as for cron and trigger fires.

AUTO-DEACTIVATE = CONSUME: on a successful fire the manager DELETES the request file. The
armed file is gone, so nothing can re-fire it — that IS the non-repeating guarantee (no
routine.yaml rewrite, no self-disabling cron). A make-up fire is free: a `fire_at` already
past at boot is still on disk, so it fires on the first tick (a one-shot's point is that it
*eventually* runs once); `expires_at` bounds that staleness — past it, the request is dropped
instead of fired.

Delivery is crash-safe: the reason is written as a DETERMINISTIC inbox message
(msg-once-<id>.json) before the request is consumed, so a crash between injection and unlink
re-delivers the same file; a crash between injection and fire leaves the message durable in
the inbox — the routine's next run drains it. All state lives on disk; a tick is idempotent
and needs no boot reconcile. Full spec: docs/schedule-once.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .. import schedule_once
from ..config import ServerConfig
from ..ids import now_iso
from ..paths import atomic_write_json
from . import registry
from .runner import Runner

log = logging.getLogger("rsched.schedule_once")


def _parse(value: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class OneShotManager:
    """Owns the request-spool→fire side of one-shot triggers; constructed with the shared
    server + runner and ticked by the Scheduler with its live catalog.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.routines_home

    async def tick(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """One pass over the spool. Never raises into the scheduler loop."""
        try:
            for slug in schedule_once.slugs_with_requests(self.home):
                await self._service(slug, catalog.get(slug))
        except Exception:
            log.exception("schedule-once tick failed")

    async def _service(self, slug: str, info: registry.RoutineInfo | None) -> None:
        paths = schedule_once.pending_requests(self.home, slug)
        if not paths:
            return
        if info is None or not info.cfg.enabled:
            self._drop(slug, paths, "routine missing or disabled")
            return
        now = datetime.now(UTC)
        due: list[tuple[Path, dict, datetime]] = []
        for path in paths:
            rec = schedule_once.read_request(path)
            if not rec or not rec.get("active", True):
                continue
            exp = _parse(rec.get("expires_at")) if rec.get("expires_at") else None
            if exp is not None and exp <= now:
                self._drop(slug, [path], "expired before firing")
                continue
            fire_at = _parse(rec.get("fire_at"))
            if fire_at is None:
                self._drop(slug, [path], "unreadable fire_at")
                continue
            if fire_at <= now:
                due.append((path, rec, fire_at))
        if not due:
            return
        # one-run-per-routine: fire the EARLIEST-due request; any others fire on later ticks
        # once the routine is free again (the one-shot analog of trigger coalescing).
        if self.runner.draining or self.runner.is_active(slug):
            return
        due.sort(key=lambda d: d[2])
        await self._fire(slug, info, due[0][0], due[0][1])

    async def _fire(self, slug: str, info: registry.RoutineInfo, path: Path, rec: dict) -> None:
        # Inject-then-fire with no await between the is_active gate and runner.fire's own
        # re-check: one event loop, so nothing can slip a competing run in between.
        inbox = info.cfg.dir / "inbox"
        atomic_write_json(inbox / f"msg-once-{rec.get('id')}.json",
                          {"text": _fire_text(rec), "ts": now_iso(), "via": "schedule_once"})
        rid = await self.runner.fire(info.cfg, reason="schedule_once")
        # auto-deactivate = consume: the armed file is gone, nothing can re-fire it
        path.unlink(missing_ok=True)  # noqa: ASYNC240 — fast local-FS unlink; the daemon does sync FS I/O in async by design
        now = now_iso()
        state = schedule_once.read_state(self.home, slug)
        state.update(last_fired=now, fires=int(state.get("fires") or 0) + 1)
        schedule_once.write_state(self.home, slug, state)
        if rid:
            log.info("schedule-once fired routine=%s run=%s req=%s", slug, rid, rec.get("id"))
        else:
            log.error("schedule-once fire refused routine=%s req=%s — reason already injected "
                      "as an inbox message; the next run picks it up", slug, rec.get("id"))

    @staticmethod
    def _drop(slug: str, paths: list[Path], reason: str) -> None:
        for p in paths:
            p.unlink(missing_ok=True)
        log.warning("schedule-once requests dropped routine=%s count=%d (%s)",
                    slug, len(paths), reason)


def _fire_text(rec: dict) -> str:
    """The injected user-message text: a one-line provenance head + the arming reason."""
    head = (f"[scheduled-once fire] armed by {rec.get('requested_by') or 'unknown'} "
            f"({rec.get('created')})")
    reason = str(rec.get("reason") or "").strip()
    return f"{head}:\n\n{reason}" if reason else f"{head}."
