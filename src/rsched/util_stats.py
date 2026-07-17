"""Per-util execution statistics for the Stats tab — the answers to "exists since when,
last revised when, how often executed / successful / mis-called / permission-blocked,
first and last executed when" for every global util.

Three sources, no database (stat-fingerprint memos in the registry.py idiom):

- **Library git history** (one `git log --name-only -- utils` walk, memoized on the
  library's HEAD): created = the oldest commit touching `utils/<name>/`, last revised =
  the newest.
- **The workflow-usage stream** (durable — survives run retention): records carry a
  per-run `utils` outcome breakdown from RunContext.util_stats. A record that HAS the
  `utils` key marks its run as counted at the source; such runs are never re-derived
  from transcripts.
- **Retained transcripts** (backfill for pre-stream history): runs the stream has not
  counted are scanned for util observations — root and sub transcripts, gzip included,
  memoized per file behind an (inode, mtime, size) fingerprint, so each terminal
  transcript is parsed once per process. Backfill sees executions only; rejected/denied
  calls never became observations pre-stream, so those counts honestly start at the
  stream's adoption.

Outcome vocabulary (RunContext.count_util): ok / error (non-zero exit) / usage_error
(exit 2 — argparse's bad-arguments convention, the "called with wrong syntax" signal) /
missing (no such util) / denied (permission refusal) / rejected (malformed action).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from . import utils_lib
from .config import ServerConfig
from .engine.executor import USAGE_ERROR_EXIT
from .engine.transcript import read_events
from .health_events import WORKFLOW_USAGE_FILE
from .ids import now_iso
from .workflows.library import head_commit

log = logging.getLogger("rsched.util_stats")

OUTCOMES = ("ok", "error", "usage_error", "missing", "denied", "rejected")
_EXECUTED = ("ok", "error", "usage_error")   # outcomes where the util actually ran
_PSEUDO = ("list", "show")                   # catalog discovery, not execution

# Stat-validated memos (see daemon/registry.py): re-decided from the filesystem on every
# lookup, so the disk stays the source of truth. Terminal transcripts never change, so
# each is parsed exactly once per process; the git walk re-runs only when HEAD moves.
_transcript_memo: dict[str, tuple[tuple, dict]] = {}
_git_dates_memo: dict[str, tuple[str, dict]] = {}


def _fingerprint(*paths: Path) -> tuple:
    out: list[tuple[int, int, int] | None] = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _git_dates(home: Path) -> dict[str, dict]:
    """Map util name → {created, revised} (ISO committer dates) for every util that ever
    lived in the library repo, from ONE `git log` walk over utils/. Empty when the
    library has no git history.
    """
    head = head_commit(home)
    if not head:
        return {}
    hit = _git_dates_memo.get(str(home))
    if hit is not None and hit[0] == head:
        return {k: dict(v) for k, v in hit[1].items()}
    try:
        r = subprocess.run(["git", "-C", str(home), "log", "--format=%x01%cI",
                            "--name-only", "--", "utils"],
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    dates: dict[str, dict] = {}
    current = ""
    for line in r.stdout.splitlines():
        if line.startswith("\x01"):
            current = line[1:].strip()
            continue
        parts = line.strip().split("/")
        if len(parts) >= 2 and parts[0] == "utils" and current:  # utils/<name>/…
            name = parts[1]
            # newest commit comes first: the first sighting is the last revision,
            # every later sighting pushes `created` further into the past
            dates.setdefault(name, {"revised": current})["created"] = current
    _git_dates_memo[str(home)] = (head, dates)
    return {k: dict(v) for k, v in dates.items()}


def _merge(dst: dict, name: str, counts: dict, first: str = "", last: str = "") -> None:
    cell = dst.setdefault(name, {"counts": dict.fromkeys(OUTCOMES, 0),
                                 "first": "", "last": ""})
    for k, v in counts.items():
        if k in cell["counts"]:
            cell["counts"][k] += int(v or 0)
    if first and (not cell["first"] or first < cell["first"]):
        cell["first"] = first
    if last and last > cell["last"]:
        cell["last"] = last


def _stream_utils(server: ServerConfig) -> tuple[dict, set[str], int]:
    """(per-util aggregate, counted root run ids, counted record count) from the durable
    stream. A record carrying the `utils` key was counted at the source — its root run
    id (`slug:ts`) marks the whole run dir (subruns included: their records carry the
    key from the same engine version) as covered for the transcript backfill.
    """
    path = server.routines_home / ".control" / WORKFLOW_USAGE_FILE
    agg: dict[str, dict] = {}
    covered: set[str] = set()
    counted = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return agg, covered, counted
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or "utils" not in rec:
            continue
        covered.add(str(rec.get("run_id") or "").split("#")[0])
        counted += 1
        ts = str(rec.get("ts") or "")
        for name, counts in (rec.get("utils") or {}).items():
            if not isinstance(counts, dict) or name in _PSEUDO:
                continue
            ran = any(counts.get(k) for k in _EXECUTED)
            _merge(agg, str(name), counts, first=ts if ran else "", last=ts if ran else "")
    return agg, covered, counted


def _scan_transcript(path: Path) -> dict:
    """Per-util execution counts + first/last event ts from ONE transcript file
    (plain or .gz), memoized behind its stat fingerprint.
    """
    gz = path.with_suffix(path.suffix + ".gz")
    fp = _fingerprint(path, gz)
    hit = _transcript_memo.get(str(path))
    if hit is not None and hit[0] == fp:
        return hit[1]
    agg: dict[str, dict] = {}
    for ev in read_events(path)[0]:
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if (ev.get("type") != "observation" or not isinstance(payload, dict)
                or payload.get("kind") != "util"):
            continue
        name = str(payload.get("name") or "")
        if not name or name in _PSEUDO:
            continue
        if payload.get("missing"):
            outcome = "missing"
        elif "exit" in payload:
            code = payload.get("exit")
            outcome = ("ok" if code == 0
                       else "usage_error" if code == USAGE_ERROR_EXIT else "error")
        else:
            continue
        ts = str(ev.get("ts") or "")
        ran = outcome in _EXECUTED
        _merge(agg, name, {outcome: 1}, first=ts if ran else "", last=ts if ran else "")
    _transcript_memo[str(path)] = (fp, agg)
    return agg


def _backfill(server: ServerConfig, covered: set[str]) -> tuple[dict, int]:
    """Scan retained transcripts of runs the stream has NOT counted (pre-stream
    history) — both homes, root + sub transcripts. Returns (per-util aggregate,
    scanned run count).
    """
    agg: dict[str, dict] = {}
    scanned = 0
    for home in (server.routines_home, server.conversations_home):
        if not home.is_dir():
            continue
        for rdir in sorted(home.iterdir()):
            if not rdir.is_dir() or rdir.name.startswith(".") \
                    or not (rdir / "routine.yaml").exists():
                continue
            runs = rdir / "runs"
            if not runs.is_dir():
                continue
            for run_dir in sorted(d for d in runs.iterdir() if d.is_dir()):
                if f"{rdir.name}:{run_dir.name}" in covered:
                    continue
                scanned += 1
                transcripts = [run_dir / "transcript.jsonl",
                               *sorted((run_dir / "sub").glob("*/transcript.jsonl")),
                               *sorted((run_dir / "sub").glob("*/transcript.jsonl.gz"))]
                for t in transcripts:
                    if t.suffix == ".gz" and t.with_suffix("").exists():
                        continue   # the plain file is scanned; don't double-read
                    try:
                        cells = _scan_transcript(t)
                    except Exception:  # ONE corrupt transcript must not raise out of
                        # util_stats() and zero the whole snapshot — skip it, keep the rest
                        log.warning("util_stats: skipping unreadable transcript %s", t,
                                    exc_info=True)
                        continue
                    for name, cell in cells.items():
                        _merge(agg, name, cell["counts"], cell["first"], cell["last"])
    return agg, scanned


def util_stats(server: ServerConfig) -> dict:
    """The Stats tab's per-util table: one row per library util (plus rows for counted
    names no longer in the library), honest about unknowns — a util with no git history
    has no created/revised date, one never seen executing has zero counts and no
    first/last timestamps.
    """
    home = server.utils_home
    catalog = {u["name"]: u for u in utils_lib.list_utils(home)}
    dates = _git_dates(home)
    stream_agg, covered, stream_runs = _stream_utils(server)
    backfill_agg, backfill_runs = _backfill(server, covered)

    merged: dict[str, dict] = {}
    for src in (stream_agg, backfill_agg):
        for name, cell in src.items():
            _merge(merged, name, cell["counts"], cell["first"], cell["last"])

    rows = []
    for name in sorted(set(catalog) | set(merged)):
        cell = merged.get(name) or {"counts": dict.fromkeys(OUTCOMES, 0),
                                    "first": "", "last": ""}
        counts = cell["counts"]
        executed = sum(counts[k] for k in _EXECUTED)
        d = dates.get(name) or {}
        rows.append({
            "name": name,
            "in_library": name in catalog,
            "summary": (catalog.get(name) or {}).get("summary", ""),
            "tags": (catalog.get(name) or {}).get("tags", []),
            "created": d.get("created"),          # None = predates git / never committed
            "revised": d.get("revised"),
            "executed": executed,
            **counts,
            "first_executed": cell["first"] or None,
            "last_executed": cell["last"] or None,
        })
    rows.sort(key=lambda r: (-r["executed"], r["name"]))
    return {"utils": rows, "stream_records": stream_runs, "backfill_runs": backfill_runs}


def snapshot_path() -> Path:
    """Canonical on-disk location of the util-stats snapshot — the SINGLE persisted copy of
    the `util_stats` computation that both the Stats tab and any routine (through the
    `util-stats` global util) read, so the two never diverge.

    Lives under XDG_STATE_HOME (default ~/.local/state) on purpose: a Landlock-jailed util
    subprocess can read ~/.local/state (sandbox._HOME_RW) but NOT the routines_home/.control
    daemon-state area, so a routine's `util-stats` call could never reach a snapshot kept
    under .control/. This same resolution is mirrored by the `util-stats` util.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "routine-scheduler" / "util-stats.json"


def write_util_stats_snapshot(server: ServerConfig) -> dict:
    """Compute `util_stats(server)` once and persist it (atomic replace) to
    `snapshot_path()`, stamped with a `generated` ISO timestamp. Returns the snapshot dict.

    Best-effort on the WRITE — an I/O error is swallowed so the run-finish hook that calls
    this can never break a run — but the computed data is always returned to the caller.
    """
    data = {"generated": now_iso(), **util_stats(server)}
    path = snapshot_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return data
