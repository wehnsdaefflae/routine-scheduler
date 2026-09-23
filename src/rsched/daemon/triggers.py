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

from .. import registry, spool, triggers
from ..config import ServerConfig
from ..engine import inbox as inbox_mod
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import read_json
from .runner import Runner

log = logging.getLogger("rsched.triggers")


def _inbox_wants_a_run(inbox: Path) -> bool:
    """True if the inbox holds a message worth WAKING for — for a routine that DECLARES a
    report trigger.

    Answers (`answer-*`) never count: a deferred question is one the run did not need
    answered to finish, so its answer waits for the routine's next scheduled run — or for
    the operator's own "answer & run now" on the Decisions page, which is a manual fire, not
    a trigger. (0.330.0 fired a run on every answer; three routines started in one second
    when the operator cleared his inbox, and the schedule stopped being the schedule.) A
    CLOSURE (`closes` — the terminal acknowledgment of an exchange this routine started) is
    exempt too: it asks nothing, and buying a full run of a recipe to read "no reply needed"
    is exactly the amplification the cooldown cannot see. Anything unreadable or unrecognised
    WAKES (fail open — a message the daemon cannot classify must never be silently swallowed).

    The scan selects `msg-*.json`, the stem the ONE writer produces, and not "any file that
    is not `answer-*`": `paths.atomic_write` puts its temp file IN the target directory, so
    the old filter also matched an in-flight `.msg-….json.XXXX.tmp` — unreadable, and
    unreadable WAKES here, which bought a whole run off a race with a write.
    """
    if not inbox.is_dir():
        return False
    for path in sorted(inbox.glob("msg-*.json")):
        msg = read_json(path)
        if not isinstance(msg, dict) or not msg.get("closes"):
            return True
    return False


class TriggerManager:
    """Owns the spool→fire side of event triggers; constructed with the shared server +
    runner and ticked by the Scheduler with its live catalog.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.routines_home

    async def tick(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """One pass over the spool + the report-trigger inbox watch. Never raises into
        the scheduler loop.
        """
        try:
            for slug in triggers.slugs_with_events(self.home):
                await self._service(slug, catalog.get(slug))
            # report triggers have no spool — the durable inbox file IS the event, so
            # the watch is a cheap glob on exactly the routines that DECLARE one
            for slug, info in catalog.items():
                await self._service_report(slug, info)
        except Exception:
            log.exception("trigger tick failed")

    async def _service_report(self, slug: str, info: registry.RoutineInfo) -> None:
        """Fire a routine whose inbox holds an unconsumed report/message, if it declares
        a `report` trigger: one fire per cooldown window (everything that lands meanwhile
        is drained by that one run — coalescing), never while a run is active/queued or
        the daemon drains, never for a routine that is switched off or RETIRED (a finished
        routine has no work its inbox can restart). Nothing is consumed here: the
        fired run's own boot drain empties the inbox, and a crash before the drain just
        means one more fire after the cooldown — the messages are durable either way.
        """
        trig = next((t for t in info.cfg.triggers if t.get("type") == "report"), None)
        if trig is None or not info.fireable:
            return
        if not _inbox_wants_a_run(info.cfg.dir / "inbox"):
            return
        if self.runner.draining or self.runner.is_active(slug):
            return
        state = triggers.read_state(self.home, slug)
        raw_per = state.get("triggers")
        per: dict = raw_per if isinstance(raw_per, dict) else {}
        tid = str(trig["id"])
        # `.get(key, default)`, never `or`: a configured 0 means "no wait, fire on every
        # delivery" and an `or` fallback silently turns that into the 15-minute default.
        cooldown = int(trig.get("cooldown_s", triggers.DEFAULT_REPORT_COOLDOWN_S))
        got = per.get(tid)
        mine: dict = got if isinstance(got, dict) else {}
        if self._cooling(mine, cooldown):
            return
        now = now_iso()
        today = now[:10]
        cap = int(trig.get("max_fires_per_day", triggers.DEFAULT_REPORT_MAX_FIRES_PER_DAY))
        fires_today = int(mine.get("fires_today") or 0) if mine.get("day") == today else 0
        if cap and fires_today >= cap:
            # Silence would repeat F276's lesson: a capped trigger is a DARK routine, and
            # dark must be visible. One event per trigger per day, at the transition.
            if mine.get("capped_logged") != today:
                log_health_event(self.home, "trigger_capped", routine=slug, run_id="",
                                 detail=f"report trigger {tid} hit its {cap}/day cap — "
                                        "inbox work waits for the next scheduled run")
                per[tid] = {**mine, "capped_logged": today}
                state.update(triggers=per)
                triggers.write_state(self.home, slug, state)
            return
        rid = await self.runner.fire(info.cfg, reason="trigger")
        per[tid] = {"last_fired": now, "events": int(mine.get("events") or 0) + 1,
                    "day": today, "fires_today": fires_today + 1}
        state.update(last_fired=now, fires=int(state.get("fires") or 0) + 1, triggers=per)
        triggers.write_state(self.home, slug, state)
        if rid:
            log.info("report trigger fired routine=%s run=%s", slug, rid)

    async def _service(self, slug: str, info: registry.RoutineInfo | None) -> None:
        events = triggers.pending_events(self.home, slug)
        if not events:
            return
        if info is None or not info.fireable:
            spool.drop(events, what="trigger events", slug=slug,
                       reason="routine missing, switched off or retired")
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
            spool.drop(stale, what="trigger events", slug=slug,
                       reason="trigger deleted or event unreadable")
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
        for path, ev in live:
            inbox_mod.file_message(info.cfg.dir, _event_text(ev), via="trigger",
                                   name=f"trig-{path.stem}")
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

def _event_text(ev: dict) -> str:
    """The injected user-message text: a one-line provenance head + the verbatim payload."""
    head = f"[webhook event] trigger {ev.get('trigger')} received {ev.get('ts')}"
    if ev.get("content_type"):
        head += f" ({ev['content_type']})"
    payload = str(ev.get("payload") or "").strip()
    return f"{head}:\n\n{payload or '(empty payload)'}"
