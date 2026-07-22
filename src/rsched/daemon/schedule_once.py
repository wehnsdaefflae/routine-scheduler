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

from .. import registry, schedule_once
from ..config import ServerConfig
from ..ids import now_iso
from ..paths import atomic_write_json
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
        """One pass over the spool. Never raises into the scheduler loop. `conv--<slug>`
        entries (a conversation's self-armed one-shots — engine/interact.handle_schedule_run
        namespaces them so a same-named routine can never be mis-fired) resolve to
        conversations_home and wake the conversation by RESUME, like a detached delivery.
        """
        try:
            for slug in schedule_once.slugs_with_requests(self.home):
                if slug.startswith("conv--"):
                    await self._service_conversation(slug)
                else:
                    await self._service(slug, catalog.get(slug))
        except Exception:
            log.exception("schedule-once tick failed")

    def _due_request(self, slug: str, paths: list[Path]) -> tuple[Path, dict] | None:
        """The earliest-due armed request, dropping corrupt/expired files as it goes (an
        unreadable request would otherwise be rescanned every 5s forever).
        """
        now = datetime.now(UTC)
        due: list[tuple[Path, dict, datetime]] = []
        for path in paths:
            rec = schedule_once.read_request(path)
            if not rec:
                self._drop(slug, [path], "unreadable request file")
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
            return None
        due.sort(key=lambda d: d[2])
        return due[0][0], due[0][1]

    async def _service(self, slug: str, info: registry.RoutineInfo | None) -> None:
        paths = schedule_once.pending_requests(self.home, slug)
        if not paths:
            return
        if info is None or not info.cfg.enabled:
            self._drop(slug, paths, "routine missing or disabled")
            return
        # one-run-per-routine: fire the EARLIEST-due request; any others fire on later ticks
        # once the routine is free again (the one-shot analog of trigger coalescing).
        first = self._due_request(slug, paths)
        if first is None or self.runner.draining or self.runner.is_active(slug):
            return
        await self._fire(slug, info.cfg, first[0], first[1], resume_ts=None)

    async def _service_conversation(self, spool_slug: str) -> None:
        paths = schedule_once.pending_requests(self.home, spool_slug)
        if not paths:
            return
        from ..config import load_routine

        conv_dir = self.server.conversations_home / spool_slug.removeprefix("conv--")
        cfg = load_routine(conv_dir)[0] if (conv_dir / "routine.yaml").is_file() else None
        if cfg is None:
            self._drop(spool_slug, paths, "conversation missing")
            return
        first = self._due_request(spool_slug, paths)
        if first is None or self.runner.draining or self.runner.is_active(cfg.slug):
            return
        # a conversation continues its ONE run in place (finish-per-reply): wake it by
        # resuming the latest run; a conversation with no run yet fires fresh
        runs = sorted((conv_dir / "runs").iterdir()) if (conv_dir / "runs").is_dir() else []
        await self._fire(spool_slug, cfg, first[0], first[1],
                         resume_ts=runs[-1].name if runs else None)

    async def _fire(self, slug: str, cfg, path: Path, rec: dict,
                    *, resume_ts: str | None) -> None:
        # Inject-then-fire with no await between the is_active gate and runner.fire's own
        # re-check: one event loop, so nothing can slip a competing run in between.
        inbox = cfg.dir / "inbox"
        atomic_write_json(inbox / f"msg-once-{rec.get('id')}.json",
                          {"text": _fire_text(rec), "ts": now_iso(), "via": "schedule_once"})
        if resume_ts:
            rid = await self.runner.resume(cfg, resume_ts, reason="schedule_once")
        else:
            rid = await self.runner.fire(cfg, reason="schedule_once")
        # auto-deactivate = consume: the armed file is gone, nothing can re-fire it
        path.unlink(missing_ok=True)
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
