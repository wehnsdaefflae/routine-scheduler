"""What a run read and wrote — a per-file read-model over its transcript.

Derived from OBSERVATION events (not actions), so user slash commands — which execute
without a model turn but land the same observation payloads — count too. `read_file` /
`view_image` observations carry the file(s) touched (a batched read one entry each);
`write_file` / `edit_file` carry the path written/edited. Children are included: a
subtask's writes are the run's writes (`sub/<n>/…` transcripts, recursively), flagged
`sub` so provenance stays visible. Rows come back in first-touched order — the run's
file story, not an alphabetical inventory.

A row also carries the run-relative BASE directories its path was seen under (`""` for the
parent, `sub/1` for a child). A relative path only means something against the dir of the run
that touched it, and keying rows on the string alone threw that away: the file server then
guessed, resolving every row against the parent's dir, so a child's working-dir file
(`build/page-1.png` under `sub/1/`) was listed as an openable row and 404'd on click, while a
sibling that happened to exist under the parent opened fine — inconsistent, inside one
directory of one subrun (R1193).
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
    """[{path, reads, writes, edits, bytes, errors, sub, bases}] in first-touched order.

    `bytes` totals successful write_file payloads; a failed touch counts only under
    `errors` (the op never happened). `sub` marks a path any CHILD run touched, and
    `bases` names the run-relative directories it was touched under, in first-touched
    order — what a relative path resolves against.
    Memoized on the run's transcript fingerprints (rail-polled endpoint).
    """
    from . import memo

    return memo.memoized(f"files:{run_dir}", memo.transcript_paths(run_dir),
                         lambda: _file_activity(run_dir))


def _file_activity(run_dir: Path) -> list[dict]:
    rows: dict[str, dict] = {}

    def walk(d: Path, *, sub: bool) -> None:
        base = d.relative_to(run_dir).as_posix() if d != run_dir else ""
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
                                             "sub": False, "bases": []})
                if failed:
                    row["errors"] += 1
                else:
                    row[op] += 1
                    row["bytes"] += nbytes
                row["sub"] = row["sub"] or sub
                if base not in row["bases"]:
                    row["bases"].append(base)
        subdir = d / "sub"
        if subdir.is_dir():
            for child in sorted(p for p in subdir.iterdir() if p.name.isdigit()):
                walk(child, sub=True)

    walk(run_dir, sub=False)
    return list(rows.values())
