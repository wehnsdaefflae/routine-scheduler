"""Bug-reports file: append-only JSONL log of bug reports any routine's run may file via
the ungated `report_bug` action — the default-on "report potential bugs" channel.

Writes to <routines_home>/.control/bug-reports.jsonl. Each line is a JSON object:
{"ts": <iso>, "routine": <slug>, "run_id": <id>, "title": <str>, "detail": <str>}

self-audit's gather-evidence reads this stream each run and turns unresolved entries into
findings. Best-effort append: I/O errors are swallowed so filing a report never blocks a
run — the caller learns success from the returned path (None on failure).
"""

from __future__ import annotations

import json
from pathlib import Path

from .ids import now_iso

BUG_REPORTS_FILE = "bug-reports.jsonl"


def file_bug_report(routines_home: Path, *, routine: str, run_id: str,
                    title: str, detail: str = "") -> Path | None:
    """Append one bug report to <routines_home>/.control/bug-reports.jsonl.

    Returns the file path on success, or None if the write failed (best-effort, like the
    health-events log — a failed report must never abort the reporting run).
    """
    path = Path(routines_home) / ".control" / BUG_REPORTS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso(),
                "routine": routine,
                "run_id": run_id,
                "title": title[:300],
                "detail": detail[:4000],
            }, ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None


def read_bug_reports(routines_home: Path) -> list[dict]:
    """All filed bug reports, oldest first — the read side self-audit's gather-evidence
    uses to turn entries into findings. Malformed lines are skipped; a missing file is an
    empty stream (not an error).
    """
    path = Path(routines_home) / ".control" / BUG_REPORTS_FILE
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
