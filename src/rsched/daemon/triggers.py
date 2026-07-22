"""TriggerManager — turns spooled trigger events into routine fires.

The web layer records webhook events durably (rsched.triggers.write_event → the
`.control/triggers/<slug>/` spool); this manager, ticked from the scheduler after the
cron-fire loop, is the ONLY thing that turns them into runs — so run spawning, the
one-run-per-routine rule, max_concurrent_runs and the restart drain stay the daemon's
job, exactly as for cron fires. A trigger fire draws from the normal cron slot pool and
holds a restart drain like any scheduled run.

COALESCING (the trigger analog of the catchup/overrun rules — docs/triggers.md): events
wait in the spool while the routine has an active/queued run, while the daemon drains
for a restart, or inside the trigger's cooldown window — and however many piled up, the
next fire is ONE run. Every coalesced event still lands as its OWN inbox message
immediately before that fire, so no payload is lost. Events whose trigger (or routine)
was deleted or disabled after arrival are dropped with a log line.

Delivery is crash-safe, not transactional: each event becomes a DETERMINISTIC inbox
filename (msg-trig-<event>.json) before its spool file is unlinked, so a crash between
the two re-delivers the same file; a crash between injection and fire leaves the
messages durable in the inbox — the routine's next run (cron, manual, or the next
trigger event) drains them. All state lives on disk; a tick is idempotent and needs no
boot reconcile.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .. import registry, triggers
from ..config import ServerConfig
from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from .runner import Runner

log = logging.getLogger("rsched.triggers")


class TriggerManager:
    """Owns the spool→fire side of event triggers; constructed with the shared server +
    runner and ticked by the Scheduler with its live catalog.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.routines_home

    async def tick(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """One pass over the spool. Never raises into the scheduler loop."""
        try:
            for slug in triggers.slugs_with_events(self.home):
                await self._service(slug, catalog.get(slug))
        except Exception:
            log.exception("trigger tick failed")

    async def _service(self, slug: str, info: registry.RoutineInfo | None) -> None:
        events = triggers.pending_events(self.home, slug)
        if not events:
            return
        if info is None or not info.cfg.enabled:
            self._drop(slug, events, "routine missing or disabled")
            return
        configured = {str(t["id"]): t for t in info.cfg.triggers
                      if t.get("type") == "webhook"}
        live: list[tuple[Path, dict]] = []
        stale: list[Path] = []
        for path in events:
            ev = read_json(path)
            if isinstance(ev, dict) and str(ev.get("trigger")) in configured:
                live.append((path, ev))
            else:
                stale.append(path)
        if stale:
            self._drop(slug, stale, "trigger deleted or event unreadable")
        if not live:
            return
        # coalesce: the spool holds the events; ONE fire once the routine is free again
        if self.runner.draining or self.runner.is_active(slug):
            return
        state = triggers.read_state(self.home, slug)
        raw_per = state.get("triggers")
        per: dict = raw_per if isinstance(raw_per, dict) else {}
        # cooldown is PER TRIGGER (docs/triggers.md): an event whose own trigger is still
        # cooling stays spooled for a later tick; a sibling trigger's events fire now.
        ready = [(path, ev) for path, ev in live
                 if not self._cooling(
                     per.get(str(ev.get("trigger"))) or {},
                     int(configured[str(ev.get("trigger"))].get("cooldown_s") or 0))]
        if not ready:
            return
        await self._fire(slug, info, ready, state)

    @staticmethod
    def _cooling(trigger_state: dict, cooldown_s: int) -> bool:
        last = str(trigger_state.get("last_fired") or "")
        if not last or cooldown_s <= 0:
            return False
        try:
            fired = datetime.fromisoformat(last)
        except ValueError:
            return False
        return (datetime.now(UTC) - fired).total_seconds() < cooldown_s

    async def _fire(self, slug: str, info: registry.RoutineInfo,
                    live: list[tuple[Path, dict]], state: dict) -> None:
        # Inject-then-fire with no await between the is_active gate and runner.fire's own
        # re-check: one event loop, so nothing can slip a competing run in between — the
        # injected messages can only be drained by THIS fire.
        inbox = info.cfg.dir / "inbox"
        for path, ev in live:
            atomic_write_json(inbox / f"msg-trig-{path.stem}.json",
                              {"text": _event_text(ev), "ts": now_iso(), "via": "trigger"})
            path.unlink(missing_ok=True)
        rid = await self.runner.fire(info.cfg, reason="trigger")
        now = now_iso()
        raw_per = state.get("triggers")
        per: dict = raw_per if isinstance(raw_per, dict) else {}
        for _, ev in live:
            tid = str(ev.get("trigger"))
            got = per.get(tid)
            mine: dict = got if isinstance(got, dict) else {}
            per[tid] = {"last_fired": now, "events": int(mine.get("events") or 0) + 1}
        state.update(last_fired=now, fires=int(state.get("fires") or 0) + 1, triggers=per)
        triggers.write_state(self.home, slug, state)
        if rid:
            log.info("trigger fired routine=%s run=%s events=%d", slug, rid, len(live))
        else:
            # refused despite the gates (shouldn't happen): the messages stay durable in
            # the inbox and the routine's next run drains them — nothing is lost
            log.error("trigger fire refused routine=%s — %d event(s) already injected as "
                      "inbox messages; the next run picks them up", slug, len(live))

    @staticmethod
    def _drop(slug: str, paths: list[Path], reason: str) -> None:
        for p in paths:
            p.unlink(missing_ok=True)
        log.warning("trigger events dropped routine=%s count=%d (%s)",
                    slug, len(paths), reason)


def _event_text(ev: dict) -> str:
    """The injected user-message text: a one-line provenance head + the verbatim payload."""
    head = f"[webhook event] trigger {ev.get('trigger')} received {ev.get('ts')}"
    if ev.get("content_type"):
        head += f" ({ev['content_type']})"
    payload = str(ev.get("payload") or "").strip()
    return f"{head}:\n\n{payload or '(empty payload)'}"
