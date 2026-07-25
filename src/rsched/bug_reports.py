"""Bug-reports file: append-only JSONL log of bug reports any routine's run may file via
the ungated `report_bug` action — the default-on "report potential bugs" channel.

Writes to <routines_home>/.control/bug-reports.jsonl. Each line is a JSON object:
{"id": "R7", "ts": <iso>, "routine": <slug>, "run_id": <id>, "title": <str>, "detail": <str>}

The `id` is monotonic (`R1`, `R2`, …) and assigned here, under the same advisory lock the
append takes — two runs filing at once cannot land on the same number. `R` and not `B`:
the user's own reviewer-backlog items are written `B<n>` in prose, and the console's
reference links would mislink them. The id makes a bug report a first-class ITEM alongside
findings and decisions (docs/items.md); every row carries one.

self-audit's gather-evidence reads this stream each run and turns unresolved entries into
findings. Best-effort append: I/O errors are swallowed so filing a report never blocks a
run — the caller learns success from the return value (None on failure).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .ids import now_iso
from .paths import file_lock

BUG_REPORTS_FILE = "bug-reports.jsonl"
BUG_ID_RE = re.compile(r"^R(\d+)$")


def bug_reports_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / BUG_REPORTS_FILE


def next_id(path: Path) -> str:
    """The next free `R<n>` for this stream: one past the highest id already in it. Reading
    the whole (small, append-only) file keeps the counter IN the data — no sidecar to drift
    out of sync with a hand-edited or restored file.
    """
    highest = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "R1"
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = BUG_ID_RE.match(str(row.get("id") or "")) if isinstance(row, dict) else None
        if m:
            highest = max(highest, int(m.group(1)))
    return f"R{highest + 1}"


def file_bug_report(routines_home: Path, *, routine: str, run_id: str,
                    title: str, detail: str = "") -> tuple[Path, str] | None:
    """Append one bug report to <routines_home>/.control/bug-reports.jsonl.

    Returns `(path, id)` on success, or None if the write failed (best-effort, like the
    health-events log — a failed report must never abort the reporting run). The id
    assignment and the append happen under one lock, so concurrent runs get distinct ids.
    """
    path = bug_reports_path(routines_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            item_id = next_id(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "id": item_id,
                    "ts": now_iso(),
                    "routine": routine,
                    "run_id": run_id,
                    "title": title[:300],
                    "detail": detail[:4000],
                }, ensure_ascii=False) + "\n")
        return path, item_id
    except OSError:
        return None
