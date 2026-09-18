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
from ..priorities import priorities_path
from ..reports import REPORTS_FILE, read_reports
from . import memo
from .item_reports import refs, report_row_item, resolved_carriers

SELF_AUDIT_SLUG = "self-audit"

#: The status vocabulary. `unknown` is the absence of a recorded status, not a state an
#: item is put into.
STATUSES = ("open", "in_progress", "addressed", "settled", "dropped", "unknown")

TYPE_BY_PREFIX = {"F": "finding", "D": "decision", "R": "report"}

#: Item ids in HISTORICAL prose — findings and decisions only (see `_row_ids`).
ID_RE = re.compile(r"\b([FD]\d{1,4})\b")


def _audit_dir(routine_dir: Path) -> Path:
    return routine_dir / "audit"


def source_paths(routine_dir: Path, routines_home: Path) -> list[Path]:
    """The five inputs, in the order the docs list them — also the memo fingerprint.
    The priorities store rides along so a ⚑ toggle invalidates the memo like any other
    source edit.
    """
    audit = _audit_dir(routine_dir)
    return [audit / "report.json", audit / "changelog.jsonl",
            audit / "decisions-answered.json",
            Path(routines_home) / ".control" / REPORTS_FILE,
            priorities_path(routines_home)]


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
        "addressed": addressed, "evidence": [], "refs": refs(item_id, title, detail),
        "archive_only": False,
    }
    if kind == "finding":
        item["severity"] = str(entry.get("severity") or "")
        item["evidence"] = [str(e) for e in (entry.get("evidence") or [])]
    else:
        item["options"] = [str(o) for o in (entry.get("options") or [])]
        item["resolution"] = str(entry.get("resolution") or "")
    return item


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
        item["to"], item["delivered"], item["retracted"] = "", {}, {}
        item["superseded"], item["supersedes"] = {}, []
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
           answered_path: Path, reports_path: Path, priorities_file: Path) -> dict:
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
    # A report CLOSES the rows it disposes of — the reply is the closure record, so the map is
    # built over the whole stream before any item is shaped. A RETRACTED reply settles nothing:
    # the target's question never got its answer delivered.
    #
    # TWO faces, one map (D134). `answers` names the ONE exchange a reply belongs to; `settles`
    # names every row it terminally disposes of. The map used to be built from `answers` alone,
    # which is why a reply answering several rows in its own prose settled exactly one of them
    # and the rest aged on with an empty `answered_by` (F497: R1585 answered R1308 and R1328,
    # closed R1527, and left both at 11 and 12 days). An EARLIER settlement wins over a later
    # one — the row was finished when it was first declared finished.
    closed_by: dict[str, str] = {}
    for r in rows:
        if r.get("retracted"):
            continue
        reply_id = str(r.get("id") or "")
        for raw in (str(r.get("answers") or ""), *(r.get("settles") or [])):
            settled_id = str(raw).strip().upper()
            if settled_id:
                closed_by.setdefault(settled_id, reply_id)
    # A SUPERSEDED row reads its carrier's status, so the carriers have to be shaped first.
    # Two passes over the same rows rather than a recursive read: the fold is a chain (a
    # carrier can itself be folded into a later one), and resolving it in place would make an
    # item's status depend on ledger ORDER instead of on the thread it ended up in.
    carrier_status: dict[str, str] = {}
    for row in rows:
        item_id = str(row.get("id") or "").strip().upper()
        if item_id and not row.get("superseded"):
            items[item_id] = report_row_item(row, addressed.get(item_id, []), closed_by, {})
            carrier_status[item_id] = items[item_id]["status"]
    resolved = resolved_carriers(rows, carrier_status)
    for row in rows:
        item_id = str(row.get("id") or "").strip().upper()
        if item_id and row.get("superseded"):
            items[item_id] = report_row_item(row, addressed.get(item_id, []), closed_by,
                                              resolved)
    for item_id in [*addressed, *answered]:
        item_id = str(item_id).strip().upper()
        if item_id and item_id not in items and item_id[:1] in TYPE_BY_PREFIX:
            items[item_id] = _archive_item(item_id, addressed.get(item_id, []), answered)

    prior = read_json(priorities_file)
    flagged = ({str(k).upper() for k, v in prior.items() if isinstance(v, dict)}
               if isinstance(prior, dict) else set())
    for item in items.values():
        if item["id"] in flagged:
            item["priority"] = True
    ordered = sorted(items.values(), key=_sort_key, reverse=True)
    # A ⚑ outranks recency: the user's explicit "work this first" floats above the feed
    # (stable sort — flagged and unflagged each keep newest-origin-first inside their band).
    ordered.sort(key=lambda i: not i.get("priority"))
    return {"items": ordered, "counts": counts(ordered)}


def counts(items: list[dict]) -> dict:
    """Totals by type and by status — the filter chips' numbers, always over the
    UNFILTERED set so a chip never counts only what the current filter already shows.

    `active` cross-tabulates the two, because the page's headline number is the one place
    where summing them lies. `open`+`in_progress` mixes a WORKLIST (maintenance items someone
    has to act on) with a FEED (unread run summaries, which are `open` until read and reappear
    every time any routine finishes). Reported as one figure it can never fall to zero by
    working the backlog, so a steady worklist reads as a growing one — which is exactly how
    2026-09-15's "53 open, why does it only ever grow" started: 37 items and 16 unread.
    `type`/`status` cannot answer it on their own — neither is conditioned on the other, and
    the type totals are lifetime rather than active.
    """
    by_type: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    worklist = unread = folded = 0
    for item in items:
        by_type[item["type"]] += 1
        by_status[item["status"]] += 1
        if item["type"] == "summary":
            unread += item["status"] == "open"
        elif item.get("superseded"):
            # A FOLDED row is counted apart, never into the worklist: it cannot be answered,
            # and it settles when the report that took it over does. Folding it in would make
            # ten defects carried in one hand-off read as ten open threads — the inflation the
            # fold exists to remove, and it would put this page 14 apart from self-audit's own
            # vitals line over the same ledger.
            folded += item["status"] in ("open", "in_progress")
        else:
            worklist += item["status"] in ("open", "in_progress")
    return {"type": dict(by_type), "status": dict(by_status),
            "active": {"worklist": worklist, "unread": unread, "folded": folded}}


def filter_items(items: list[dict], *, type_: str = "", status: str = "",
                 routine: str = "", target: str = "", search: str = "",
                 folded: bool = False) -> list[dict]:
    """Apply the API's filters. `search` is a case-insensitive substring over the id, the
    prose, and the addressed summaries — an archive-only item has no prose of its own, so
    its changelog summaries are the only way to find it by text.

    `routine` and `target` are the two ENDS of a report and they are not interchangeable:
    `routine` matches who FILED an item (its origin), `target` who it was addressed TO. Only
    the origin end existed until 0.326.0, so a routine asking for the work addressed to it had
    no filter that expressed the question and got the whole store back instead — a 1 MB body
    that truncates into invalid JSON for the caller (R1404). An item with no target (a finding,
    a decision, an untriaged report) matches no `target` query.
    """
    needle = search.strip().lower()
    # A FOLDED row is hidden from BROWSING and never from LOOKING SOMETHING UP: it belongs to
    # the thread that took it over (whose card names it), so it is not a peer in a list — but
    # a search, a reflink or the explicit `folded` chip must still reach it.
    show_folded = folded or bool(needle)
    # `status` accepts a comma list ("open,in_progress") — the Items page's default
    # "active" view is exactly that pair, and one param beats a second filter channel.
    wanted_status = {s.strip() for s in status.split(",") if s.strip()}
    out = []
    for item in items:
        if item.get("superseded") and not show_folded:
            continue
        if type_ and item["type"] != type_:
            continue
        if wanted_status and item["status"] not in wanted_status:
            continue
        if routine and item["origin"]["routine"] != routine:
            continue
        if target and str(item.get("to") or "") != target:
            continue
        if needle:
            hay: list[Any] = [item["id"], item["title"], item["detail"]]
            hay += [a["summary"] for a in item["addressed"]]
            if needle not in " ".join(str(h) for h in hay).lower():
                continue
        out.append(item)
    return out
