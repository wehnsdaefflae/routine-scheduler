"""The two ways work is lost from the ledger without ever becoming an open item.

Both are invisible to every filter on the Messages page, which is why they are banners above it
rather than rows inside it, and both are SURFACED rather than gated — a human judges the promise.

An item routinely defers part of its scope into another item: "the sidebar panel ships with
F324's shared component". The carrier then ships its OWN scope, closes, and the changelog `items`
join records the carrier as addressed. Nothing anywhere checks that what was deferred INTO it
actually shipped, because the deferral exists only as prose in a summary. So the deferred piece
does not become an open item, does not appear on any page, and is simply gone.

That is not hypothetical. D98's stopping-conditions **sidebar panel** was deferred into F324 on
2026-08-21; F324 shipped the rail on 2026-08-26, closed `addressed` naming R339/R340/R341/F336
as delivered, and the panel was never built. The feature stayed enforced in the prompt and the
finish gate while being invisible for six days, and nobody could have seen it from the ledger.
`tests/test_orphans.py` uses that exact history as its fixture.

## What counts as an orphan

A deferral names a carrier in one of the documented phrasings. It is an ORPHAN when:

1. the carrier item is CLOSED (`addressed` / `settled` / `dropped`), and
2. none of the deferring item's ids appears anywhere in the carrier's closure evidence — its
   own `detail`, or any changelog row that names the carrier.

Rule 2 is the whole check: an item that delivered what was deferred into it says so, because
that is how every closure note in this ledger is written. F336 was deferred into F324 the same
day as D98's panel and F324's closure names F336 — so F336 reads as delivered and D98 does not,
which is exactly the distinction that was missed by eye.

## The second: an addressed report that was never delivered

`file_report` writes the ledger row and the target's `inbox/msg-rep-<id>.json` in one call, so an
addressed report always has a message waiting. A row written any OTHER way — an operator batch
appended straight to the stream — has a `target` and no message, and the target can therefore
never see it, never drain it, and never stamp it `delivered`. It sits `open` forever, counted in
every backlog figure, addressed to a routine that has never heard of it. Twelve rows from the
2026-08-29 web-UI migration are exactly that (D114).

A row is UNDELIVERED when it names a target, carries no `delivered` and no `retracted` stamp, and
no `msg-rep-<id>.json` for it exists in that target's inbox — or the target is not a routine at
all. A retracted report is excluded by definition: retraction unlinks the message on purpose. A
report whose message is still SITTING in the inbox is not this — that is the normal state of a
report waiting for its target's next run, and the ledger already shows it.

## Why prose matching is acceptable here

The house rule is that a name-matching check over a DYNAMIC catalog turns unrelated things red
the day something is named after an ordinary word. This corpus is neither dynamic nor open: it is
an append-only ledger of closed items, matched against a fixed vocabulary of deferral phrasings
and `[FDR]` + digits ids. It also does not GATE anything — it surfaces a row for a human to
judge, so a false positive costs a glance, not a red build. The fix for a false positive is to
write the closure note so it names what it delivered — the behaviour this wants anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

CLOSED = ("addressed", "settled", "dropped")

#: The phrasings this ledger actually uses to defer work into another item, followed by that
#: item's id. Extending this list is how a new phrasing becomes visible to the check.
_CARRIER = re.compile(
    r"(?:ships? with|rides?(?: on)?|deferred to|defer(?:red)? into|folded in(?:to)?|"
    r"carried into|moves? (?:in)?to|next increment[^.]{0,40}?)"
    r"[^.]{0,90}?\b([FDR]\d+)", re.IGNORECASE)

_ID = re.compile(r"\b([FDR]\d+)\b")


def _mentions(text: str, ids: set[str]) -> bool:
    return bool(ids & set(_ID.findall(text or "")))


def find(findings: list[dict], rows: list[dict]) -> list[dict]:
    """Every deferral whose carrier closed without delivering it, newest source first.

    `findings` is report.json's list; `rows` is the changelog in file order.
    """
    status = {str(f.get("id")): str(f.get("status") or "") for f in findings}
    detail = {str(f.get("id")): str(f.get("detail") or "") for f in findings}
    # A carrier's CLOSURE evidence: its own detail, plus changelog rows whose `items` name it.
    # Deliberately NOT rows that merely mention it in prose — the deferring sentence names both
    # the source ids and the carrier ("D98 … deferred to F324"), so counting prose mentions
    # makes every deferral its own proof of delivery and the check finds nothing, ever.
    # `items` is the only join this ledger trusts (docs/items.md); the same rule applies here.
    evidence: dict[str, list[str]] = {fid: [txt] for fid, txt in detail.items()}
    for row in rows:
        text = str(row.get("summary") or "")
        for fid in {str(i) for i in (row.get("items") or [])}:
            evidence.setdefault(fid, []).append(text)

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sources: list[tuple[str, set[str], str, str]] = [
        (f"finding {f['id']}", {str(f["id"])}, str(f.get("detail") or ""), "")
        for f in findings if f.get("id")]
    sources += [
        (f"changelog {(r.get('commit') or '')[:8]}",
         {str(i) for i in (r.get("items") or [])},
         str(r.get("summary") or ""), str(r.get("ts") or ""))
        for r in rows]

    for label, src_ids, text, ts in sources:
        for m in _CARRIER.finditer(text):
            carrier = m.group(1)
            if carrier in src_ids or (label, carrier) in seen:
                continue
            if status.get(carrier, "") not in CLOSED:
                continue          # still open: the deferral is still tracked by the carrier
            if any(_mentions(ev, src_ids) for ev in evidence.get(carrier, [])):
                continue          # the carrier's closure names it — delivered
            seen.add((label, carrier))
            out.append({
                "source": label, "source_ids": sorted(src_ids), "carrier": carrier,
                "carrier_status": status.get(carrier, "unknown"), "ts": ts,
                # the sentence itself, so the reader judges the actual promise rather than
                # trusting this module's opinion of it
                "promise": text[max(0, m.start() - 90):m.end() + 80].strip(),
            })
    out.sort(key=lambda o: o["ts"], reverse=True)
    return out


def find_undelivered(reports: list[dict], routines_home: Path) -> list[dict]:
    """Every addressed report whose message never reached its target's inbox, newest first.

    `reports` is the folded stream (`rsched.reports.read_reports`). The check is the FILE, not the
    stamp: the stamp says a run has read the message, while the file's absence says no run ever
    can.
    """
    out: list[dict] = []
    for row in reports:
        target = str(row.get("target") or "")
        # A closure (answers + closes) is born settled — the terminal acknowledgment of an
        # exchange, asking nothing back — so an undelivered one is not lost work and never
        # belongs in this banner. Operator closures written straight to the stream
        # (R1152-R1156, 2026-09-04) lack an inbox file like any batch-appended row, but being
        # settled is exactly their point: there is nothing for the target to act on.
        if (not target or row.get("delivered") or row.get("retracted")
                or row.get("closes")):
            continue
        item_id = str(row.get("id") or "")
        inbox = Path(routines_home) / target / "inbox"
        if (inbox / f"msg-rep-{item_id}.json").exists():
            continue                      # waiting normally for the target's next run
        out.append({
            "kind": "undelivered", "id": item_id, "target": target,
            "target_exists": (Path(routines_home) / target / "routine.yaml").exists(),
            "from": str(row.get("routine") or ""), "ts": str(row.get("ts") or ""),
            "title": str(row.get("title") or ""),
        })
    out.sort(key=lambda o: o["ts"], reverse=True)
    return out


def load(routine_dir: Path) -> list[dict]:
    """`find` over the self-audit routine's own two files."""
    from ..paths import read_json
    from .items import read_changelog

    audit = routine_dir / "audit"
    report = read_json(audit / "report.json")
    findings = report.get("findings") or [] if isinstance(report, dict) else []
    rows = find(findings, read_changelog(audit / "changelog.jsonl"))
    for row in rows:
        row.setdefault("kind", "deferral")
    return rows


def load_undelivered(routines_home: Path) -> list[dict]:
    """`find_undelivered` over the live report stream."""
    from ..reports import read_reports, reports_path

    return find_undelivered(read_reports(reports_path(routines_home)), routines_home)
