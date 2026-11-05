"""The shapes of a FINDING or DECISION item — from a report row, from a row's last record
after the report dropped it, and from the changelog alone — split out of `items.py`, which
merges them (docs/items.md is the spec).

The middle shape is the one that keeps an item from vanishing (`rsched/item_records.py`): a
row self-audit's rewrite left out is built from its last recorded entry, with its last recorded
status, so a row dropped while live stays live. It is ended only by a recorded status, or by a
changelog row that addressed it AFTER it left — a release naming it is a fact newer than the
record, while one from before it left was already in the status the record carries.
"""

from __future__ import annotations

from datetime import datetime

from ..priorities import SELF_AUDIT_SLUG
from .item_reports import refs

#: The status vocabulary. `unknown` is the absence of a recorded status, not a state an
#: item is put into.
STATUSES = ("open", "in_progress", "addressed", "settled", "dropped", "unknown")

TYPE_BY_PREFIX = {"F": "finding", "D": "decision", "R": "report"}

LIVE = ("open", "in_progress")


def status_from_report(entry: dict) -> str:
    """The report's own status when it names one from the vocabulary. A value outside it is
    a data error and reads `unknown` — no synonym translation lives here.
    """
    raw = str(entry.get("status") or "").strip().lower()
    return raw if raw in STATUSES else "unknown"


def report_item(kind: str, entry: dict, report: dict,
                 addressed: list[dict], answered: dict,
                 answer_words: dict | None = None) -> dict:
    item_id = str(entry.get("id") or "").strip().upper()
    title, detail = str(entry.get("title") or ""), str(entry.get("detail") or "")
    since = report.get("since") if isinstance(report.get("since"), dict) else {}
    status = status_from_report(entry)
    if status == "unknown" and kind == "decision" and answered.get(item_id):
        status = "settled"                      # the user answered it — a recorded fact
    # An `open` status is NOT overridden here, deliberately. A widening to
    # `status in ("unknown", "open")` was built on 2026-10-25 and reverted the same run:
    # `tests/test_api.py::test_items_page_reflects_answered_decision_after_consumption` pins
    # that an item's status comes from the REPORT (self-audit's judgement, where `settled`
    # means the item is dealt with) while the answer is an OVERLAY the pages apply from
    # `web/api_audit.answered_decisions`. The live case says the same: D213 was answered on
    # 2026-10-25 and the work it authorized (F756) is still unshipped, so `settled` would
    # misreport it. What was missing was never the status — it was the resolution below.
    item = {
        "id": item_id, "type": kind, "status": status, "title": title, "detail": detail,
        "origin": {"routine": SELF_AUDIT_SLUG, "run_id": str(report.get("run_id") or ""),
                   "ts": str(report.get("generated") or ""),
                   "commit": str((since or {}).get("commit") or "")},
        "addressed": addressed, "evidence": [], "refs": refs(item_id, title, detail),
        "archive_only": False, "owner": str(entry.get("owner") or "").strip(),
    }
    if kind == "finding":
        item["severity"] = str(entry.get("severity") or "")
        item["evidence"] = [str(e) for e in (entry.get("evidence") or [])]
    else:
        item["options"] = [str(o) for o in (entry.get("options") or [])]
        item["resolution"] = str(entry.get("resolution") or "")
        if not item["resolution"] and (marker := str(answered.get(item_id) or "")):
            # A settled decision with an EMPTY resolution records nothing — the status says
            # "answered" and no surface can say what was decided. The report's own
            # `resolution` always wins (self-audit's considered wording, written by its next
            # run); this fills the gap before that run happens, from the two records the
            # answer actually wrote.
            item["resolution"] = (answer_words or {}).get(item_id) or f"answered {marker}"
    return item


def archive_item(item_id: str, addressed: list[dict], answered: dict) -> dict:
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


def _instant(raw: object) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    return when if when.tzinfo else None


def recorded_item(record: dict, addressed: list[dict], answered: dict,
                  answer_words: dict | None = None) -> dict:
    """An item the current report no longer holds, from its LAST RECORDED row (item_records).

    Its status is the recorded one — a row dropped while live stays live — unless a changelog
    row addressed it after it left. A record with NO status (rows written before self-audit
    emitted one) falls back to the archive rule: `addressed` when a changelog row names it.
    `left_report` says when the report omitted it, which is what a surface needs to tell
    "self-audit closed it" apart from "self-audit lost it".
    """
    raw_entry, raw_meta = record.get("entry"), record.get("report")
    entry: dict = raw_entry if isinstance(raw_entry, dict) else {}
    meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
    kind = str(record.get("kind") or TYPE_BY_PREFIX.get(str(entry.get("id"))[:1], "finding"))
    item = report_item(kind, entry, meta, addressed, answered, answer_words)
    item["left_report"] = str(record.get("left") or "")
    if item["status"] == "unknown" and addressed:
        item["status"] = "addressed"    # recorded before statuses existed: the archive rule
    left = _instant(item["left_report"])
    if item["status"] in LIVE and left is not None and any(
            (when := _instant(row.get("ts"))) is not None and when > left for row in addressed):
        item["status"] = "addressed"
    return item
