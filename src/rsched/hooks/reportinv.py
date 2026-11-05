"""The ledger row read back AS the hook it was, and the first DECISION taken from the stream.

Campaign step 7 of D152. Step 4 made the hook the SOURCE of every new report
(`reportproj`): the hook is written first and `reports.jsonl` is computed from it. What it did
not do is close the loop — `hooks/store.py`'s own docstring says it out loud: *"Nothing reads
this stream to make a decision yet"*. Ten modules fold `reports.jsonl` to answer something
(the open-thread cap, the item read models, the orphan sweep, the priorities fold); not one of
them reads the stream. A store that only tests read is a claim, not a source.

Two things have to exist before a decision can move, and this module is both.

**1. The INVERSE (`hook_of_row`).** The stream began on 2026-10-19; the ledger began on
2026-07-19. 3,952 of its 5,016 rows — every row of 2,209 report ids — predate the stream and
have no hook. A decision repointed at the stream today would answer from a third of the
history and be WRONG while looking right: the open-thread cap would stop counting threads
opened before October. So the history is carried over, and that needs the projection read
backwards. It is exact rather than approximate because `reportproj.ledger_row` and
`event_row` are total: measured against the live ledger, no filing or event row carries a key
outside what they emit, and no id is stored in a case the normalization would change.

Leniently, like `from_record` and for the same reason: this reads rows written by every build
since July. One live row (R1062, 2026-08-29) carries `closes` with neither `answers` nor
`settles` — a shape today's `Disposal` REFUSES (D196). It is history and must read back as
what it says, so the payload is assembled directly here rather than through `Disposal`, and
the refusal stays where it belongs: on the write path, judging what a run files NOW.

**2. The first decision ON the stream (`open_threads_now`).** The open-thread cap is the right
one to move first: it is the only place where folding this history REFUSES something (a run's
fourth parallel report to one owner), so it is where a wrong count is visible rather than
cosmetic — and it is one call, inside the ledger lock, that already holds the rows to compare
against. It reads the STREAM's answer and the LEDGER's, and when they differ it files
`hook_stream_diverged` and keeps the ledger's. The agreement is the campaign's whole claim;
until the duplication is removed, a disagreement is a defect in this module or an unmirrored
write, and either way the ledger is what the operator's own backlog counts are built on.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..health_events import log_health_event
from ..report_threads import open_threads
from . import Hook, addressee
from .reportproj import TERMINAL_EVENTS, stream_rows

log = logging.getLogger("rsched.hooks")


def hook_of_row(row: dict) -> Hook:
    """The hook one `reports.jsonl` row IS — a filing or a lifecycle fact.

    The inverse of `reportproj.rows_for`, and the same single entry point: a caller folding
    the ledger never has to ask which kind of row it is holding. An unparseable row yields a
    hook with an empty id, which the backfill skips rather than writing freight nothing names.
    """
    row = row if isinstance(row, dict) else {}
    report_id = str(row.get("id") or "")
    ts = str(row.get("ts") or "")
    if event := str(row.get("event") or ""):
        payload: dict = {"event": event}
        for key in ("run_id", "by", "to"):
            if value := str(row.get(key) or ""):
                payload[key] = value
        return Hook(id=report_id, source="report", trigger={"kind": "send"},
                    addressee=addressee("triage"), payload=payload, delivery="next_prompt",
                    disposition="settled" if event in TERMINAL_EVENTS else "open",
                    origin={"run_id": run} if (run := str(row.get("run_id") or "")) else {},
                    ts=ts)
    filing: dict = {"title": str(row.get("title") or ""),
                    "detail": str(row.get("detail") or "")}
    if answers := str(row.get("answers") or ""):
        filing["answers"] = answers
    if row.get("closes"):
        filing["closes"] = True
    if settles := [str(i) for i in (row.get("settles") or ())]:
        filing["settles"] = settles
    if supersedes := [str(i) for i in (row.get("supersedes") or ())]:
        filing["supersedes"] = supersedes
    target = str(row.get("target") or "")
    return Hook(id=report_id, source="report", trigger={"kind": "send"},
                addressee=addressee("routine", target) if target else addressee("triage"),
                payload=filing, delivery="next_prompt", disposition="open",
                origin={"routine": str(row.get("routine") or ""),
                        "run_id": str(row.get("run_id") or "")},
                ts=ts)


def open_threads_now(routines_home: Path, rows: list[dict], *, routine: str,
                     target: str) -> list[str]:
    """The open threads this sender holds to this owner, DECIDED FROM THE HOOK STREAM.

    `rows` is the ledger the caller already read under its lock — passed in rather than read
    again, so the comparison is against the same bytes the cap would otherwise have used and
    cannot drift between two reads of a file another process appends to.

    Returns the LEDGER's answer whenever the two disagree, and says so in the health stream.
    That is deliberate conservatism, not a hedge: while both stores are written (the campaign
    replaces the duplication last), the ledger is the record the operator's backlog counts and
    `self-audit`'s folds are built on, so a cap that refused a filing the ledger does not
    justify would be this module's defect reaching a run's work. The event is the thing that
    makes the disagreement findable — the state the step set out to make impossible.
    """
    ledger = open_threads(rows, routine=routine, target=target)
    try:
        stream = open_threads(stream_rows(routines_home), routine=routine, target=target)
    except OSError:
        log.exception("report-hooks: reading the hook stream for the thread cap raised")
        return ledger
    if sorted(stream) != sorted(ledger):
        detail = (f"the hook stream and the report ledger disagree about {routine}'s open "
                  f"threads to {target or 'triage'}: the stream says "
                  f"{sorted(stream) or 'none'}, the ledger says {sorted(ledger) or 'none'}. "
                  "The ledger's answer was used. Either a report was written without going "
                  "through the hook (hooks/reportproj is the one writer), or the backfill of "
                  "the pre-stream history is incomplete (migrate_report_hooks)")
        log_health_event(routines_home, "hook_stream_diverged", routine=routine, run_id="",
                         detail=detail, stream=len(stream), ledger=len(ledger))
        log.warning("report-hooks: %s", detail)
    return ledger
