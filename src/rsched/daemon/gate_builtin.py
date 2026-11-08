"""The gate's BUILT-IN reasons — work by construction, asked before any check and never
configured away (docs/run-gates.md "Reasons that run before any check is asked").

Each is read in the gate's killable preparation child (`daemon/gate_prepare.child_main`), never
on the daemon's event loop. The baseline half — the last admitted run did not finish ok, or its
recipe or finish line changed since — stays in `gate_prepare` beside the `last_ok` reading the
checks share.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import RoutineConfig, ServerConfig
from ..engine import inbox as inbox_mod
from ..paths import read_json


def pending_inbox(directory: Path) -> bool:
    """Does freight wait in the inbox — the ONE inbox predicate, fail-open like every gate read
    (a `msg-*` file it cannot parse is WORK)?

    A CLOSURE is not freight (`closes: true` — a report reply that ends a thread this routine
    started, born settled). It asks nothing of the routine, it waits in the inbox unconsumed
    until a run the gate DOES admit reads it, and the `max_quiet` backstop bounds that wait.
    Counted, closures were the gate's biggest single leak: of the 43 fires the inbox admitted
    up to 2026-11-08, 10 held nothing but closures — nine of them rules-review's, whose
    reports other routines answer and close as a matter of course. The report trigger has
    excluded them since it existed (`daemon/triggers.py`); the gate now agrees with it.
    """
    return inbox_mod.has_pending_messages(directory, include_closures=False,
                                          on_unparseable=True)


def pending_answers(directory: Path) -> bool:
    """Does a person's answer to one of this routine's questions wait to be read?

    An answer never STARTS a run — nothing fires on it (the operator reversed that on
    2026-09-12). But once the schedule has fired, an unread answer IS work for that fire: the
    routine asked because it could not go on without it. A gate that skipped here left the
    answer waiting for whatever else admitted a run, which for a quiet routine is never (two
    routines held one each on 2026-09-29, one of them unblocking a parked job).
    """
    inbox = directory / "inbox"
    if not inbox.is_dir():
        return False
    # Only an answer the next run would actually CONSUME counts: one carrying text, to a
    # question still pending (engine/inbox_questions.collect_deferred_answers). A stray answer
    # file whose question is gone is never read by any run; counting it would admit every
    # fire forever (one had sat in a routine's inbox for sixteen days).
    for path in inbox.glob("answer-*.json"):
        obj = read_json(path)
        if not isinstance(obj, dict) or "text" not in obj:
            continue
        qid = str(obj.get("qid") or path.stem.removeprefix("answer-"))
        if (directory / "questions" / "pending" / f"{qid}.json").is_file():
            return True
    return False


def pending_notes(cfg: RoutineConfig, server: ServerConfig) -> bool:
    """Does a note from a routine sharing one of its stores wait for this one? The engine
    delivers a note at the addressee's next boot (`sharedstores.drain`), so a gate that skipped
    here would hold a teammate's hand-off until something else admitted a run.
    """
    from .. import sharedstores

    return any(any(sharedstores.notes_dir(store, cfg.slug).glob("note-*.json"))
               for store in sharedstores.stores_of(server.routines_home, cfg.fs_write_roots))


def keeps_jobs(cfg: RoutineConfig) -> bool:
    """Does this routine keep TASKS (rsched/jobs.py)? Then its checks are asked even when a
    built-in reason already decided "run": which jobs have work is what its run needs from
    them (`gate.json` → engine/jobops.py).
    """
    from ..grants import effective_settings, normalize_capabilities

    return effective_settings(normalize_capabilities(cfg.capabilities)[0])["jobs"] == "on"


def jobs_clock_reason(cfg: RoutineConfig, checks: list[dict] | None = None) -> str:
    """A task due by its OWN clock — work an earlier run left, a wake date that came, a quiet
    limit passed, a task no check watches — admits the fire, whatever the checks would say:
    nothing else would ever bring the routine back for it.

    `checks` are the ones this fire asks (`gatecompose.compose`): a run-written gate that stops
    watching a job leaves that job unwatched, and an unwatched job is due by its own clock.
    """
    from .. import jobs

    now = datetime.now(ZoneInfo(cfg.tz))
    watched = list(cfg.run_gate.checks) if checks is None else checks
    due = jobs.clock_due(cfg.dir, watched, today=now.date(), now=now)
    if not due:
        return ""
    return "job(s) due by their own clock: " + "; ".join(f"{tid} ({why})" for tid, why in due)


def reason(cfg: RoutineConfig, server: ServerConfig, checks: list[dict], *,
           inbox: bool, jobs_on: bool) -> str:
    """The first built-in reason this fire is work, or "" — `inbox` is `pending_inbox`,
    already read by the caller, which answers the inbox-only race guard with it too.
    """
    if inbox:
        return "pending inbox"
    if pending_answers(cfg.dir):
        return "an answer to one of its questions is waiting to be read"
    if pending_notes(cfg, server):
        return "a note from a routine sharing one of its stores is waiting"
    return jobs_clock_reason(cfg, checks) if jobs_on else ""
