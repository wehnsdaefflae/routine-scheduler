"""MIGRATION(expires=2026-12-15): fold the rows that were ROUTED before routing left a trace.

F492. Handing an untargeted report to its owner used to produce a new report that merely NAMED
the original; the original kept its own `open` status and its empty `target`, so the next
triage pass found it, decided it was untriaged, and routed it again. The measurement that
opened the finding: `BACKLOG … triage=17` on 2026-09-15, of which THIRTEEN had been routed that
same run — and R1491/R1496/R1516/R1517/R1519/R1520 had already been routed once the day before,
in R1525, before R1558 routed them a second time.

0.345.0 gives routing a trace (`supersedes` → a `superseded` event row). This carries the four
hand-offs that were already made across, so the first triage pass after the upgrade does not
route them a third time.

The mapping is HARD-CODED, not derived. A prose scan for `R<n>` over a carrier's body is
exactly the false-positive machine this migration exists to clean up after: R1559's body names
R1352 (an escalation it cites) and R1529 (a neighbour) besides the two rows it actually routes,
and folding those would silently retire two live items. Each list below is what that carrier's
own TITLE declares it is routing, read once by hand.

Where two carriers routed the same row, it folds into the LATER one: that is the thread the
owner will actually drain, and a row belongs to exactly one thread.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .ids import now_iso
from .paths import file_lock
from .reports import read_reports, reports_path

log = logging.getLogger("rsched.migrate")

#: carrier id → the rows its title declares it routes, with its target. R1525 keeps only R1518:
#: the other six it routed were routed again by R1558 the next day, and the live thread wins.
ROUTED: dict[str, tuple[str, tuple[str, ...]]] = {
    "R1525": ("global-utils-review", ("R1518",)),
    "R1558": ("global-utils-review", ("R1491", "R1496", "R1516", "R1517", "R1519", "R1520",
                                      "R1547", "R1548", "R1554", "R1557")),
    "R1559": ("steward-hub-maintainer", ("R1528", "R1530")),
    "R1561": ("llmsectest-weekday", ("R1553",)),
}


def migrate(routines_home: Path) -> list[str]:
    """Stamp one `superseded` event per row still waiting to be folded. Returns a log line per
    carrier — nothing is silent.

    Idempotent and conservative in both directions: a row already folded (by this migration or
    by a real `supersedes` since) is left alone, and so is one that has since been RETRACTED or
    given a `target` of its own — both mean something happened to it after the hand-off, and
    this migration is only entitled to record the hand-off itself.
    """
    path = reports_path(routines_home)
    if not path.exists():
        return ["no report ledger — nothing to fold"]
    notes: list[str] = []
    with file_lock(path.with_suffix(".lock")):
        rows = {str(r.get("id") or ""): r for r in read_reports(path)}
        events = []
        for carrier, (target, originals) in ROUTED.items():
            if carrier not in rows:
                notes.append(f"{carrier}: not in this ledger — skipped")
                continue
            folded, skipped = [], []
            for old in originals:
                row = rows.get(old)
                if row is None or row.get("superseded") or row.get("retracted") \
                        or row.get("target"):
                    skipped.append(old)
                    continue
                folded.append(old)
                events.append({"id": old, "event": "superseded", "ts": now_iso(),
                               "by": carrier, "to": target})
            notes.append(f"{carrier} → {target}: folded {folded or 'none'}"
                         + (f"; left alone {skipped}" if skipped else ""))
        if events:
            with path.open("a", encoding="utf-8") as fh:
                for event in events:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return notes


def run(routines_home: Path) -> int:
    """Daemon-boot entry point. Returns the number of carriers that folded at least one row."""
    notes = migrate(routines_home)
    changed = sum(1 for n in notes if "folded [" in n)
    if changed:
        log.warning("routed-reports migration: %s", "; ".join(notes))
    return changed
