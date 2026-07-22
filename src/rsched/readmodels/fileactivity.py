"""What a run read and wrote — a per-file read-model over its transcript.

Derived from OBSERVATION events (not actions), so user slash commands — which execute
without a model turn but land the same observation payloads — count too. `read_file` /
`view_image` observations carry the file(s) touched (a batched read one entry each);
`write_file` / `edit_file` carry the path written/edited. Children are included: a
subtask's writes are the run's writes (`sub/<n>/…` transcripts, recursively), flagged
`sub` so provenance stays visible. Rows come back in first-touched order — the run's
file story, not an alphabetical inventory.
"""

from __future__ import annotations

from pathlib import Path

from ..engine.transcript import read_events

_READ_KINDS = frozenset({"read_file", "view_image"})
_WRITE_OPS = {"write_file": "writes", "edit_file": "edits"}


def _touched(payload: dict) -> list[tuple[str, str, bool, int]]:
    """(path, op-counter, failed, bytes) per file this one observation touched."""
    kind = str(payload.get("kind") or "")
    if kind in _READ_KINDS:
        files = payload.get("files") or ([payload] if payload.get("path") else [])
        return [(str(f.get("path") or ""), "reads", bool(f.get("error")), 0)
                for f in files if isinstance(f, dict)]
    if op := _WRITE_OPS.get(kind):
        path = str(payload.get("path") or "")
        return [(path, op, bool(payload.get("error")),
                 int(payload.get("bytes") or 0))] if path else []
    return []


def file_activity(run_dir: Path) -> list[dict]:
    """[{path, reads, writes, edits, bytes, errors, sub}] in first-touched order.

    `bytes` totals successful write_file payloads; a failed touch counts only under
    `errors` (the op never happened). `sub` marks a path any CHILD run touched.
    Memoized on the run's transcript fingerprints (rail-polled endpoint).
    """
    from . import memo

    return memo.memoized(f"files:{run_dir}", memo.transcript_paths(run_dir),
                         lambda: _file_activity(run_dir))


def _file_activity(run_dir: Path) -> list[dict]:
    rows: dict[str, dict] = {}

    def walk(d: Path, *, sub: bool) -> None:
        events, _ = read_events(d / "transcript.jsonl")
        for ev in events:
            if ev.get("type") != "observation":
                continue
            payload = ev.get("payload")
            for path, op, failed, nbytes in _touched(payload if isinstance(payload, dict)
                                                     else {}):
                if not path:
                    continue
                row = rows.setdefault(path, {"path": path, "reads": 0, "writes": 0,
                                             "edits": 0, "bytes": 0, "errors": 0,
                                             "sub": False})
                if failed:
                    row["errors"] += 1
                else:
                    row[op] += 1
                    row["bytes"] += nbytes
                row["sub"] = row["sub"] or sub
        subdir = d / "sub"
        if subdir.is_dir():
            for child in sorted(p for p in subdir.iterdir() if p.name.isdigit()):
                walk(child, sub=True)

    walk(run_dir, sub=False)
    return list(rows.values())
