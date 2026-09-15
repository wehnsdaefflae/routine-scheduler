"""An `R<n>`'s shape and status, derived from the report ledger alone.

Split out of `items.py` (which is at the ~350-line house cap) because it answers a different
question from the rest of that module. A finding's or a decision's status is READ from
`report.json`, where self-audit wrote it; a report has no such author. Its status is DERIVED,
here, from the event rows the engine stamped on it — delivered, retracted, superseded — plus
the later reports that answered or took it over. `readmodels/items.py` merges what this
returns with the audit record; the precedence rules are docs/items.md § Reports.

The fold (`superseded`) is why this needs two passes over the stream rather than one lookup
per row: a row handed to its owner reads the status of the THREAD it joined, and that thread
can itself have been folded into a later one.
"""

from __future__ import annotations

import re

#: Item ids in CURRENT prose — report ids included. Unlike the changelog's historical scan
#: (`items._row_ids`) this one matches `R<n>`: current prose can legitimately name a report,
#: where an `R` in an archived changelog row predates the namespace and is a false positive.
REF_RE = re.compile(r"\b([FDR]\d{1,4})\b")


def refs(item_id: str, *parts: str) -> list[str]:
    """Other item ids named in this item's own prose, so the graph is navigable."""
    return sorted(set(REF_RE.findall(" ".join(parts))) - {item_id})


def _event(row: dict, key: str) -> dict:
    """One folded EVENT stamp off a report row, or `{}`. A hand-trimmed ledger can leave a
    non-dict there, and three call sites reading it inline is three places for that to be a
    crash instead of an absence.
    """
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def report_row_item(row: dict, addressed: list[dict], closed_by: dict[str, str],
                     carrier_status: dict[str, str]) -> dict:
    """One `R<n>`: what was raised, by whom, and how far it has got.

    An UNADDRESSED report waits in the stream for triage, so its status comes from the
    changelog alone. An ADDRESSED one has a delivery lifecycle the ledger records, and that
    progression is the reason the ledger exists — it separates a hand-off that carried from
    one that silently never arrived. Precedence: `dropped` when the user RETRACTED it before
    the target consumed it (the recipient never saw it, so no other state can apply);
    **the CARRIER's status when this row was SUPERSEDED** — folding a row into another report
    makes that report its thread, so it reads whatever the thread reads and stops being its own
    open item (F492: a routed row that kept its own `open` was re-triaged and re-routed every
    run); `settled` when the row itself carries `closes: true` (a terminal acknowledgment, born
    settled — it asks nothing back) or when a later report carries `answers: "<this id>"`
    (the target replied, having acted or said why not; answering a closure works and changes
    nothing — it is already settled); `addressed` when a changelog row names the id;
    `in_progress` once the target's run drained it; otherwise `open`.
    """
    item_id = str(row.get("id") or "").strip().upper()
    title, detail = str(row.get("title") or ""), str(row.get("detail") or "")
    delivered, retracted, superseded = (_event(row, k) for k in
                                        ("delivered", "retracted", "superseded"))
    if retracted:
        status = "dropped"
    elif superseded:
        # The carrier is the thread now. An unknown carrier (a hand-trimmed ledger) reads
        # `in_progress`: the row was handed off, and whatever happened next is not visible.
        status = carrier_status.get(str(superseded.get("by") or ""), "in_progress")
    elif row.get("closes") or item_id in closed_by:
        status = "settled"
    elif addressed:
        status = "addressed"
    elif delivered:
        status = "in_progress"
    else:
        status = "open"
    return {
        "id": item_id, "type": "report", "status": status,
        "title": title, "detail": detail,
        "origin": {"routine": str(row.get("routine") or ""),
                   "run_id": str(row.get("run_id") or ""),
                   "ts": str(row.get("ts") or ""), "commit": ""},
        "addressed": addressed, "evidence": [],
        "refs": refs(item_id, title, detail, str(row.get("answers") or "")),
        "archive_only": False,
        # A folded row's OWNER is the carrier's target. Leaving it empty would keep saying
        # "nobody owns this" about a row that was handed over — which is the exact false
        # reading F492 is about, and what every "rows with no target" triage query reads.
        # How it got there is not lost: `superseded` names the carrier, and the card renders
        # "folded into R<n> → <owner>" rather than the ordinary sender→target line.
        "to": str(row.get("target") or superseded.get("to") or ""),
        "delivered": delivered,
        "retracted": retracted,
        "superseded": superseded,
        "supersedes": [str(i) for i in (row.get("supersedes") or [])],
        "answers": str(row.get("answers") or ""),
        "closes": bool(row.get("closes")),
        "answered_by": closed_by.get(item_id, ""),
    }


def resolved_carriers(rows: list[dict], carrier_status: dict[str, str]) -> dict[str, str]:
    """Report id → the status of the THREAD it belongs to, following `superseded.by` to the
    end of the chain. A carrier can itself be folded into a later report (a triage pass
    consolidating two earlier hand-offs), and the rows a run sees must read the status of the
    thread that is actually live rather than of an intermediate that is no longer anyone's.

    A cycle or a dangling `by` resolves to nothing and the caller's default applies — the
    ledger is append-only and neither is reachable by any write here, but a hand-trimmed file
    must not spin.
    """
    folds = {str(r.get("id") or "").upper(): str((r.get("superseded") or {}).get("by") or "")
             for r in rows if r.get("superseded")}
    out: dict[str, str] = dict(carrier_status)
    for start, first in folds.items():
        seen, at = {start}, first
        while at and at not in carrier_status and at in folds and at not in seen:
            seen.add(at)
            at = folds[at]
        if at in carrier_status:
            out[start] = carrier_status[at]
    return out
