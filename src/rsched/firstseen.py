"""Item ORIGIN STAMPS: when this instance first saw each maintenance item (F753).

Every finding and decision on the Messages page was dated by `origin.ts` — the `generated`
date of the report that CONTAINS it. `self-audit` rewrites `audit/report.json` WHOLESALE
every run, so that date moved to "today" on every run: an item's age could not be read and
"the ten oldest items" was unmeasurable. self-audit confirmed it by direct measurement
(the /api/items rows carried no date field of any kind) and had to quota by the id ordinal
instead.

So first sight is recorded HERE, where no rewrite reaches it: one small JSON map under
`.control/`, beside the ⚑ flag store and for the same reasons (priorities.py) — NOT
report.json, which self-audit rewrites wholesale every run and which this repo may never
write, and NOT reports.jsonl, the append-only ledger of what runs SAID rather than what the
instance has seen.

WHO WRITES IT, and why a stamp can never be clobbered:

* The one writer is the `/api/items` SERVE path (`web/api_items.items`), which is the only
  place that holds the whole merged id set — findings, decisions and reports at once. It
  calls `stamp()` with those ids; `stamp()` only ever ADDS a key (`setdefault` under a file
  lock, written atomically), so a date already recorded is never moved. There is no API,
  no CLI and no routine that can set one: the store has no write channel to clobber with.
* It is NOT in the read model's memo fingerprint, deliberately, and the read model itself
  stays write-free (its docstring: "nothing here writes"). Two reasons. The stamp set can
  only be known AFTER the merge, so a stamp written during a request could not appear in
  that same request's memoized index — a brand-new item would be served undated exactly on
  the day it was born. And a fingerprinted store would invalidate the whole 177 KB merge on
  every request that discovers an id. Instead `annotate()` attaches the stamps to the rows
  the handler is about to serve, per request, off a value no memo holds.

WHAT AN UNSTAMPED ITEM READS AS — the backfill is not faked. An item that already existed
when stamping began has no true first-sight date, and inventing one (the ship date, "today",
the report's own `generated`) is exactly the false precision F753 is about. So the store
records two things the first pass knows: an `epoch` — the moment stamping began — and
`backfilled`, the ids that pass could not have witnessed because they were already on the page.
Those read as a FLOOR: `first_seen` = the oldest date the item can be PROVEN to predate, with
`first_seen_or_earlier` true, which the UI renders as "2026-07-22 or earlier" rather than as a
birthday. The floor is the earliest of the epoch and the item's own changelog traces — a
changelog row naming the id proves the item existed when that row was written, which is real
evidence and usually far older than the epoch. Every id stamped after that first pass is an
exact sighting (`first_seen_or_earlier` false). Nothing is ever back-dated into the store.

`backfilled` is a NAMED SET rather than "any stamp at or before the epoch" because `now_iso` is
SECOND-resolution: an item genuinely first seen inside the epoch's own second would otherwise
read as a floor it is not, and the distinction would rest on comparing near-equal strings.

Dates are otherwise compared as ISO strings, as the rest of the tree does
(`readmodels/change_fleet`, `priorities.owned_priority_items`): mixed UTC offsets make that an
approximation of a few hours, immaterial to an age read in days.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock, read_json
from .priorities import ITEM_ID_RE

FIRST_SEEN_FILE = "item-first-seen.json"

#: The two row fields, named once: `web/api_items` serves them, `docs/items.md` specifies
#: them and `static/components/item-age.js` renders them.
STAMP_FIELD = "first_seen"
FLOOR_FIELD = "first_seen_or_earlier"


def first_seen_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / FIRST_SEEN_FILE


def read_first_seen(routines_home: Path) -> dict:
    """The store, as `{"epoch": iso, "items": {item_id: iso}, "backfilled": {item_id}}`.

    `epoch` is when stamping began on this instance and `backfilled` names the ids that first
    pass found already present — those are floors rather than sightings. A key that is not an
    item id, or a value that is not a date string, is dropped on the way in: the store can hold
    no junk key even if something put one there.
    """
    data = read_json(first_seen_path(routines_home))
    if not isinstance(data, dict):
        return {"epoch": "", "items": {}, "backfilled": set()}
    raw = data.get("items")
    items: dict[str, str] = {}
    if isinstance(raw, dict):
        items = {str(k).upper(): v for k, v in raw.items()
                 if ITEM_ID_RE.match(str(k).upper()) and isinstance(v, str) and v.strip()}
    backfilled = {str(i).upper() for i in (data.get("backfilled") or [])
                  if ITEM_ID_RE.match(str(i).upper())}
    return {"epoch": str(data.get("epoch") or ""), "items": items, "backfilled": backfilled}


def stamp(routines_home: Path, item_ids: Iterable[str]) -> dict:
    """Record first sight of every id in `item_ids` the store does not hold yet, and return
    the store as `read_first_seen` reads it.

    A date already recorded is NEVER moved — the whole point of the file. Ids that are not
    item ids are filtered out rather than refused: the caller passes the index as it is, and
    a run SUMMARY's id is a run id (`<slug>:<ts>`) by design, not a malformed item.

    The pass that CREATES the store sets the epoch to the same moment it stamps with, so
    everything already on the page that day reads as a floor; later arrivals are exact.
    """
    seen = sorted(i for i in {str(x).strip().upper() for x in item_ids} if ITEM_ID_RE.match(i))
    store = read_first_seen(routines_home)
    if not seen or all(i in store["items"] for i in seen):
        return store
    path = first_seen_path(routines_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        store = read_first_seen(routines_home)        # another writer may have won the lock
        ts = now_iso()
        first_pass = not store["epoch"]
        epoch = store["epoch"] or ts
        for item_id in seen:
            store["items"].setdefault(item_id, ts)
            if first_pass:
                # The pass that CREATES the store cannot have witnessed anything: these items
                # were already on the page, so each is recorded as a floor by NAME. `now_iso`
                # is second-resolution, so "is this stamp younger than the epoch?" would read
                # an item stamped in the epoch's own second as a sighting it never was.
                store["backfilled"].add(item_id)
        atomic_write_json(path, {"epoch": epoch, "items": store["items"],
                                 "backfilled": sorted(store["backfilled"])})
        store["epoch"] = epoch
    return store


def row_stamp(row: dict, store: dict) -> tuple[str, bool]:
    """`(first_seen, or_earlier)` for one item row — see the module docstring.

    A REPORT and a run SUMMARY need no store at all: `.control/reports.jsonl` records when a
    report was filed and a summary's id IS its run, and neither record is ever rewritten. So
    they read their own `origin.ts` exactly, and no second date is invented for them.
    """
    filed = str((row.get("origin") or {}).get("ts") or "")
    item_id = str(row.get("id") or "").strip().upper()
    if not ITEM_ID_RE.match(item_id):
        return filed, False                           # a summary: its origin IS its run
    if item_id.startswith("R") and filed:
        return filed, False                           # the ledger's own filing time
    recorded = store["items"].get(item_id, "")
    if recorded and item_id not in store["backfilled"]:
        return recorded, False                        # the instance watched this one arrive
    floor = recorded or store["epoch"]
    traces = [str(a.get("ts") or "") for a in (row.get("addressed") or []) if a.get("ts")]
    candidates = [c for c in (floor, *traces) if c]
    return (min(candidates) if candidates else ""), True


def annotate(rows: Iterable[dict], store: dict) -> None:
    """Attach the two stamp fields to every row, in place — the handler's last step before
    it serves them. The rows are the memo's own deep copies (`readmodels.memo.memoized`),
    so this mutates nothing anybody else holds.
    """
    for row in rows:
        row[STAMP_FIELD], row[FLOOR_FIELD] = row_stamp(row, store)
