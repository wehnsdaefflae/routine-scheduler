"""Items: the system-maintenance index — every finding (`F<n>`), decision (`D<n>`), bug
and report (`R<n>`) the scheduler carries, with its status, purpose, origin, and the
changelog rows that addressed it.

Five files merge into one shape (docs/items.md is the spec):

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

- `<routines>/.control/item-records.json` — the LAST RECORDED row of every finding and
  decision report.json ever carried (`rsched/item_records.py`). A row the current report no
  longer holds is built from it with its last status, so a row dropped while live stays live.

The row shapes themselves live in `item_rows.py`. An id no record ever held — one dropped
before the records existed — lives on solely through the changelog and the answered markers:
those are `archive_only` and carry no prose of their own. The self-audit
routine writes a `status` on every finding; one still reading `unknown` was written before it
did, and an absent status is NEVER recovered from title prose.

This model and `priorities.flagged_item_owner` MUST AGREE on who owns a finding or decision
(D240): a card's `owner` is the one self-audit wrote into the row (`item_rows.report_item`,
never inferred from status or prose), and the ⚑ resolver reads the hand-over marker in that
same row to pick the digest the flag lands in — so a row being built names
`scheduler-builder` on the Messages page and in the builder's digest alike
(`tests/test_priorities.py` pins the pair). Self-audit writes the marker line and the `owner`
field in one act; written apart, this page would show the field while the flag followed the
marker, and the two surfaces would name different routines.

Read-model discipline: nothing here writes, and the merge is memoized behind the stat
fingerprint of those files plus the ⚑ priorities store (`priorities.py`), whose flags float
an item to the top of the list. An item's AGE is NOT from here (`origin.ts` is its source's own
date): `firstseen.py` stamps the served rows, off a store it writes, so not a memo source (F753).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..item_records import read_records, records_path
from ..paths import read_json
from ..priorities import priorities_path, read_priorities
from ..reports import read_reports, reports_path
from . import decision_answers, memo
from .item_counts import owner_of
from .item_reports import event_stamp, report_row_item, resolved_carriers
from .item_rows import TYPE_BY_PREFIX, archive_item, recorded_item, report_item

#: Item ids in HISTORICAL prose — findings and decisions only (see `_row_ids`), with no digit
#: ceiling for the same reason as `item_reports.REF_RE`.
ID_RE = re.compile(r"\b([FD]\d+)\b")


def _audit_dir(routine_dir: Path) -> Path:
    return routine_dir / "audit"


def source_paths(routine_dir: Path, routines_home: Path) -> list[Path]:
    """The inputs, in the order the docs list them — also the memo fingerprint.
    The priorities store rides along so a ⚑ toggle invalidates the memo like any other
    source edit.
    """
    audit = _audit_dir(routine_dir)
    # The audit INBOX rides along because a queued `[AUDIT decision · …] selected: …` message
    # carries what the user chose (`queued_answer_words`): without it his answer would not
    # invalidate this memo, and the index would keep serving the decision as unresolved until
    # some other source changed. `decisions_read._audit_decisions` fingerprints the same dir
    # for the same reason.
    return [audit / "report.json", audit / "changelog.jsonl",
            audit / "decisions-answered.json", routine_dir / "inbox",
            reports_path(routines_home), priorities_path(routines_home),
            records_path(routines_home)]


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


def _sort_key(item: dict) -> tuple:
    """Newest origin first; within one timestamp, the higher number first."""
    digits = "".join(c for c in item["id"] if c.isdigit())
    return (item["origin"]["ts"], int(digits or 0))


def build(routine_dir: Path, routines_home: Path) -> dict:
    """The merged index: `{"items": [...]}`, newest origin first. Its counts are
    `item_counts.counts`, taken by the caller over whatever it merges in (the summaries).
    """
    return memo.memoized(f"items:{routine_dir}", source_paths(routine_dir, routines_home),
                         lambda: _build(routine_dir, routines_home))


def _build(routine_dir: Path, routines_home: Path) -> dict:
    audit = _audit_dir(routine_dir)
    report = read_json(audit / "report.json")
    report = report if isinstance(report, dict) else {}
    answered = read_json(audit / "decisions-answered.json")
    answered = answered if isinstance(answered, dict) else {}
    answer_words = decision_answers.queued_answer_words(routine_dir)
    addressed = _addressed_by_id(read_changelog(audit / "changelog.jsonl"))

    items: dict[str, dict] = {}
    for kind, field in (("finding", "findings"), ("decision", "decisions")):
        for entry in report.get(field) or []:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id") or "").strip().upper()
            if item_id:
                items[item_id] = report_item(kind, entry, report,
                                              addressed.get(item_id, []), answered,
                                              answer_words)
    rows = read_reports(reports_path(routines_home))
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
        if item_id and not event_stamp(row, "superseded"):
            items[item_id] = report_row_item(row, addressed.get(item_id, []), closed_by, {})
            carrier_status[item_id] = items[item_id]["status"]
    resolved = resolved_carriers(rows, carrier_status)
    for row in rows:
        item_id = str(row.get("id") or "").strip().upper()
        if item_id and event_stamp(row, "superseded"):
            items[item_id] = report_row_item(row, addressed.get(item_id, []), closed_by,
                                              resolved)
    # A row the report no longer holds is built from its LAST RECORD (item_records), so one
    # dropped while live stays live; only an id no record ever held falls to the changelog.
    for item_id, record in read_records(routines_home).items():
        if item_id not in items and isinstance(record, dict):
            items[item_id] = recorded_item(record, addressed.get(item_id, []), answered,
                                           answer_words)
    for item_id in [*addressed, *answered]:
        item_id = str(item_id).strip().upper()
        if item_id and item_id not in items and item_id[:1] in TYPE_BY_PREFIX:
            items[item_id] = archive_item(item_id, addressed.get(item_id, []), answered)

    flagged = set(read_priorities(routines_home))
    for item in items.values():
        if item["id"] in flagged:
            item["priority"] = True
    ordered = sorted(items.values(), key=_sort_key, reverse=True)
    # A ⚑ outranks recency: the user's explicit "work this first" floats above the feed
    # (stable sort — flagged and unflagged each keep newest-origin-first inside their band).
    ordered.sort(key=lambda i: not i.get("priority"))
    return {"items": ordered}


def filter_items(items: list[dict], *, type_: str = "", status: str = "",
                 routine: str = "", target: str = "", owner: str = "", search: str = "",
                 folded: bool = False) -> list[dict]:
    """Apply the API's filters. `search` is a case-insensitive substring over the id, the
    prose, and the addressed summaries — an archive-only item has no prose of its own, so
    its changelog summaries are the only way to find it by text.

    `routine` and `target` are the two ENDS of a report and they are not interchangeable:
    `routine` matches who FILED an item (its origin), `target` who it was addressed TO. Only
    the origin end existed until 0.326.0, so a routine asking for the work addressed to it had
    no filter that expressed the question and got the whole store back instead — a 1 MB body
    that truncates into invalid JSON for the caller (R1404). An item with no target (a finding,
    a decision, an untriaged report) matches no `target` query. `owner` is who must act next
    (`item_counts.owner_of`), the split the page's headline number is broken into; a summary
    has none.
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
        if owner and (item["type"] == "summary" or owner_of(item) != owner):
            continue
        if needle:
            hay: list[Any] = [item["id"], item["title"], item["detail"]]
            hay += [a["summary"] for a in item["addressed"]]
            if needle not in " ".join(str(h) for h in hay).lower():
                continue
        out.append(item)
    return out
