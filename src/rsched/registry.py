"""Derived routine catalog + run index — the filesystem is the source of truth.

scan() reads ~/routines/*/routine.yaml (never executes anything); the run index is rebuilt
from runs/ directories. No database, no cache files: a routine dropped into the directory
appears on the next rescan, one deleted disappears. Parsing is memoized per file behind a
stat() check (see the memo block below) — freshness is re-decided from the filesystem on
every lookup, so the memo can never serve state the disk no longer holds.
"""

from __future__ import annotations

import copy
import gzip
import shutil
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from croniter import croniter

from .config import RoutineConfig, ServerConfig, load_routine
from .engine.inbox import open_questions
from .paths import read_json

GZIP_AFTER_RUNS = 5  # transcripts older than the N most recent runs get gzipped

# THE run-state vocabulary (status.json `state`) — every consumer (SSE, wizard, detached
# manager, retention) imports these rather than inlining its own tuple.
TERMINAL_STATES = ("finished", "failed", "aborted")   # past these a run never changes again
ACTIVE_STATES = ("queued", "starting", "running", "waiting_user", "paused")


@dataclass
class RunInfo:
    """One run as read off disk (`runs/<ts>/status.json`) — the registry never asks the
    engine process; the filesystem is the interface.
    """

    run_id: str
    ts: str
    dir: Path
    state: str = "unknown"       # running | waiting_user | paused | finished | failed | aborted
    outcome: str | None = None   # finish outcome (ok|partial|failed|aborted) — partial is
                                 # invisible in `state` (folded into finished); None while live
    summary: str = ""
    pid: int | None = None
    turn: int = 0
    usage: dict = field(default_factory=dict)
    question: dict | None = None
    updated: str = ""
    model: str = ""              # the engine's resolved "<endpoint>/<model>" for this run
    elapsed_s: int | None = None   # active wall-clock (final write = the run's duration)


@dataclass
class RoutineInfo:
    """A routine plus its recent runs and open questions — one catalog entry, rebuilt
    from the filesystem on every rescan (no cache, no database).
    """

    cfg: RoutineConfig
    problems: list[str]
    runs: list[RunInfo]                  # newest first
    open_questions: list[dict]

    @property
    def slug(self) -> str:
        return self.cfg.slug

    @property
    def last_run(self) -> RunInfo | None:
        return self.runs[0] if self.runs else None

    @property
    def active_run(self) -> RunInfo | None:
        r = self.last_run
        return r if r and r.state in ACTIVE_STATES else None


# Stat-validated memos: scan() runs on every web request and scheduler tick, yet most of what
# it parses (terminal runs, an unchanged routine.yaml) never changes between calls. Each entry
# is keyed on the (inode, mtime_ns, size) of the files behind it and reused only while that
# fingerprint still matches — a stat() per lookup, so the filesystem stays the source of truth
# and there is no invalidation protocol to get wrong. atomic_write renames a fresh tmp file
# into place (new inode), so every cross-process rewrite is caught even inside one mtime tick.
# Callers get COPIES, never the cached objects. scan() prunes entries for dirs gone from disk.
_run_memo: dict[str, tuple[tuple, RunInfo]] = {}
_cfg_memo: dict[str, tuple[tuple, tuple[RoutineConfig | None, list[str]]]] = {}
_questions_memo: dict[str, tuple[tuple, list[dict]]] = {}


def _fingerprint(*paths: Path) -> tuple:
    out: list[tuple[int, int, int] | None] = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _prune(memo: dict, home: Path, visited: set[str]) -> None:
    prefix = f"{home}/"
    for key in [k for k in memo if k.startswith(prefix) and k not in visited]:
        del memo[key]


def read_run(run_dir: Path, slug: str) -> RunInfo:
    fp = _fingerprint(run_dir / "status.json", run_dir / "result.md")
    hit = _run_memo.get(str(run_dir))
    if hit is not None and hit[0] == fp:
        return _copy_run(hit[1])
    info = _read_run_fresh(run_dir, slug)
    _run_memo[str(run_dir)] = (fp, info)
    return _copy_run(info)


def _copy_run(info: RunInfo) -> RunInfo:
    return replace(info, usage=dict(info.usage), question=copy.deepcopy(info.question))


def _read_run_fresh(run_dir: Path, slug: str) -> RunInfo:
    info = RunInfo(run_id=f"{slug}:{run_dir.name}", ts=run_dir.name, dir=run_dir)
    st = read_json(run_dir / "status.json")
    if isinstance(st, dict):
        info.state = st.get("state", "unknown")
        info.outcome = st.get("outcome")
        info.pid = st.get("pid")
        info.model = str(st.get("model") or "")
        info.turn = int(st.get("turn") or 0)
        info.usage = st.get("usage") or {}
        info.question = st.get("question")
        info.updated = st.get("updated", "")
        if st.get("elapsed_s") is not None:
            info.elapsed_s = int(st["elapsed_s"])
        elif st.get("updated") and st.get("started"):
            # MIGRATION(expires=2026-10-01): runs from before elapsed_s existed —
            # best-effort from the two stamps; delete once retention has cycled them out
            try:
                started = parse_run_ts(str(st["started"]))
                updated = datetime.fromisoformat(str(st["updated"]))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                if started is not None:
                    info.elapsed_s = max(0, int((updated - started).total_seconds()))
            except ValueError:
                pass
    result = run_dir / "result.md"
    if result.exists():
        try:
            info.summary = result.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return info


def run_index(routine_dir: Path, slug: str) -> list[RunInfo]:
    runs_dir = routine_dir / "runs"
    if not runs_dir.is_dir():
        return []
    dirs = sorted((d for d in runs_dir.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    return [read_run(d, slug) for d in dirs]


def scan(server: ServerConfig, home: Path | None = None) -> dict[str, RoutineInfo]:
    """Catalog one home. Default: routines_home; pass server.conversations_home to catalog
    conversations — same dir shape, same RoutineInfo, different world (no schedule).
    """
    home = home or server.routines_home
    catalog: dict[str, RoutineInfo] = {}
    if not home.is_dir():
        return catalog
    visited: set[str] = set()
    for d in sorted(home.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not (d / "routine.yaml").exists():
            continue
        visited.add(str(d))
        cfg, problems = _load_routine_memo(d)
        if cfg is None:
            cfg = RoutineConfig(slug=d.name, dir=d, enabled=False)
            problems = [*problems, "unloadable routine.yaml — treated as disabled"]
        catalog[cfg.slug] = RoutineInfo(cfg=cfg, problems=problems,
                                        runs=run_index(d, cfg.slug),
                                        open_questions=_open_questions_memo(d))
    _prune(_cfg_memo, home, visited)
    _prune(_questions_memo, home, visited)
    _prune(_run_memo, home,
           {str(r.dir) for info in catalog.values() for r in info.runs})
    return catalog


def _load_routine_memo(d: Path) -> tuple[RoutineConfig | None, list[str]]:
    # both config AND tuning feed the parsed RoutineConfig — a tuning-only edit (the
    # slider, or the improver re-levelling deliberation) must miss the memo too
    fp = _fingerprint(d / "routine.yaml", d / "tuning.yaml")
    hit = _cfg_memo.get(str(d))
    if hit is None or hit[0] != fp:
        hit = (fp, load_routine(d))
        _cfg_memo[str(d)] = hit
    cfg, problems = hit[1]
    return (cfg.model_copy(deep=True) if cfg is not None else None), list(problems)


def _open_questions_memo(d: Path) -> list[dict]:
    # keyed on BOTH dirs: a new/rewritten question touches questions/pending, an answer
    # (which flips a question's `answered` flag) lands in inbox/ — either changes the result.
    fp = _fingerprint(d / "questions" / "pending", d / "inbox")
    hit = _questions_memo.get(str(d))
    if hit is None or hit[0] != fp:
        hit = (fp, open_questions(d))
        _questions_memo[str(d)] = hit
    return copy.deepcopy(hit[1])


def all_homes(server: ServerConfig) -> tuple[Path, Path, Path]:
    """The three run homes, in resolution order — every cross-home probe iterates THIS
    tuple (drifted inline copies once disagreed on which homes exist).
    """
    return (server.routines_home, server.conversations_home, server.background_home)


class Schedulable(Protocol):
    """What next_fire needs — RoutineConfig and LibrarySyncConfig both satisfy it, so
    the library-sync job rides the same cron math without duck-typed type:ignores.
    """

    cron: str
    tz: str
    enabled: bool


def next_fire(cfg: Schedulable, after: datetime) -> datetime | None:
    if not cfg.cron or not cfg.enabled:
        return None
    tz = ZoneInfo(cfg.tz)
    return croniter(cfg.cron, after.astimezone(tz)).get_next(datetime)


def last_due_fire(cfg: RoutineConfig, before: datetime) -> datetime | None:
    if not cfg.cron:
        return None
    tz = ZoneInfo(cfg.tz)
    return croniter(cfg.cron, before.astimezone(tz)).get_prev(datetime)


def parse_run_ts(ts: str) -> datetime | None:
    """Parse a run-ts back to an aware datetime. Run-ts is ALWAYS UTC (see ids.run_ts), so it
    is read as UTC regardless of the routine's display tz — catch-up comparisons are by
    absolute instant. (Reading it in the routine's tz used to skew last_start by the
    server↔routine offset, which could spuriously re-fire a run_once routine on a UTC host.)
    """
    try:
        return datetime.strptime(ts, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def missed_fire(cfg: RoutineConfig, runs: list[RunInfo], now: datetime) -> datetime | None:
    """Boot-time catch-up: the most recent due fire that no run covered, honoring the
    routine's catchup policy. Returns the missed fire time or None.
    """
    if cfg.catchup != "run_once" or not cfg.cron or not cfg.enabled:
        return None
    due = last_due_fire(cfg, now)
    if due is None:
        return None
    last_start = parse_run_ts(runs[0].ts) if runs else None
    if last_start is None or last_start < due - timedelta(seconds=59):
        return due
    return None


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
