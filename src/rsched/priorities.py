"""User-flagged item priorities: the Messages page's "work this first" channel (D75).

The Items index (readmodels/items.py) merges findings (`F<n>`), decisions (`D<n>`) and
reports (`R<n>`) into one maintenance backlog, but the ORDER a routine worked it in was
the routine's own guess. The priority flag is the user's counter-signal: one ⚑ toggle on
an item card, stored here, that (a) floats the item to the top of the Messages page and
(b) reaches the OWNING routine's next run — `composer.state_digest` appends a "PRIORITY
items" section listing the flagged items that routine owns, so its orient stage reads
them before it plans.

Ownership is resolved at read time, never stored: an `R<n>`'s owner is its `target`
(an untargeted triage row belongs to self-audit, which owns triage), and every
`F<n>`/`D<n>` lives in self-audit's report.json, so self-audit owns those. The store
itself is one small JSON map under `.control/` — NOT report.json (self-audit rewrites
that wholesale every run, which would silently clobber a user's flag) and NOT
reports.jsonl (the append-only ledger records what runs SAID, not UI state).
"""

from __future__ import annotations

import re
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock, read_json
from .reports import read_reports, reports_path

PRIORITIES_FILE = "item-priorities.json"
ITEM_ID_RE = re.compile(r"^[FDR]\d{1,4}$")

#: Findings and decisions live in this routine's report.json; untargeted reports wait in
#: its triage. Mirrors readmodels/items.py — the read model and this resolver must agree.
SELF_AUDIT_SLUG = "self-audit"


def priorities_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / PRIORITIES_FILE


def read_priorities(routines_home: Path) -> dict[str, dict]:
    """The store: `{item_id: {"ts": iso}}` — an entry's presence IS the flag; unflagging
    removes it, so the file never accumulates dead rows.
    """
    data = read_json(priorities_path(routines_home))
    if not isinstance(data, dict):
        return {}
    return {str(k).upper(): v for k, v in data.items()
            if isinstance(v, dict) and ITEM_ID_RE.match(str(k).upper())}


def set_priority(routines_home: Path, item_id: str, on: bool) -> dict[str, dict]:
    """Flag or unflag one item; returns the updated store. Raises ValueError on a
    non-item id — the API surfaces that as a 400 rather than storing junk keys.
    """
    item_id = str(item_id).strip().upper()
    if not ITEM_ID_RE.match(item_id):
        raise ValueError(f"not an item id (F<n>/D<n>/R<n>): {item_id!r}")
    path = priorities_path(routines_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        current = read_priorities(routines_home)
        if on:
            current[item_id] = {"ts": now_iso()}
        else:
            current.pop(item_id, None)
        atomic_write_json(path, current)
    return current


def owned_priority_items(routines_home: Path, routine_slug: str) -> list[dict]:
    """The flagged items THIS routine owns, oldest flag first — each `{"id", "title"}`.

    A flagged `R<n>` that no longer exists in the ledger resolves to no owner and is
    skipped (the flag stays until the user clears it — silently dropping it would hide
    the user's signal). Titles are best-effort: an id is always enough to look the item
    up on the Messages page.
    """
    flagged = read_priorities(routines_home)
    if not flagged:
        return []
    ordered = sorted(flagged, key=lambda i: str(flagged[i].get("ts") or ""))
    report_rows: dict[str, dict] = {}
    if any(item_id.startswith("R") for item_id in ordered):
        report_rows = {str(r.get("id") or "").upper(): r
                       for r in read_reports(reports_path(routines_home))}
    titles: dict[str, str] = {}
    if routine_slug == SELF_AUDIT_SLUG and any(item_id[0] in "FD" for item_id in ordered):
        report = read_json(Path(routines_home) / SELF_AUDIT_SLUG / "audit" / "report.json")
        if isinstance(report, dict):
            titles = {str(e.get("id") or "").upper(): str(e.get("title") or "")
                      for field in ("findings", "decisions")
                      for e in (report.get(field) or []) if isinstance(e, dict)}
    out: list[dict] = []
    for item_id in ordered:
        if item_id.startswith("R"):
            row = report_rows.get(item_id)
            if row is None:
                continue
            owner = str(row.get("target") or "") or SELF_AUDIT_SLUG
            if owner == routine_slug:
                out.append({"id": item_id, "title": str(row.get("title") or "")})
        elif routine_slug == SELF_AUDIT_SLUG:
            out.append({"id": item_id, "title": titles.get(item_id, "")})
    return out


def digest_section(routines_home: Path, routine_slug: str) -> str:
    """The state-digest paragraph for one routine — empty string when nothing is flagged
    for it, so the digest carries no empty heading.
    """
    items = owned_priority_items(routines_home, routine_slug)
    if not items:
        return ""
    lines = "\n".join(f"- {i['id']}" + (f" — {i['title']}" if i["title"] else "")
                      for i in items)
    return ("PRIORITY items the user flagged for THIS routine (⚑ on the Messages page — "
            "address these ahead of other backlog this run, and cite each id in what you "
            "ship, route or report):\n" + lines)
