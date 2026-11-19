"""What a SELECTION has actually held, and what that prices its next run at (D295, part a).

The operator, 2026-11-19: *"b then a — build the RSS watchdog, and price the admission estimate
from the selection's own history"*. This module is the history half; `memwatch` is the watchdog
that measures it and `memgate` is what charges against it.

## Why a history of our own

The gate already samples memory into `.gate-evidence/memory-peaks.jsonl`, and it cannot answer
this question: measured 2026-11-19, its 1,004 rows carry `stage` and NO selection identity at
all — which is precisely why its `tests` label pools 754 rows spanning 500-4,393 MB and why a
single constant priced off that label is ~2.4x the median and 0.64x the worst at the same time.
A hold belongs to a selection, so the row has to name one.

The key names everything that changes the hold and nothing that does not: the paths, the marker
expression and the worker count (the same paths at 4 xdist workers hold about 2.4 GB more than
at none), sorted so the order they were typed in cannot mint a second key for one selection.

## Best-effort, in both directions

Writing is swallowed on every error: a guard that can fail the session it is guarding is a
defect dressed as evidence. Reading skips a line it cannot parse, because this file is appended
to by sessions that the watchdog can KILL mid-write — a truncated last line is the expected
state, not a corruption to report.
"""

from __future__ import annotations

import json
import time
from hashlib import blake2b
from pathlib import Path

#: Where a selection's measured holds are kept — beside the gate's own evidence, since it is
#: evidence of the same kind. One JSON line per finished session.
HISTORY_FILE = "selection-holds.jsonl"

#: The directory that evidence lives in, derived like `test_policy`'s warn-window file: this
#: checkout's own `.audit-wt/.gate-evidence`, so a worktree records against the tree it tested.
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / ".audit-wt" / ".gate-evidence"


def selection_key(paths, markexpr: str, workers: int) -> str:
    """A stable name for WHAT THIS SESSION RUNS — the thing a hold belongs to.

    The selection, its marker expression and its worker count, because all three change the
    hold: the same paths at 4 xdist workers hold about 2.4 GB more than at none. Sorted, so the
    order the paths were typed in does not mint a second key for one selection, and digested
    rather than spelled out (blake2b: an identity, never a signature) because a key is
    compared and never read — a whole-suite call's paths would otherwise be a
    4,000-character field in every row.
    """
    body = "\0".join([*sorted(str(p) for p in (paths or [])), markexpr.strip(),
                      f"w{max(0, int(workers))}"])
    return blake2b(body.encode("utf-8", "replace"), digest_size=8).hexdigest()


def priced_for(key: str, fallback_mb: int, *, evidence_dir: Path | None = None,
               ) -> tuple[int, str]:
    """The charge for the selection `key` names, and WHICH figure it was — D295's part (a).

    The history read and the pricing in one call, because the two are never wanted apart: a
    caller that read the file and then priced from it would have to know this module's row
    shape. `fallback_mb` is the CLASS charge its caller computed (`memgate.need_mb`), passed in
    rather than imported — the class figures are the gate's subject and the history is this
    module's, and a cycle between the two is the only way they could disagree.
    """
    return priced_need_mb(key, history=holds_by_key(evidence_dir or EVIDENCE_DIR),
                          fallback_mb=fallback_mb)
def history_path(evidence_dir: Path) -> Path:
    """The history file's path under a gate-evidence directory."""
    return Path(evidence_dir) / HISTORY_FILE


def record_hold(evidence_dir: Path, *, key: str, held_mb: int, selection: str,
                seconds: float) -> None:
    """Append ONE session's measured peak hold. Best-effort: a guard that can fail the session
    it is guarding is a defect dressed as evidence, so every write error is swallowed.

    `selection` and `seconds` ride along for a human reading the file; only `key` and `held_mb`
    are read back by `priced_need_mb`.
    """
    try:
        path = history_path(evidence_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key,
                                     "selection": selection, "held_mb": int(held_mb),
                                     "seconds": round(float(seconds), 1)}) + "\n")
    except (OSError, TypeError, ValueError):
        return


def holds_by_key(evidence_dir: Path) -> dict[str, int]:
    """The WORST hold recorded per selection key. An unreadable or half-written line is
    skipped rather than raised on: this file is appended to by sessions that can be killed.
    """
    worst: dict[str, int] = {}
    try:
        text = history_path(evidence_dir).read_text(encoding="utf-8")
    except OSError:
        return worst
    for line in text.splitlines():
        try:
            row = json.loads(line)
            key, held = str(row["key"]), int(row["held_mb"])
        except (ValueError, TypeError, KeyError):
            continue
        if held > worst.get(key, 0):
            worst[key] = held
    return worst


def priced_need_mb(key: str, *, history: dict[str, int], fallback_mb: int,
                   margin: float = 1.25) -> tuple[int, str]:
    """What to charge this selection, and WHICH figure that was — `(mb, source)`.

    Its own worst measured hold plus a margin (25 %) when this exact selection has run before;
    the class constant otherwise, named as the fallback so a refusal says which figure refused
    it. The margin exists because a hold is not a constant either: the same selection grows with
    the tree, and pricing it at exactly its last peak would refuse it the first time a new test
    lands.

    Never MORE than the class constant: that figure is the measured worst case of a whole class
    and a per-selection price above it would re-introduce the over-charge this replaces.
    """
    seen = int(history.get(str(key), 0))
    if seen <= 0:
        return int(fallback_mb), "the class constant (this selection has not run before)"
    priced = min(int(fallback_mb), -(-int(seen * float(margin)) // 64) * 64)
    return priced, f"this selection's own worst hold ({seen} MB) plus a margin"

