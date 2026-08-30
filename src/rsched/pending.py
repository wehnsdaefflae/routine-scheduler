"""Queued creation — what a SCHEDULED run does instead of creating a routine or a group (F328).

`create_routine` and `manage_group` are restricted to root conversations because a scheduled run
has no user in the loop to design with. The restriction is right; the consequence was wrong.
routine-improver reached a run holding a FULLY DESIGNED, user-approved routine plus a two-phase
group — all five gate questions already answered — and could not materialize any of it, so the
design had to be hand-carried back to the operator to paste in (R353).

The missing piece was never permission. It is a QUEUE. D92's preview→confirm already built the
exact shape for conversations: store a DRAFT, let the user confirm it later. A scheduled run gets
the same flow with a longer gap between the two halves — it writes a pending record here and its
run ends; the Decisions page shows what would be created; one click materializes it through the
SAME `workflows.scaffold` / `rsched.groups` path everything else uses, or discards it.

Two invariants this module exists to keep:

1. **The engine still never writes routine.yaml.** A pending record is a proposal in
   `.control/pending-creations/`, nothing more. The WEB layer materializes, exactly as it already
   applies forever-grants — one config writer, unchanged.
2. **The queuing run learns the outcome the ordinary way.** When the user acts, a message lands in
   the proposing routine's `inbox/`, drained by its next scheduled run. Nothing wakes anything: a
   creation is not urgent, and a queue that started runs would be a scheduler in disguise.

Ungated on purpose, like `report`: writing a proposal no one has approved creates nothing and
reaches no one but the operator's own Decisions page. The approval IS the gate, and it is a human.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .ids import now_iso, run_ts
from .paths import atomic_write_json, read_json

if TYPE_CHECKING:
    from .config import ServerConfig

log = logging.getLogger("rsched.pending")

PENDING_SUBDIR = Path(".control") / "pending-creations"

# manage_group verbs a scheduled run may run DIRECTLY: `list` writes nothing, and a run that
# cannot read the group store cannot propose a correct update to it. Every mutating verb queues.
READ_ONLY_VERBS = frozenset({"list"})


def pending_dir(routines_home: Path) -> Path:
    return routines_home / PENDING_SUBDIR


def new_id() -> str:
    return f"pc-{run_ts()}-{uuid.uuid4().hex[:6]}"


def queue(routines_home: Path, *, kind: str, routine: str, run_id: str, fields: dict,
          summary: str) -> dict:
    """Write one pending creation and return the record. `fields` is the action's own fields,
    stored verbatim — the materializer reads exactly what the run proposed, so what the operator
    approves on the page and what gets built cannot drift apart.
    """
    rec = {"id": new_id(), "kind": kind, "routine": routine, "run_id": run_id,
           "created_at": now_iso(), "summary": summary, "fields": fields}
    d = pending_dir(routines_home)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / f"{rec['id']}.json", rec)
    log.info("pending: %s queued %s (%s) as %s", routine, kind, summary, rec["id"])
    return rec


def load_all(routines_home: Path) -> list[dict]:
    """Every queued creation, oldest first — the Decisions page's list."""
    d = pending_dir(routines_home)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("pc-*.json")):
        rec = read_json(p)
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    return out


def load(routines_home: Path, pid: str) -> dict | None:
    rec = read_json(pending_dir(routines_home) / f"{pid}.json")
    return rec if isinstance(rec, dict) and rec.get("id") else None


def drop(routines_home: Path, pid: str) -> bool:
    path = pending_dir(routines_home) / f"{pid}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def notify_proposer(server: ServerConfig, rec: dict, outcome: str) -> bool:
    """Tell the routine that queued this what the user decided — an ordinary inbox message its
    NEXT scheduled run drains. Returns False when the proposing routine is gone (a proposal can
    outlive its author; that is not an error, it just has nobody to tell).
    """
    # A record no RUN queued has no proposer to tell. `library-drift` (daemon/library_watch.py)
    # is filed BY the daemon ABOUT a routine, so its `routine` is the victim, not the author —
    # messaging it "your proposal was discarded" would be a message about something it never did.
    if not rec.get("run_id"):
        return False
    routine_dir = server.routines_home / str(rec.get("routine") or "")
    if not (routine_dir / "routine.yaml").is_file():
        return False
    inbox = routine_dir / "inbox"
    inbox.mkdir(exist_ok=True)
    atomic_write_json(inbox / f"msg-pending-{rec['id']}.json",
                      {"text": f"[queued creation {outcome}] Your proposed "
                               f"{rec.get('kind')} — {rec.get('summary')} — was {outcome} by the "
                               "user on the Decisions page. Nothing else is pending from it.",
                       "ts": now_iso(), "via": "pending"})
    return True
