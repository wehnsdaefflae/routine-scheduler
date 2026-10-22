"""Run RETENTION: delete run dirs past `keep_runs`, gzip the transcripts of older ones.

Split out of `registry.py`, whose job is the DERIVED CATALOG — reading the filesystem and
never changing it. Retention is the one thing in that module that wrote: it deletes
directories and rewrites transcripts, and it was the only reason the catalog imported `gzip`
and `shutil`. One responsibility per file (CLAUDE.md Standards); the daemon's reaper is the
only caller (`daemon/runner_reap.py`).
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

from .registry import ACTIVE_STATES, run_index

GZIP_AFTER_RUNS = 5  # transcripts older than the N most recent runs get gzipped


def _gzip_in_place(plain: Path) -> None:
    """Compress <name> → <name>.gz and remove the plain file; best-effort (kept files are
    still read via read_events' _open_maybe_gz).
    """
    if not plain.exists():
        return
    gz = plain.with_suffix(plain.suffix + ".gz")
    try:
        with plain.open("rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        plain.unlink()
    except OSError:
        gz.unlink(missing_ok=True)


def apply_retention(routine_dir: Path, slug: str, keep_runs: int) -> None:
    """Delete run dirs beyond keep_runs (oldest first) and gzip the transcript + LLM-task
    sidecar of runs older than the GZIP_AFTER_RUNS most recent. Never touches a live run.
    """
    runs = run_index(routine_dir, slug)  # newest first
    alive = {r.ts for r in runs if r.state in ACTIVE_STATES}
    for r in runs[keep_runs:]:
        if r.ts in alive:
            continue
        shutil.rmtree(r.dir, ignore_errors=True)
    for r in runs[GZIP_AFTER_RUNS:keep_runs]:
        if r.ts in alive:
            continue
        _gzip_in_place(r.dir / "transcript.jsonl")
        _gzip_in_place(r.dir / "llm-tasks.jsonl")
        for sub in (r.dir / "sub").rglob("transcript.jsonl"):
            _gzip_in_place(sub)   # rglob: nested child trees (sub/N/sub/M/…) compress too
