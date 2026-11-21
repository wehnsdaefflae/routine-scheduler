"""What a SELECTION has actually held, and what that prices its next run at (D295, part a)."""

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
    """A stable name for WHAT THIS SESSION RUNS — the thing a hold belongs to."""
    body = "\0".join([*sorted(str(p) for p in (paths or [])), markexpr.strip(),
                      f"w{max(0, int(workers))}"])
    return blake2b(body.encode("utf-8", "replace"), digest_size=8).hexdigest()


def priced_for(key: str, fallback_mb: int, *, evidence_dir: Path | None = None,
               ) -> tuple[int, str]:
    """The charge for the selection `key` names, and WHICH figure it was — D295's part (a)."""
    return priced_need_mb(key, history=holds_by_key(evidence_dir or EVIDENCE_DIR),
                          fallback_mb=fallback_mb)
def history_path(evidence_dir: Path) -> Path:
    """The history file's path under a gate-evidence directory."""
    return Path(evidence_dir) / HISTORY_FILE


def record_hold(evidence_dir: Path, *, key: str, held_mb: int, selection: str,
                seconds: float) -> None:
    """Append ONE session's measured peak hold. Best-effort: a guard that can fail the session
    it is guarding is a defect dressed as evidence, so every write error is swallowed.
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
    """What to charge this selection, and WHICH figure that was — `(mb, source)`."""
    seen = int(history.get(str(key), 0))
    if seen <= 0:
        return int(fallback_mb), "the class constant (this selection has not run before)"
    priced = min(int(fallback_mb), -(-int(seen * float(margin)) // 64) * 64)
    return priced, f"this selection's own worst hold ({seen} MB) plus a margin"

