"""User-flagged item priorities: the Messages page's "work this first" channel (D75).

The Items index (readmodels/items.py) merges findings (`F<n>`), decisions (`D<n>`) and
reports (`R<n>`) into one maintenance backlog, but the ORDER a routine worked it in was
the routine's own guess. The priority flag is the user's counter-signal: one ⚑ toggle on
an item card, stored here, that (a) floats the item to the top of the Messages page and
(b) reaches the OWNING routine's next run — `composer.state_digest` appends a "PRIORITY
items" section listing the flagged items that routine owns, so its orient stage reads
them before it plans.

Ownership is resolved at read time, never stored: an `R<n>`'s owner is its `target` — for
a row another report took over (`supersedes`), the target the carrier handed it to — and an
untargeted triage row belongs to self-audit, which owns triage. Every `F<n>`/`D<n>` lives in
self-audit's report.json whoever owns it, so its owner is read from the ROW
(`flagged_item_owner`, D240): the BUILDER for a row self-audit has handed over and that is
still in progress, self-audit otherwise — an `addressed` row back with the ledger keeper, so
a flag on finished work still reaches someone who can clear or answer it. Before that split
every flagged `F`/`D` resolved to self-audit, which is the routine that may not build them:
the operator's "work this first" reached the wrong routine for exactly the items it is most
likely to be set on. `readmodels/items.py` carries the same ownership on its cards (its
`owner` field, verbatim from the row self-audit wrote), so the Messages page and this digest
section name the same routine. The store
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
# Unbounded like the ledger's own `reports.REPORT_ID_RE`: the report counter passed R2000 in
# its second month, and a flag the API refused at R10000 would read as "not an item id".
ITEM_ID_RE = re.compile(r"^[FDR]\d+$")

#: Findings and decisions live in this routine's report.json; untargeted reports wait in
#: its triage. The ONE definition: readmodels/items.py (and the web routes reading through
#: it) import it from here, because the read model and this resolver must agree.
SELF_AUDIT_SLUG = "self-audit"

#: The routine that BUILDS what the ledger keeper hands over — feature development, split off
#: from self-audit's maintenance mandate (operator, 2026-10-01: no overlap between the two).
BUILDER_SLUG = "scheduler-builder"

#: The marker, owned HERE so the recipes' wording and this resolver cannot drift apart
#: silently: the literal line self-audit's `write-report` stage writes into a row's `detail`
#: when it hands that row to the builder (`Handed to scheduler-builder <date>: <what to
#: build>`, status `in_progress`). It is the only hand-over signal both sides read — a row
#: carrying `owner: scheduler-builder` and no such line reached the builder through nothing,
#: which is how eighteen handed rows sat unbuilt until 2026-10-23. tests/test_priorities.py
#: pins it against the real `detail` text of a live row.
HANDED_TO_BUILDER = f"Handed to {BUILDER_SLUG}"

#: The two routines a flagged `F<n>`/`D<n>` can belong to — both read self-audit's report.json,
#: which is where every such row lives whoever owns it.
FD_OWNERS = (SELF_AUDIT_SLUG, BUILDER_SLUG)


def flagged_item_owner(row: dict) -> str:
    """Which routine a flagged `F<n>`/`D<n>` belongs to, read from its ledger row.

    The BUILDER when self-audit has handed the row over and the work is still in progress: the
    marker line in `detail` AND `status: in_progress`. Both are required — the marker alone
    sits in the prose of rows self-audit has only written up (and in the prose of rows the
    builder already shipped, which a later run appends to behind the marker), and
    `in_progress` alone is self-audit's own ongoing work.

    The KEEPER otherwise, including a row the report no longer holds (nothing to read a
    hand-over from) and an `addressed` one: a flag on finished work still has to reach someone
    who can clear or answer it, and that is the routine that owns the ledger.

    Never stored, always read from the row — the module's rule for every id (the store holds
    flags, not ownership), so a hand-over or a completion moves the flag with no migration.
    """
    detail = str(row.get("detail") or "")
    status = str(row.get("status") or "").strip().lower()
    if status == "in_progress" and HANDED_TO_BUILDER in detail:
        return BUILDER_SLUG
    return SELF_AUDIT_SLUG


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
    the user's signal). An `F<n>`/`D<n>` is read from self-audit's report.json — which both
    `FD_OWNERS` consult, since that is where every such row lives — and routed to exactly
    one of them by `flagged_item_owner`; a row the report no longer holds still reaches the
    ledger keeper, for the same reason. Titles are best-effort: an id is always enough to
    look the item up on the Messages page.
    """
    flagged = read_priorities(routines_home)
    if not flagged:
        return []
    ordered = sorted(flagged, key=lambda i: str(flagged[i].get("ts") or ""))
    report_rows: dict[str, dict] = {}
    if any(item_id.startswith("R") for item_id in ordered):
        report_rows = {str(r.get("id") or "").upper(): r
                       for r in read_reports(reports_path(routines_home))}
    fd_rows: dict[str, dict] = {}
    if routine_slug in FD_OWNERS and any(item_id[0] in "FD" for item_id in ordered):
        report = read_json(Path(routines_home) / SELF_AUDIT_SLUG / "audit" / "report.json")
        if isinstance(report, dict):
            fd_rows = {str(e.get("id") or "").upper(): e
                       for field in ("findings", "decisions")
                       for e in (report.get(field) or []) if isinstance(e, dict)}
    out: list[dict] = []
    for item_id in ordered:
        if item_id.startswith("R"):
            row = report_rows.get(item_id)
            if row is None:
                continue
            # The Messages card's `to` (readmodels/item_reports): a FOLDED row is the carrier
            # target's now. Read as untargeted, a flagged triage row that was routed to its
            # owner kept landing in self-audit's digest — the re-routing F492 removed.
            folded = row.get("superseded")
            carrier_to = folded.get("to") if isinstance(folded, dict) else ""
            owner = str(row.get("target") or carrier_to or "") or SELF_AUDIT_SLUG
            if owner == routine_slug:
                out.append({"id": item_id, "title": str(row.get("title") or "")})
        elif routine_slug in FD_OWNERS:
            row = fd_rows.get(item_id) or {}
            if flagged_item_owner(row) == routine_slug:
                out.append({"id": item_id, "title": str(row.get("title") or "")})
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
