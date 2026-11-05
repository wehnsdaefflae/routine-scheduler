"""Every finding and decision self-audit's `report.json` has EVER carried, kept after it leaves.

`report.json` is rewritten WHOLE by every self-audit run, and the item index used to treat
whatever that rewrite held as the whole set: a row left out was gone unless some changelog row
happened to name it. Measured 2026-11-05 over all 239 committed versions of the file: five rows
left it while still `open`/`in_progress` with no changelog row and no answer — F216, F217, F322,
F355 and F445 — and so vanished from every surface, the work they described unfinished and
unrecorded. 200 more left closed and kept no record either. The recipe's own sentence ("items
that drop out of the report keep their history through the changelog") was true only for the
items some release happened to name.

So the record of a row is kept HERE, not in the file the routine rewrites: the newest version of
every `F<n>`/`D<n>` row ever seen, with when it was last seen and when it left. The item index
(`readmodels/items`) builds an item from this record for every id the current report no longer
holds, with its last recorded status — so a row that leaves the report while live stays LIVE in
the index and in every count, and only a recorded status change (or a release naming it after it
left) ends it. Losing an item now takes an act, never an omission.

ONE writer: the daemon's `item_record_watch`, which observes the file whenever it changes. It is
derived state under `.control/`, never config, and holds no judgement of its own — what it keeps
is exactly what self-audit wrote, verbatim.
"""

from __future__ import annotations

from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock, read_json

#: The two report.json arrays and the item type each holds.
KINDS = (("finding", "findings"), ("decision", "decisions"))

LIVE = ("open", "in_progress")


def records_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "item-records.json"


def read_records(routines_home: Path) -> dict[str, dict]:
    """`{id: record}` — `kind`, `entry` (the row verbatim), `report` (run_id, generated, since),
    `seen` (when last observed in a report) and `left` (when a report first omitted it, or "").
    """
    data = read_json(records_path(routines_home))
    return data if isinstance(data, dict) else {}


def _rows(report: dict) -> dict[str, tuple[str, dict]]:
    rows: dict[str, tuple[str, dict]] = {}
    for kind, field in KINDS:
        for entry in report.get(field) or []:
            if isinstance(entry, dict) and (rid := str(entry.get("id") or "").strip().upper()):
                rows[rid] = (kind, entry)
    return rows


def observe(routines_home: Path, report: dict, *, at: str = "") -> list[str]:
    """Record every row of `report`, and stamp `left` on every recorded row it omits. Returns
    the ids that left WHILE LIVE in this observation — the ones the index now keeps alive.

    A report with no rows at all is not observed: an empty or half-written file is not a
    rewrite that dropped everything, and recording it as one would stamp 464 departures.
    """
    rows = _rows(report)
    if not rows:
        return []
    at = at or now_iso()
    meta: dict[str, object] = {"run_id": str(report.get("run_id") or ""),
            "generated": str(report.get("generated") or ""),
            "since": report.get("since") if isinstance(report.get("since"), dict) else {}}
    path = records_path(routines_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    dropped: list[str] = []
    with file_lock(path.with_suffix(".lock")):
        records = read_records(routines_home)
        for rid, (kind, entry) in rows.items():
            records[rid] = {"kind": kind, "entry": entry, "report": meta, "seen": at, "left": ""}
        for rid, rec in records.items():
            if rid in rows or rec.get("left"):
                continue
            rec["left"] = at
            if str((rec.get("entry") or {}).get("status") or "").strip().lower() in LIVE:
                dropped.append(rid)
        atomic_write_json(path, records)
    return sorted(dropped, key=lambda i: (i[0], int(i[1:]) if i[1:].isdigit() else 0))
