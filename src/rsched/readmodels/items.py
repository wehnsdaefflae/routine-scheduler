"""Items: the system-maintenance index — every finding (`F<n>`), decision (`D<n>`), bug
and report (`R<n>`) the scheduler carries, with its status, purpose, origin, and the
changelog rows that addressed it.

Four files merge into one shape (docs/items.md is the spec):

- `<self-audit>/audit/report.json` — findings + decisions, and the CURRENT status. Always
  the authority: the changelog is an archive and never overrides it.
- `<self-audit>/audit/changelog.jsonl` — which commit addressed which item, when. Rows mix
  pretty-printed and compact JSON, so it is parsed with a streaming `raw_decode` loop; a
  line-oriented parser silently drops every multi-line row.
- `<self-audit>/audit/decisions-answered.json` — durable "the user answered it" markers.
- `<routines>/.control/reports.jsonl` — the ungated `report` stream: every problem a run
  raised, addressed to an owning routine or left for triage, plus the `delivered` event rows
  that say whether an addressed one was picked up. It is the status authority for an `R<n>`,
  the way `report.json` is for an `F<n>`.

A report holds only its own window, so most items live on solely through the changelog and
the answered markers — those are `archive_only` and carry no prose of their own. Findings
have no `status` field on disk yet (the self-audit routine will emit one from the spec on a
later run); an absent status reads `unknown` and is NEVER recovered from title prose.

Read-model discipline: nothing here writes, and the merge is memoized behind the four
files' stat fingerprint.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..paths import read_json
from ..reports import REPORTS_FILE, read_reports
from . import memo

SELF_AUDIT_SLUG = "self-audit"

#: The status vocabulary. `unknown` is the absence of a recorded status, not a state an
#: item is put into.
STATUSES = ("open", "in_progress", "addressed", "settled", "dropped", "unknown")

TYPE_BY_PREFIX = {"F": "finding", "D": "decision", "R": "report"}

#: Item ids in HISTORICAL prose — findings and decisions only (see `_row_ids`).
ID_RE = re.compile(r"\b([FD]\d{1,4})\b")
#: Item ids in CURRENT prose — report ids included.
REF_RE = re.compile(r"\b([FDR]\d{1,4})\b")


def _audit_dir(routine_dir: Path) -> Path:
    return routine_dir / "audit"


def source_paths(routine_dir: Path, routines_home: Path) -> list[Path]:
    """The four inputs, in the order the docs list them — also the memo fingerprint."""
    audit = _audit_dir(routine_dir)
    return [audit / "report.json", audit / "changelog.jsonl",
            audit / "decisions-answered.json",
            Path(routines_home) / ".control" / REPORTS_FILE]


# ---- source readers ---------------------------------------------------------------------


def read_changelog(path: Path) -> list[dict]:
    """Every changelog row in FILE ORDER (oldest first). The file mixes pretty-printed and
    compact objects; `raw_decode` walks it as a stream of JSON values, so a multi-line row
    is one row rather than a dozen unparseable lines.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    decoder = json.JSONDecoder()
    rows: list[dict] = []
    pos, end = 0, len(raw)
    while pos < end:
        while pos < end and raw[pos].isspace():
            pos += 1
        if pos >= end:
            break
        try:
            obj, pos = decoder.raw_decode(raw, pos)
        except json.JSONDecodeError:
            break                      # a truncated tail: keep everything parsed so far
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


# ---- the changelog join -----------------------------------------------------------------


def _row_ids(row: dict) -> tuple[list[str], str]:
    """The item ids one changelog row touched, and how they were found. An explicit
    `items: [...]` field is the only trusted join; older rows fall back to an `F<n>`/`D<n>`
    scan of their prose, flagged best-effort. The fallback never matches `R<n>` — bug ids
    postdate every historical row, so any `R` in old prose is a false positive.
    """
    explicit = [str(i).strip().upper() for i in (row.get("items") or []) if str(i).strip()]
    if explicit:
        return sorted(set(explicit)), "explicit"
    prose = " ".join(str(row.get(k) or "") for k in ("title", "summary", "detail"))
    return sorted(set(ID_RE.findall(prose))), "best-effort"


def _addressed_by_id(rows: list[dict]) -> dict[str, list[dict]]:
    """Map each item id to the changelog rows that touched it, newest first."""
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        ids, link = _row_ids(row)
        if not ids:
            continue
        entry = {"ts": str(row.get("ts") or ""), "commit": str(row.get("commit") or ""),
                 "run_id": str(row.get("run_id") or ""),
                 "summary": str(row.get("summary") or ""),
                 "title": str(row.get("title") or ""), "link": link}
        for item_id in ids:
            out[item_id].append(entry)
    for entries in out.values():
        entries.sort(key=lambda e: e["ts"], reverse=True)
    return dict(out)


# ---- item assembly ----------------------------------------------------------------------


def _refs(item_id: str, *parts: str) -> list[str]:
    """Other item ids named in this item's own prose (`F<n>`/`D<n>`/`R<n>`), so the graph is
    navigable. Unlike the changelog fallback this scan includes `R` — the prose is current,
    not historical.
    """
    found = set(REF_RE.findall(" ".join(parts)))
    return sorted(found - {item_id})


def _status_from_report(entry: dict) -> str:
    """The report's own status when it names one from the vocabulary. A value outside it is
    a data error and reads `unknown` — no synonym translation lives here.
    """
    raw = str(entry.get("status") or "").strip().lower()
    return raw if raw in STATUSES else "unknown"


def _report_item(kind: str, entry: dict, report: dict,
                 addressed: list[dict], answered: dict) -> dict:
    item_id = str(entry.get("id") or "").strip().upper()
    title, detail = str(entry.get("title") or ""), str(entry.get("detail") or "")
    since = report.get("since") if isinstance(report.get("since"), dict) else {}
    status = _status_from_report(entry)
    if status == "unknown" and kind == "decision" and answered.get(item_id):
        status = "settled"                      # the user answered it — a recorded fact
    item = {
        "id": item_id, "type": kind, "status": status, "title": title, "detail": detail,
        "origin": {"routine": SELF_AUDIT_SLUG, "run_id": str(report.get("run_id") or ""),
                   "ts": str(report.get("generated") or ""),
                   "commit": str((since or {}).get("commit") or "")},
        "addressed": addressed, "evidence": [], "refs": _refs(item_id, title, detail),
        "archive_only": False,
    }
    if kind == "finding":
        item["severity"] = str(entry.get("severity") or "")
        item["evidence"] = [str(e) for e in (entry.get("evidence") or [])]
    else:
        item["options"] = [str(o) for o in (entry.get("options") or [])]
        item["resolution"] = str(entry.get("resolution") or "")
    return item


def _report_row_item(row: dict, addressed: list[dict], closed_by: dict[str, str]) -> dict:
    """One `R<n>`: what was raised, by whom, and how far it has got.

    An UNADDRESSED report waits in the stream for triage, so its status comes from the
    changelog alone. An ADDRESSED one has a delivery lifecycle the ledger records, and that
    progression is the reason the ledger exists — it separates a hand-off that carried from
    one that silently never arrived. Precedence: `settled` when the row itself carries
    `closes: true` (a terminal acknowledgment, born settled — it asks nothing back) or when
    a later report carries `answers: "<this id>"` (the target replied, having acted or said
    why not; answering a closure works and changes nothing — it is already settled);
    `addressed` when a changelog row names the id; `in_progress` once the target's run
    drained it; otherwise `open`.
    """
    item_id = str(row.get("id") or "").strip().upper()
    title, detail = str(row.get("title") or ""), str(row.get("detail") or "")
    delivered = row.get("delivered") if isinstance(row.get("delivered"), dict) else {}
    if row.get("closes") or item_id in closed_by:
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
        "refs": _refs(item_id, title, detail, str(row.get("answers") or "")),
        "archive_only": False,
        "to": str(row.get("target") or ""),
        "delivered": delivered,
        "answers": str(row.get("answers") or ""),
        "closes": bool(row.get("closes")),
        "answered_by": closed_by.get(item_id, ""),
    }


def _archive_item(item_id: str, addressed: list[dict], answered: dict) -> dict:
    """An item no source holds a record of any more: it survives through the changelog or an
    answered marker alone. It carries no prose — the UI shows its newest `addressed` entry
    instead. Origin is the EARLIEST linked row: the first trace of it, not necessarily the
    moment it was raised.
    """
    kind = TYPE_BY_PREFIX.get(item_id[:1], "finding")
    first = addressed[-1] if addressed else {}
    marker = str(answered.get(item_id) or "")
    item = {
        "id": item_id, "type": kind,
        "status": "settled" if (kind == "decision" and marker)
                  else "addressed" if addressed else "unknown",
        "title": "", "detail": "",
        "origin": {"routine": SELF_AUDIT_SLUG, "run_id": str(first.get("run_id") or ""),
                   "ts": str(first.get("ts") or marker),
                   "commit": str(first.get("commit") or "")},
        "addressed": addressed, "evidence": [], "refs": [], "archive_only": True,
    }
    if kind == "finding":
        item["severity"] = ""
    elif kind == "decision":
        item["options"], item["resolution"] = [], ""
    elif kind == "report":
        item["to"], item["delivered"] = "", {}
        item["answers"], item["answered_by"] = "", ""
        item["closes"] = False
    return item


def _sort_key(item: dict) -> tuple:
    """Newest origin first; within one timestamp, the higher number first."""
    digits = "".join(c for c in item["id"] if c.isdigit())
    return (item["origin"]["ts"], int(digits or 0))


def build(routine_dir: Path, routines_home: Path) -> dict:
    """The merged index: `{"items": [...], "counts": {...}}`, newest origin first."""
    paths = source_paths(routine_dir, routines_home)
    key = f"items:{routine_dir}"
    return memo.memoized(key, paths, lambda: _build(*paths))


def _build(report_path: Path, changelog_path: Path,
           answered_path: Path, reports_path: Path) -> dict:
    report = read_json(report_path)
    report = report if isinstance(report, dict) else {}
    answered = read_json(answered_path)
    answered = answered if isinstance(answered, dict) else {}
    addressed = _addressed_by_id(read_changelog(changelog_path))

    items: dict[str, dict] = {}
    for kind, field in (("finding", "findings"), ("decision", "decisions")):
        for entry in report.get(field) or []:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id") or "").strip().upper()
            if item_id:
                items[item_id] = _report_item(kind, entry, report,
                                              addressed.get(item_id, []), answered)
    rows = read_reports(reports_path)
    # A report that names another in `answers` CLOSES it — the reply is the closure record,
    # so the map is built over the whole stream before any item is shaped.
    closed_by = {str(r.get("answers")).strip().upper(): str(r.get("id") or "")
                 for r in rows if str(r.get("answers") or "").strip()}
    for row in rows:
        item_id = str(row.get("id") or "").strip().upper()
        if item_id:
            items[item_id] = _report_row_item(row, addressed.get(item_id, []), closed_by)
    for item_id in [*addressed, *answered]:
        item_id = str(item_id).strip().upper()
        if item_id and item_id not in items and item_id[:1] in TYPE_BY_PREFIX:
            items[item_id] = _archive_item(item_id, addressed.get(item_id, []), answered)

    ordered = sorted(items.values(), key=_sort_key, reverse=True)
    return {"items": ordered, "counts": counts(ordered)}


def counts(items: list[dict]) -> dict:
    """Totals by type and by status — the filter chips' numbers, always over the
    UNFILTERED set so a chip never counts only what the current filter already shows.
    """
    by_type: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    for item in items:
        by_type[item["type"]] += 1
        by_status[item["status"]] += 1
    return {"type": dict(by_type), "status": dict(by_status)}


def filter_items(items: list[dict], *, type_: str = "", status: str = "",
                 routine: str = "", search: str = "") -> list[dict]:
    """Apply the API's filters. `search` is a case-insensitive substring over the id, the
    prose, and the addressed summaries — an archive-only item has no prose of its own, so
    its changelog summaries are the only way to find it by text.
    """
    needle = search.strip().lower()
    out = []
    for item in items:
        if type_ and item["type"] != type_:
            continue
        if status and item["status"] != status:
            continue
        if routine and item["origin"]["routine"] != routine:
            continue
        if needle:
            hay: list[Any] = [item["id"], item["title"], item["detail"]]
            hay += [a["summary"] for a in item["addressed"]]
            if needle not in " ".join(str(h) for h in hay).lower():
                continue
        out.append(item)
    return out
