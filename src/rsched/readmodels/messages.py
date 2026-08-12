"""The four-folder MESSAGES read model (D74 phase 1 — operator order 2026-08-05).

Every text in this system is for or from an individual routine (or both). This read model
folds the EXISTING stores into the four folders the operator ordered for each routine's
page — a READ MODEL: nothing here writes a message.

    inbox     — waiting for THIS routine's next run: `<routine>/inbox/msg-*.json`
                (user-filed, engine-filed, or a delivered report — the inbox file IS the
                delivery vehicle). User-creatable/editable/deletable while it waits.
    outbox    — reports THIS routine addressed to another that the recipient has NOT yet
                consumed: ledger order rows with `target` and no `delivered` stamp. A
                retracted row (`reports.retract_report`) leaves the folder — it is neither
                waiting nor consumed; the Messages page still lists it, status `dropped`.
    read      — already consumed by THIS routine: `runs/<ts>/consumed/msg-*.json`,
                newest first. Read-only — history, not a work queue.
    received  — reports THIS routine addressed to another that the recipient HAS
                consumed: ledger rows with a `delivered` stamp. Read-only.

`answer-*` files (question answers) stay OFF this surface on purpose: they belong to the
Decisions page's record, and rendering them as messages would fork that vocabulary.
The write half lives in `web/api_messages.py`; the decision record for what is and is not
writable per folder is docs/messages.md.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import read_json
from ..reports import read_reports, reports_path

READ_CAP = 50            # the read folder is history — cap it, newest first


def _msg_row(path: Path, *, folder: str, editable: bool, run_ts: str = "") -> dict:
    rec = read_json(path)
    rec = rec if isinstance(rec, dict) else {}
    return {"folder": folder, "file": path.name, "ts": str(rec.get("ts") or ""),
            "text": str(rec.get("text") or ""),
            "from": str(rec.get("from") or rec.get("source") or rec.get("via") or "user"),
            **({"report": rec.get("report")} if rec.get("report") else {}),
            **({"run_ts": run_ts} if run_ts else {}),
            "editable": editable}


def _report_row(row: dict, *, folder: str) -> dict:
    return {"folder": folder, "report": row.get("id"), "ts": str(row.get("ts") or ""),
            "to": str(row.get("target") or ""), "title": str(row.get("title") or ""),
            "text": str(row.get("detail") or ""),
            **({"delivered": row["delivered"]} if row.get("delivered") else {}),
            "editable": False}


def build(routine_dir: Path, routines_home: Path) -> dict:
    """The four folders for one routine, each newest-first."""
    slug = routine_dir.name
    inbox = [_msg_row(p, folder="inbox", editable=True)
             for p in sorted((routine_dir / "inbox").glob("msg-*.json"))]
    read: list[dict] = []
    runs_dir = routine_dir / "runs"
    for run in sorted(runs_dir.iterdir(), reverse=True) if runs_dir.is_dir() else []:
        read.extend(_msg_row(p, folder="read", editable=False, run_ts=run.name)
                    for p in sorted((run / "consumed").glob("msg-*.json"), reverse=True))
        if len(read) >= READ_CAP:
            break
    read = read[:READ_CAP]
    outbox: list[dict] = []
    received: list[dict] = []
    for row in read_reports(reports_path(routines_home)):
        if row.get("routine") != slug or not row.get("target"):
            continue
        if row.get("delivered"):
            received.append(_report_row(row, folder="received"))
        elif not row.get("retracted"):
            outbox.append(_report_row(row, folder="outbox"))
    inbox.reverse()
    outbox.reverse()
    received.reverse()
    return {"inbox": inbox, "outbox": outbox, "read": read, "received": received}
