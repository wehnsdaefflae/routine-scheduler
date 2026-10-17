"""Owed work reaches the operator: what a person asked and nobody finished, and work nobody owns.

Two failures from the 2026-10-17 investigation, both of the same shape — work that was KNOWN and
that nothing on the instance would ever bring back:

- an approval sat unfinished for eleven weeks (freelance-radar's profile update, "You fix all of
  that", 2026-07-30). Obligations now carry from run to run (engine/obligations.py), and every run
  answers for them — but a routine that keeps leaving one unmet says so only in its own
  accounting. So once an obligation is open longer than `obligations.OVERDUE_DAYS`, this files
  `obligation_overdue` on the blocked-work surface (readmodels/health_stream.BLOCKED_EVENTS): one
  row per routine per day, naming each owed item, how long, and what the last run left unmet.
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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig, load_routine
from ..engine import finishline, obligations
from ..health_events import log_health_event

log = logging.getLogger("rsched.daemon")

#: How long a report may wait without an owner before it becomes the operator's decision.
UNOWNED_DAYS = 7
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

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
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

    def _file_unowned(self, home: Path, today: date) -> None:
        from .. import pending

        found = unowned(home, today)
        live = {f["report"] for f in found}
        for rec in pending.load_all(home):
            if rec.get("kind") == "unowned-report" and \
                    (rec.get("fields") or {}).get("report") not in live:
                pending.drop(home, rec["id"])        # it found an owner, or was settled
        for f in found:
            pending.queue(home, kind="unowned-report", routine=f["from"], run_id="", fields=f,
                          summary=(f"{f['report']} has had no owner for {f['days']} days: "
                                   f"{f['title']}"))


def cutoff(today: date) -> date:
    """The filing date before which an unowned report is the operator's — for a reader."""
    return today - timedelta(days=UNOWNED_DAYS)
