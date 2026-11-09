"""Does an operator-only block STILL HOLD right now? (2026-11-09)

`readmodels/operator_blocks` marks the few blocked-work events nothing inside the system will
clear, and the rail's badge counts them. It counted every such row in the fold's 7-day window,
so once the cause was gone the badge stayed lit for a week: on 2026-11-09 it read 10 — nine
fires skipped under a pause lifted five days earlier, and a stale backup three snapshots after
the backup came back. A badge that counts history cries wolf, which is how a real one is missed.

So each row is asked about NOW, by the one reader that already decides that condition:

- `fire_paused` / `lane_fire_paused` — is scheduling paused now (`daemon/pause.paused`)? The
  skipped fires themselves are gone by design (D237); what the operator can act on is the pause.
- `backup_stale` — does the backup watch judge it stale now (`backup_watch.staleness`)?
- `answer_undelivered` — does that routine or conversation still hold an answer no run will
  read (`readmodels/answer_pickup`)?
- `lane_chain_member_skipped` — does a lane still name that slug while no routine has it?

`None` means it cannot be told (an `unknown` backup verdict, no scheduler, `oneshot_skipped`,
which nothing records the re-arming of): the row then keeps counting. Only a KNOWN `False`
retires a row from the badge, never a guess.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import lanes, registry
from ..daemon import backup_watch, pause
from ..readmodels import answer_pickup


def checker(state) -> Callable[[str, str], bool | None]:
    """`(event, subject) -> holds now?` over this app's live state, one backup read per call."""
    server = state.server
    sched, runner = getattr(state, "scheduler", None), getattr(state, "runner", None)
    backup: list[bool | None] = []

    def backup_stale() -> bool | None:
        if not backup:
            verdict, _ = backup_watch.staleness()
            backup.append({"stale": True, "ok": False}.get(verdict))
        return backup[0]

    def undelivered(slug: str) -> bool | None:
        if sched is None or runner is None:
            return None
        lane_of = lanes.scheduled_lane_by_member(server.routines_home)
        verdicts = [answer_pickup.pickup_of(
            server, w["home"], slug, next_fires=sched.next_fires,
            lane_next_fires=sched.lane_next_fires, is_active=runner.is_active, lane_of=lane_of)
            for w in answer_pickup.waiting_answers(server) if w["slug"] == slug]
        if any(v and v["state"] == "never" for v in verdicts):
            return True
        return None if any(v is None for v in verdicts) else False

    def missing_member(slug: str) -> bool:
        named = any(slug in lanes.member_slugs(lane)
                    for lane in lanes.list_lanes(server.routines_home))
        return named and registry.info(server, server.routines_home, slug) is None

    def holds(event: str, subject: str) -> bool | None:
        if event in ("fire_paused", "lane_fire_paused"):
            return pause.paused(server)
        if event == "backup_stale":
            return backup_stale()
        if event == "answer_undelivered":
            return undelivered(subject)
        if event == "lane_chain_member_skipped":
            return missing_member(subject)
        return None

    return holds
