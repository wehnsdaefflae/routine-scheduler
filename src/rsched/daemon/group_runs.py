"""GroupRunManager — advances an armed sequential group fire, one member per tick (D53 Phase B).

Phase A stored group DEFINITIONS (rsched.groups) and their CRUD UI; nothing fired. Phase B
makes a group fire its members BACK-TO-BACK: fire a member, wait for it to reach a TERMINAL
state, then — per its outcome and the group's on_failure policy — fire the next, and so on.

A chain runs ONCE over the members in group order (the F292 two-pass split machinery is
retired — D90, 2026-08-16): a flow that needs both an inbound and an outbound end
brackets the group instead, with an inbound-router member placed first (its ingest lands
before anyone else fires) and an outbound-sender member placed last (it reads the state
every earlier member staged and communicates).

This manager, ticked from the Scheduler beside TriggerManager/OneShotManager after the
cron-fire loop, is the ONLY thing that turns an armed chain (rsched.group_runs.arm, written
by the API's "run group now") into member runs — so run spawning, the one-run-per-routine
rule, max_concurrent_runs and the restart drain stay the daemon's job, exactly as for cron,
trigger and one-shot fires. A group member draws from the normal cron slot pool.

SEQUENTIAL, ACROSS TICKS: the tick is 5s but a member run takes minutes, so the chain's
progress lives on disk (rsched.group_runs) and each tick does at most one transition per
group: "the in-flight member is still running → wait", "it terminated → record it, apply
the policy", or "no member in flight → fire the next (or flip pass / finish)". Nothing
fires two members of one group at once.

FAILURE = a non-`ok` outcome. A member run's status.json carries both `state` (partial folds
into "finished") and `outcome` (the raw finish status: ok|partial|failed|aborted). A member
counts as FAILED for the on_failure policy when its outcome is anything but "ok" — a
budget-exhausted "partial" did not complete its job, so `stop` halts the chain there
(including the whole outbound pass: outbound reads state the halted ingest never staged).
`on_failure="continue"` fires the remaining members regardless. A member that is missing,
disabled, or crashed without finishing (pid dead, not active) is recorded as a failure too, so
a broken member never hangs the chain forever.

CONSUME ON TERMINAL: when a chain ends (done or stopped) the in-flight file is REMOVED — the
per-member results were logged and each member's own run history is the durable record, and a
cleared slot lets the group be re-armed immediately. One in-flight chain per group id.
All state lives on disk; a tick is idempotent and needs no boot reconcile.
"""

from __future__ import annotations

import logging

from .. import group_runs, registry
from ..config import ServerConfig
from ..groups import member_slugs
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import read_json
from .runner import Runner
from .runner_state import _pid_alive

log = logging.getLogger("rsched.group_runs")


def _fire_slugs(rec: dict) -> list[str]:
    """The chain's ordered fire list (rec["cursor"] indexes into it)."""
    return member_slugs(rec)


class GroupRunManager:
    """Owns the advance side of armed sequential group fires; constructed with the shared
    server + runner and ticked by the Scheduler with its live catalog.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.routines_home

    async def tick(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """One advance pass over every in-flight chain. Never raises into the scheduler loop."""
        try:
            for rec in group_runs.in_flight(self.home):
                await self._advance(rec, catalog)
        except Exception:
            log.exception("group-run tick failed")

    async def _advance(self, rec: dict, catalog: dict[str, registry.RoutineInfo]) -> None:
        # A member in flight → collect its result when terminal; otherwise fire the next one.
        # At most one transition per tick, so a group never fires two members at once.
        if rec.get("current_run"):
            self._collect(rec, catalog)
        else:
            await self._fire_next(rec, catalog)

    def _finalize(self, rec: dict, status: str) -> None:
        """End the chain (status = done | stopped): stamp it, log it, consume the file.

        Emits a group_chain_done / group_chain_stopped health event (F316): the in-flight
        file is consumed right here, so the event is the chain's only durable chain-level
        record - and a scheduled group's daily/weekly done event is a HEARTBEAT whose
        absence over a schedule period is how an audit detects a silently starved group.
        """
        gid = str(rec.get("group_id") or "")
        rec["status"] = status
        rec["ended"] = now_iso()
        entries = rec.get("log") or []
        not_ok = [str(e.get("slug")) for e in entries if e.get("outcome") != "ok"]
        log_health_event(
            self.home, f"group_chain_{status}", routine=gid,
            run_id=str(rec.get("id") or ""),
            detail=f"{rec.get('name') or gid}: {len(entries)} member runs, {len(not_ok)} not-ok"
                   + (f" ({', '.join(not_ok)})" if not_ok else "")
                   + f", armed_by={rec.get('armed_by') or '?'}, armed {rec.get('created')}")
        log.info("group chain %s group=%s members=%d", status, gid, len(rec.get("members") or []))
        group_runs.remove(self.home, gid)

    def _end_of_chain(self, rec: dict) -> None:
        """The cursor ran past the fire list: the chain is complete."""
        self._finalize(rec, "done")

    def _collect(self, rec: dict, catalog: dict[str, registry.RoutineInfo]) -> None:
        """The in-flight member terminated (or not yet). Record its result, advance the cursor,
        and apply the on_failure policy — leaving the NEXT fire to a following tick.
        """
        result = self._member_result(rec, catalog)
        if result is None:
            return  # still running — wait for a later tick
        state, outcome = result
        slug = str(rec["current_run"]).partition(":")[0]
        rec["log"].append({"slug": slug, "run_id": rec["current_run"],
                           "state": state, "outcome": outcome})
        rec["current_run"] = None
        rec["cursor"] = int(rec.get("cursor") or 0) + 1
        if outcome != "ok" and rec.get("on_failure") == "stop":
            self._finalize(rec, "stopped")
        elif int(rec.get("cursor") or 0) >= len(_fire_slugs(rec)):
            self._end_of_chain(rec)
        else:
            group_runs.save(self.home, rec)

    async def _fire_next(self, rec: dict, catalog: dict[str, registry.RoutineInfo]) -> None:
        """No member in flight: fire the member at the cursor, or flip/finish the pass. The
        daemon owns the drain + one-run-per-routine rules, exactly as for cron/trigger/one-shot
        fires.
        """
        gid = str(rec.get("group_id") or "")
        fire_list = _fire_slugs(rec)
        cursor = int(rec.get("cursor") or 0)
        if cursor >= len(fire_list):
            self._end_of_chain(rec)
            return
        if self.runner.draining:
            return
        slug = fire_list[cursor]
        info = catalog.get(slug)
        if info is None or not info.cfg.enabled:
            rec["log"].append({"slug": slug, "run_id": None, "state": "skipped",
                               "outcome": "failed"})
            rec["cursor"] = cursor + 1
            log.warning("group member skipped group=%s slug=%s (missing or disabled)", gid, slug)
            log_health_event(
                self.home, "group_chain_member_skipped", routine=slug, run_id="",
                detail=f"group {rec.get('name') or gid} ({gid}): member missing or disabled - "
                       + ("chain stops (on_failure=stop)" if rec.get("on_failure") == "stop"
                          else "chain continues"))
            if rec.get("on_failure") == "stop":
                self._finalize(rec, "stopped")
            else:
                group_runs.save(self.home, rec)
            return
        if self.runner.is_active(slug):
            return  # the member is already running (another fire) — wait for it to free up
        rid = await self.runner.fire(info.cfg, reason="group")
        if rid:
            rec["current_run"] = rid
            rec["status"] = "running"
            group_runs.save(self.home, rec)
            log.info("group fired member group=%s slug=%s run=%s (%d/%d)",
                     gid, slug, rid, cursor + 1, len(fire_list))
        # rid is None (overrun/drain slipped in): leave current_run null, retry next tick

    def _member_result(self, rec: dict,
                       catalog: dict[str, registry.RoutineInfo]) -> tuple[str, str] | None:
        """(`state`, `outcome`) once the in-flight member run is terminal, else None.
        A run whose dir is gone / whose pid is dead while it is no longer active counts as a
        crashed FAILURE so the chain does not hang on it forever.
        """
        run_id = str(rec.get("current_run") or "")
        slug, _, ts = run_id.partition(":")
        if not slug or not ts:
            return ("failed", "failed")
        info = catalog.get(slug)
        run_dir = (info.cfg.dir if info else self.home / slug) / "runs" / ts
        st = read_json(run_dir / "status.json")
        if not isinstance(st, dict):
            return None if self.runner.is_active(slug) else ("failed", "failed")
        state = str(st.get("state") or "unknown")
        if state in registry.TERMINAL_STATES:
            outcome = st.get("outcome") or ("ok" if state == "finished" else state)
            return (state, str(outcome))
        # not terminal yet: still live, or crashed without a finish (pid dead, not tracked)
        if slug not in self.runner.active and not _pid_alive(st.get("pid")):
            return ("failed", "failed")
        return None
