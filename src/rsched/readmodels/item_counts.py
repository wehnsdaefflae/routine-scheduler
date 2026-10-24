"""Counts over the item index: the Messages page's chip numbers, and the one place where summing
statuses lies.

Split out of `items.py`, which builds the index; this module only reads it. The page's headline
number is `open`+`in_progress`, and summed naively it answers no question anybody asks. It
mixes three things that leave the list in different ways:

- a WORKLIST, which falls only when its OWNER acts,
- a FEED of unread run summaries, which come back every time any routine finishes, and
- reports IN TRANSIT, which no one can act on until the target's next scheduled run drains them,
- reports addressed to a routine that no longer EXISTS, which nobody can ever drain.

So `counts()` names each part, and splits the worklist BY OWNER. On 2026-10-18 the count read
100 and one of those items was the operator's to act on; it could only fall when another routine
acted, but nothing on the page said so.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection

LIVE = ("open", "in_progress")

#: The owner of an UNADDRESSED report: self-audit's triage stream, where it waits to be routed.
TRIAGE = "triage"

#: A finding or decision whose report.json row names no `owner`. It is left visibly unknown,
#: never inferred from the row's status or prose (docs/items.md: status is never recovered
#: from prose, and neither is ownership).
UNASSIGNED = "unassigned"


def owner_of(item: dict) -> str:
    """Who must act on a maintenance item next.

    A report's owner is the routine it is addressed TO (`to`), or `triage` when it has none. A
    finding or decision carries the `owner` self-audit wrote into its report.json row: a routine
    slug, or `operator` for an item only the operator can move (an open decision, for one).
    """
    if item["type"] == "report":
        return str(item.get("to") or "") or TRIAGE
    return str(item.get("owner") or "") or UNASSIGNED


def in_transit(item: dict, readers: Collection[str]) -> bool:
    """An ADDRESSED report its target has not drained yet, while that target is a routine that
    will run (`readers`). Delivery happens at the target's next scheduled run, which for a weekly
    routine is days away, and until then nobody can act on it.

    A report aimed at a routine that starts no run is NOT in transit: no delivery is coming, so
    it stays on the worklist under that owner, where it reads as the stuck row it is.
    """
    return (item["type"] == "report" and item["status"] == "open"
            and str(item.get("to") or "") in readers)


def unroutable(item: dict, installed: Collection[str]) -> bool:
    """An addressed report whose target is no routine this instance holds (`installed`).

    A routine that was deleted — or ARCHIVED, which `registry.scan` cannot see either, since it
    skips `.archive/` — leaves nothing a delivery could reach: no live directory, no inbox, no
    run. So the row can be drained by NOBODY, and on the worklist it reads as live work waiting
    on an owner, which is why R2629 (`steward-hub-maintainer` → `grants-radar`, archived
    2026-10-19) was counted as drainable backlog for days. It is counted and named APART, never
    dropped: the row is work somebody filed, and only a person can re-route or retract it.

    Not the same as a target that is switched off or retired — that routine EXISTS, the
    operator can switch it back on, and `in_transit` already keeps it on the worklist where it
    reads as stuck. `installed` is the routines a home holds whatever their state; empty means
    the caller has not said what the fleet is, so nothing is judged (as with `readers`).
    """
    if not installed or item["type"] != "report":
        return False
    target = str(item.get("to") or "")
    return bool(target) and target not in installed


def counts(items: list[dict], readers: Collection[str] = (),
           installed: Collection[str] = ()) -> dict:
    """Totals by type and by status — the filter chips' numbers, always over the UNFILTERED
    set so a chip never counts only what the current filter already shows — and `active`, the
    live items cross-tabulated into parts that never overlap:

    - `worklist` — maintenance items someone must act on now, with `by_owner` splitting it;
    - `unread` — summaries not yet read (a summary is `open` until read);
    - `folded` — rows another report TOOK OVER: they cannot be answered and settle with their
      carrier, so counting them would make ten defects carried in one hand-off read as ten
      open threads, and would put this page apart from self-audit's own vitals line;
    - `unroutable` — addressed reports whose target is no routine any more: nobody can drain
      them, so counting them as worklist showed live work waiting on an owner that is gone;
    - `in_transit` — addressed reports waiting for their target's next run.

    `readers` is the set of routines that will run; without it nothing is in transit.
    `installed` is every routine the home holds whatever its state; without it nothing is
    unroutable. Both are the CALLER's knowledge of the fleet — this module reads no disk.
    """
    by_type: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_owner: dict[str, int] = defaultdict(int)
    worklist = unread = folded = transit = stranded = 0
    for item in items:
        by_type[item["type"]] += 1
        by_status[item["status"]] += 1
        if item["status"] not in LIVE:
            continue
        if item["type"] == "summary":
            unread += item["status"] == "open"
        elif item.get("superseded"):
            folded += 1
        elif unroutable(item, installed):
            stranded += 1
        elif in_transit(item, readers):
            transit += 1
        else:
            worklist += 1
            by_owner[owner_of(item)] += 1
    owners = dict(sorted(by_owner.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"type": dict(by_type), "status": dict(by_status),
            "active": {"worklist": worklist, "unread": unread, "folded": folded,
                       "unroutable": stranded, "in_transit": transit, "by_owner": owners}}
