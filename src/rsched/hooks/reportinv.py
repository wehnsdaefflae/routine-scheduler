"""The ledger row read back AS the hook it was — the inverse of `hooks/reportproj`.

Campaign step 7 of D152. Step 4 made the hook the SOURCE of every new report (`reportproj`):
the hook is written first and `reports.jsonl` is computed from it. The stream began on
2026-10-19; the ledger began on 2026-07-19, and 3,952 of its 5,016 rows then predated the
stream. Before any decision could read the stream that history had to be carried over, and that
needed the projection read backwards — `hook_of_row`. It is exact rather than approximate
because `reportproj.ledger_row` and `event_row` are total: measured against the live ledger, no
filing or event row carries a key outside what they emit, and no id is stored in a case the
normalization would change. The round trip (`rows_for(hook_of_row(row)) == row`) is what keeps
"the ledger is a projection" a checked claim rather than a sentence.

Leniently, like `from_record` and for the same reason: this reads rows written by every build
since July. One live row (R1062, 2026-08-29) carries `closes` with neither `answers` nor
`settles` — a shape today's `Disposal` REFUSES (D196). It is history and must read back as
what it says, so the payload is assembled directly here rather than through `Disposal`, and
the refusal stays where it belongs: on the write path, judging what a run files NOW.

The decisions that once compared the two stores here (the open-thread cap, the owed-reply hold)
read the stream alone since the comparison was retired: they take `reports.read_reports`' rows
like every other reader (`report_threads.open_threads` / `still_owed`).
"""

from __future__ import annotations

from . import Hook, addressee
from .reportproj import TERMINAL_EVENTS


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

