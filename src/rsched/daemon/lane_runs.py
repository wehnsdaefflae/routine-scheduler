"""LaneRunManager — advances an armed sequential lane fire, one member per tick (D53 Phase B).

Phase A (`rsched.lanes`) is the lane DEFINITION store and its CRUD UI. Phase B makes a lane
fire its members BACK-TO-BACK: fire a member, wait for it to reach a TERMINAL state, then —
per its outcome and the lane's on_failure policy — fire the next.

A chain fires each member ONCE, in lane order (D90). A flow that needs both an inbound and
an outbound end BRACKETS the lane: an inbound-router member placed first (its ingest lands
before anyone else fires) and an outbound-sender member placed last (it reads the state
every earlier member staged and communicates).

This manager, ticked from the Scheduler beside TriggerManager/OneShotManager after the
cron-fire loop, is the ONLY thing that turns an armed chain (rsched.lane_runs.arm, written
by the API's "run lane now") into member runs — so run spawning, the one-run-per-routine
rule, max_concurrent_runs and the restart drain stay the daemon's job, exactly as for cron,
trigger and one-shot fires. A lane member draws from the normal cron slot pool.

SEQUENTIAL, ACROSS TICKS: the tick is 5s but a member run takes minutes, so the chain's
progress lives on disk (rsched.lane_runs) and each tick does at most one transition per
lane: "the in-flight member is still running → wait", "it terminated → record it, apply
the policy", or "no member in flight → fire the member at the cursor, or end the chain
once the cursor has run past the fire list". Nothing fires two members of one lane at once.

FAILURE = a non-`ok` outcome. A member run's status.json carries both `state` (partial folds
into "finished") and `outcome` (the raw finish status: ok|partial|failed|aborted). A member
counts as FAILED for the on_failure policy when its outcome is anything but "ok" — a
budget-exhausted "partial" did not complete its job, so `stop` halts the chain there
(the outbound member at the tail included: it reads state the halted member never staged).
`on_failure="continue"` fires the remaining members regardless. A member that is MISSING (the
chain names a routine that is not in any home) or that crashed without finishing (pid dead, not
active) is recorded as a failure too, so a broken member never hangs the chain forever.

A member that is deliberately OFF is not: `outcome: "skipped"`, the cursor advances, the chain
continues under either policy and nothing is logged as a health event. That covers both the
user switching a routine off and a routine RETIRING itself (every goal-scoped stopping condition
met — registry.RoutineInfo.retired). The two used to share the missing-member branch, which put
28 `lane_chain_member_skipped` events on the live instance and would have turned a retirement
into a daily outage of every later member under `on_failure: stop`.

CONSUME ON TERMINAL: when a chain ends (done or stopped) the in-flight file is REMOVED — the
per-member results were logged, each member's own run history is the durable record, and a
cleared slot lets the lane be re-armed immediately. One in-flight chain per lane id.
All state lives on disk; a tick is idempotent and needs no boot reconcile.
"""

from __future__ import annotations

import logging

from .. import lane_runs, registry
from ..config import ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..lanes import member_slugs
from ..paths import read_json
from .runner import Runner
from .runner_state import _pid_alive

log = logging.getLogger("rsched.lane_runs")


def _fire_slugs(rec: dict) -> list[str]:
    """The chain's ordered fire list (rec["cursor"] indexes into it)."""
    return member_slugs(rec)


class LaneRunManager:
    """Owns the advance side of armed sequential lane fires; constructed with the shared
    server + runner and ticked by the Scheduler with its live catalog.
    """

    def __init__(self, server: ServerConfig, runner: Runner):
        self.server = server
        self.runner = runner
        self.home = server.routines_home

    async def tick(self, catalog: dict[str, registry.RoutineInfo]) -> None:
        """One advance pass over every in-flight chain. Never raises into the scheduler loop."""
        try:
            for rec in lane_runs.in_flight(self.home):
                await self._advance(rec, catalog)
        except Exception:
            log.exception("lane-run tick failed")

    async def _advance(self, rec: dict, catalog: dict[str, registry.RoutineInfo]) -> None:
        # A member in flight → collect its result when terminal; otherwise fire the next one.
        # At most one transition per tick, so a lane never fires two members at once.
        if rec.get("current_run"):
            self._collect(rec, catalog)
        else:
            await self._fire_next(rec, catalog)

    def _finalize(self, rec: dict, status: str) -> None:
        """End the chain (status = done | stopped): stamp it, log it, consume the file.

        Emits a lane_chain_done / lane_chain_stopped health event (F316): the in-flight
        file is consumed right here, so the event is the chain's only durable chain-level
        record - and a scheduled lane's daily/weekly done event is a HEARTBEAT whose
        absence over a schedule period is how an audit detects a silently starved lane.
        """
        lane_id = str(rec.get("lane_id") or "")
        rec["status"] = status
        rec["ended"] = now_iso()
        entries = rec.get("log") or []
        not_ok = [str(e.get("slug")) for e in entries if e.get("outcome") != "ok"]
        log_health_event(
            self.home, f"lane_chain_{status}", routine=lane_id,
            run_id=str(rec.get("id") or ""),
            detail=f"{rec.get('name') or lane_id}: {len(entries)} member runs, "
                   f"{len(not_ok)} not-ok"
                   + (f" ({', '.join(not_ok)})" if not_ok else "")
                   + f", armed_by={rec.get('armed_by') or '?'}, armed {rec.get('created')}")
        log.info("lane chain %s lane=%s members=%d", status, lane_id,
                 len(rec.get("members") or []))
        lane_runs.remove(self.home, lane_id)

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
            lane_runs.save(self.home, rec)

    async def _fire_next(self, rec: dict, catalog: dict[str, registry.RoutineInfo]) -> None:
        """No member in flight: fire the member at the cursor, or end the chain once the
        cursor has run past the fire list. The daemon owns the drain + one-run-per-routine
        rules, exactly as for cron/trigger/one-shot fires.
        """
        lane_id = str(rec.get("lane_id") or "")
        fire_list = _fire_slugs(rec)
        cursor = int(rec.get("cursor") or 0)
        if cursor >= len(fire_list):
            self._end_of_chain(rec)
            return
        if self.runner.draining:
            return
        slug = fire_list[cursor]
        info = catalog.get(slug)
        # A member that is DELIBERATELY off — switched off by the user, or retired because it
        # reached its final goal — is not a broken chain. It used to share one branch with a
        # MISSING member and be logged `outcome: "failed"`, which put 28 health events on the
        # live instance (all four FAU members among them) and would have stopped the chain
        # outright under `on_failure: stop`. Absent is still a failure: the chain names a
        # routine that is not there.
        if info is not None and (not info.cfg.enabled or info.retired):
            why = "retired (final goal met)" if info.retired else "switched off"
            rec["log"].append({"slug": slug, "run_id": None, "state": "skipped",
                               "outcome": "skipped"})
            rec["cursor"] = cursor + 1
            log.info("lane member skipped lane=%s slug=%s (%s)", lane_id, slug, why)
            lane_runs.save(self.home, rec)
            return
        if info is None:
            rec["log"].append({"slug": slug, "run_id": None, "state": "skipped",
                               "outcome": "failed"})
            rec["cursor"] = cursor + 1
            log.warning("lane member missing lane=%s slug=%s", lane_id, slug)
            log_health_event(
                self.home, "lane_chain_member_skipped", routine=slug, run_id="",
                detail=f"lane {rec.get('name') or lane_id} ({lane_id}): member is not a routine "
                       "in any home - "
                       + ("chain stops (on_failure=stop)" if rec.get("on_failure") == "stop"
                          else "chain continues"))
            if rec.get("on_failure") == "stop":
                self._finalize(rec, "stopped")
            else:
                lane_runs.save(self.home, rec)
            return
        if self.runner.is_active(slug):
            return  # the member is already running (another fire) — wait for it to free up
        # The fire REASON is broadcast on the run-started event and read in the activity
        # stream: it is how an operator tells a chained member run from a cron one.
        rid = await self.runner.fire(info.cfg, reason="lane")
        if rid:
            rec["current_run"] = rid
            rec["status"] = "running"
            lane_runs.save(self.home, rec)
            log.info("lane fired member lane=%s slug=%s run=%s (%d/%d)",
                     lane_id, slug, rid, cursor + 1, len(fire_list))
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
