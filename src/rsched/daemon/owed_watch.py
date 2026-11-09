"""Owed work reaches the operator: what a person asked and nobody finished, and work nobody owns.

Two failures from the 2026-10-17 investigation, both of the same shape — work that was KNOWN and
that nothing on the instance would ever bring back:

- an approval sat unfinished for eleven weeks (freelance-radar's profile update, "You fix all of
  that", 2026-07-30). Obligations now carry from run to run (engine/obligations.py), and every run
  answers for them — but a routine that keeps leaving one unmet says so only in its own
  accounting. So once an obligation is open longer than `obligations.OVERDUE_DAYS`, this files
  `obligation_overdue` on the blocked-work surface (readmodels/health_stream.BLOCKED_EVENTS): one
  row per routine per day, naming each owed item, how long, and what the last run left unmet.
- an ANSWER the operator gave that no run will ever read (2026-11-09: two sat seven weeks,
  filed during their conversation's last turn). Past `UNDELIVERED_DAYS` this files
  `answer_undelivered` on the blocked-work surface, one row per routine per day — the Decisions
  page already lists them as UNDELIVERED; this is the reminder for the ones nobody went to see.
- a report nobody owned sat three weeks (R2044: "nobody owns deploying the Steward kit"). A
  report addressed to no one, or to a routine that starts no run, is read by triage at best. So
  once one has waited longer than `UNOWNED_DAYS`, this files ONE `unowned-report` decision on the
  Decisions page (the pending queue, `rsched/pending.py`): route it to a routine — suggestions
  first — or close it. The web executes the choice (web/api_pending.py) as a report from the
  operator, so the ledger says who decided. A record whose report found an owner or was settled
  meanwhile is withdrawn at the next sweep.

Daily, like the other standing-condition watches (daemon/marker_watch.py): the conditions are
measured in days, and the reads — every routine's obligations, the report index — would be waste
on a 5 s tick. Never raises into the tick.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import lanes, proofcheck
from ..config import ServerConfig, load_routine
from ..engine import finishline, obligations
from ..health_events import log_health_event
from ..readmodels import answer_pickup

log = logging.getLogger("rsched.daemon")

#: How long a report may wait without an owner before it becomes the operator's decision.
UNOWNED_DAYS = 7
#: How long an ANSWER may wait with no run that will ever read it before the operator is told
#: again on the blocked-work surface. A day, because the Decisions page says it at once; this
#: is the reminder for an answer nobody went back to look at (two sat seven weeks, 2026-09-21).
UNDELIVERED_DAYS = 1
#: The routines a homeless report is usually someone's — offered after the routine that filed it.
_LIKELY = ("scheduler-builder", "self-audit", "routine-improver")


def _day(stamp: object) -> date | None:
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        try:
            return date.fromisoformat(str(stamp)[:10])
        except ValueError:
            return None
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).date()


def runs(home: Path, slug: str) -> bool:
    """Does `slug` name a routine that starts runs — present, switched on, not retired?"""
    d = home / slug
    cfg, _ = load_routine(d) if (d / "routine.yaml").is_file() else (None, [])
    return bool(cfg is not None and cfg.enabled and not finishline.goal_reached(d))


def overdue(home: Path, today: date) -> dict[str, list[dict]]:
    """`{routine: [obligation, …]}` — each open longer than OVERDUE_DAYS."""
    out: dict[str, list[dict]] = {}
    for d in sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for ob in obligations.load(d):
            since = _day(ob.get("since"))
            if since is not None and (today - since).days > obligations.OVERDUE_DAYS:
                out.setdefault(d.name, []).append({**ob, "days": (today - since).days})
    return out


def unowned(home: Path, today: date) -> list[dict]:
    """Every report still open or in progress, older than UNOWNED_DAYS, whose owner is nobody
    or a routine that starts no run.
    """
    from ..readmodels import items

    found = []
    for it in items.build(home / "self-audit", home)["items"]:
        if it["type"] != "report" or it["status"] not in ("open", "in_progress"):
            continue
        filed = _day(it["origin"]["ts"])
        owner = str(it.get("to") or "")
        if filed is None or (today - filed).days <= UNOWNED_DAYS or (owner and runs(home, owner)):
            continue
        sender = str(it["origin"].get("routine") or "")
        suggest = [s for s in dict.fromkeys((sender, *_LIKELY)) if s and runs(home, s)]
        found.append({"report": it["id"], "title": it["title"], "from": sender, "to": owner,
                      "days": (today - filed).days, "suggest": suggest})
    return found


class OwedWatch:
    """The daemon's owed-work check, once a day."""

    def __init__(self, server: ServerConfig, scheduler=None) -> None:
        self.server = server
        self.scheduler = scheduler   # its fire tables + runner say who reads a waiting answer
        self._checked: date | None = None

    async def tick(self) -> None:
        today = datetime.now(UTC).date()
        if self._checked == today:
            return
        self._checked = today
        try:
            self.check(today)
        except Exception:
            log.exception("owed-watch: the daily check raised")

    def check(self, today: date) -> None:
        home = self.server.routines_home
        if not home.is_dir():
            return
        for slug, owed in overdue(home, today).items():
            detail = "; ".join(f"{o['id']} open {o['days']} days: {o.get('text', '')}"
                               + (f" (last left: {o['remains']})" if o.get("remains") else "")
                               for o in owed)
            log_health_event(home, "obligation_overdue", routine=slug, run_id="",
                             detail=detail, obligations=len(owed))
        self._file_unowned(home, today)
        self._file_undelivered(home)
        # D220: and the proofs. An answered decision whose authorized work never landed is
        # owed work of exactly this kind — the operator's own framing ("accept / decline my
        # response based on the result of you checking the proof"), and the reason this watch
        # is where the sweep lives rather than in a watcher of its own: same daily cadence,
        # same subject, and `daemon/scheduler.py` is over the size bar.
        moved = proofcheck.sweep_home(home)
        if moved:
            log.info("owed-watch: %d decision proof(s) moved state", moved)

    def _file_undelivered(self, home: Path) -> None:
        """`answer_undelivered`, one row per routine or conversation holding an answer that NO
        run will read and that has waited past UNDELIVERED_DAYS (`readmodels/answer_pickup`).
        """
        sched = self.scheduler
        if sched is None:
            return
        lane_of = lanes.scheduled_lane_by_member(home)
        cutoff = time.time() - UNDELIVERED_DAYS * 86400
        stuck: dict[str, list[str]] = {}
        for waiting in answer_pickup.waiting_answers(self.server):
            if waiting["since"] > cutoff:
                continue
            pick = answer_pickup.pickup_of(
                self.server, waiting["home"], waiting["slug"], next_fires=sched.next_fires,
                lane_next_fires=sched.lane_next_fires, is_active=sched.runner.is_active,
                lane_of=lane_of)
            if pick and pick["state"] == "never":
                stuck.setdefault(waiting["slug"], []).append(f"{waiting['qid']} ({pick['why']})")
        for slug, qids in stuck.items():
            log_health_event(home, "answer_undelivered", routine=slug, run_id="",
                             detail="no run will read: " + "; ".join(qids)
                             + " — deliver or settle it on the Decisions page",
                             answers=len(qids))

    def _file_unowned(self, home: Path, today: date) -> None:
        from .. import pending

        found = unowned(home, today)
        live = {f["report"] for f in found}
        for rec in pending.load_all(home):
            if rec.get("kind") == "unowned-report" and \
                    (rec.get("fields") or {}).get("report") not in live:
                pending.drop(home, rec["id"])        # it found an owner, or was settled
        for f in found:
            pending.queue(self.server, kind="unowned-report", routine=f["from"], run_id="",
                          fields=f,
                          summary=(f"{f['report']} has had no owner for {f['days']} days: "
                                   f"{f['title']}"))


def cutoff(today: date) -> date:
    """The filing date before which an unowned report is the operator's — for a reader."""
    return today - timedelta(days=UNOWNED_DAYS)
