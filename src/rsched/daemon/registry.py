"""Derived routine catalog + run index — the filesystem is the source of truth.

scan() reads ~/routines/*/routine.yaml fresh (never executes anything); the run index is
rebuilt from runs/ directories. No database, no cache files: a routine dropped into the
directory appears on the next rescan, one deleted disappears.
"""

from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

from ..config import RoutineConfig, ServerConfig, load_routine
from ..engine.inbox import open_questions
from ..paths import read_json

GZIP_AFTER_RUNS = 5  # transcripts older than the N most recent runs get gzipped


@dataclass
class RunInfo:
    """One run as read off disk (`runs/<ts>/status.json`) — the registry never asks the
    engine process; the filesystem is the interface."""

    run_id: str
    ts: str
    dir: Path
    state: str = "unknown"       # running | waiting_user | paused | finished | failed | aborted
    finish_status: str = ""      # ok | partial | failed | aborted ('' while running)
    summary: str = ""
    pid: int | None = None
    turn: int = 0
    usage: dict = field(default_factory=dict)
    question: dict | None = None
    updated: str = ""
    elapsed_s: int | None = None   # active wall-clock (final write = the run's duration)


@dataclass
class RoutineInfo:
    """A routine plus its recent runs and open questions — one catalog entry, rebuilt
    from the filesystem on every rescan (no cache, no database)."""

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
        return r if r and r.state in ("queued", "running", "waiting_user", "paused", "starting") else None


def read_run(run_dir: Path, slug: str) -> RunInfo:
    info = RunInfo(run_id=f"{slug}:{run_dir.name}", ts=run_dir.name, dir=run_dir)
    st = read_json(run_dir / "status.json")
    if isinstance(st, dict):
        info.state = st.get("state", "unknown")
        info.pid = st.get("pid")
        info.turn = int(st.get("turn") or 0)
        info.usage = st.get("usage") or {}
        info.question = st.get("question")
        info.updated = st.get("updated", "")
        if st.get("elapsed_s") is not None:
            info.elapsed_s = int(st["elapsed_s"])
        elif st.get("updated") and st.get("started"):
            # runs from before elapsed_s existed: best-effort from the two stamps
            try:
                started = datetime.strptime(str(st["started"]), "%Y%m%d-%H%M%S")
                updated = datetime.fromisoformat(str(st["updated"])).replace(tzinfo=None)
                info.elapsed_s = max(0, int((updated - started).total_seconds()))
            except ValueError:
                pass
    result = run_dir / "result.md"
    if result.exists():
        try:
            info.summary = result.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if info.state in ("finished", "failed", "aborted"):
        info.finish_status = {"finished": "ok", "failed": "failed", "aborted": "aborted"}[info.state]
        # a partial finish still lands state=finished; refine from the transcript tail lazily
        # only when a caller needs it — the summary is what the dashboard shows.
    return info


def run_index(routine_dir: Path, slug: str) -> list[RunInfo]:
    runs_dir = routine_dir / "runs"
    if not runs_dir.is_dir():
        return []
    dirs = sorted((d for d in runs_dir.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    return [read_run(d, slug) for d in dirs]


def scan(server: ServerConfig) -> dict[str, RoutineInfo]:
    home = server.routines_home
    catalog: dict[str, RoutineInfo] = {}
    if not home.is_dir():
        return catalog
    for d in sorted(home.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not (d / "routine.yaml").exists():
            continue
        cfg, problems = load_routine(d)
        if cfg is None:
            cfg = RoutineConfig(slug=d.name, dir=d, enabled=False)
            problems = [*problems, "unloadable routine.yaml — treated as disabled"]
        catalog[cfg.slug] = RoutineInfo(cfg=cfg, problems=problems,
                                        runs=run_index(d, cfg.slug),
                                        open_questions=open_questions(d))
    return catalog


def next_fire(cfg: RoutineConfig, after: datetime) -> datetime | None:
    if not cfg.cron or not cfg.enabled:
        return None
    tz = ZoneInfo(cfg.tz)
    return croniter(cfg.cron, after.astimezone(tz)).get_next(datetime)


def last_due_fire(cfg: RoutineConfig, before: datetime) -> datetime | None:
    if not cfg.cron:
        return None
    tz = ZoneInfo(cfg.tz)
    return croniter(cfg.cron, before.astimezone(tz)).get_prev(datetime)


def parse_run_ts(ts: str, tz: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y%m%d-%H%M%S").replace(tzinfo=ZoneInfo(tz))
    except ValueError:
        return None


def missed_fire(cfg: RoutineConfig, runs: list[RunInfo], now: datetime) -> datetime | None:
    """Boot-time catch-up: the most recent due fire that no run covered, honoring the
    routine's catchup policy. Returns the missed fire time or None."""
    if cfg.catchup != "run_once" or not cfg.cron or not cfg.enabled:
        return None
    due = last_due_fire(cfg, now)
    if due is None:
        return None
    last_start = parse_run_ts(runs[0].ts, cfg.tz) if runs else None
    if last_start is None or last_start < due - timedelta(seconds=59):
        return due
    return None


def apply_retention(routine_dir: Path, slug: str, keep_runs: int) -> None:
    """Delete run dirs beyond keep_runs (oldest first) and gzip transcripts older than
    the GZIP_AFTER_RUNS most recent. Never touches a run that looks alive."""
    runs = run_index(routine_dir, slug)  # newest first
    alive = {r.ts for r in runs
             if r.state in ("queued", "running", "waiting_user", "paused", "starting")}
    for r in runs[keep_runs:]:
        if r.ts in alive:
            continue
        shutil.rmtree(r.dir, ignore_errors=True)
    for r in runs[GZIP_AFTER_RUNS:keep_runs]:
        if r.ts in alive:
            continue
        plain = r.dir / "transcript.jsonl"
        if plain.exists():
            gz = plain.with_suffix(".jsonl.gz")
            try:
                with open(plain, "rb") as src, gzip.open(gz, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                plain.unlink()
            except OSError:
                gz.unlink(missing_ok=True)
        for sub in (r.dir / "sub").glob("*/transcript.jsonl"):
            gzs = sub.with_suffix(".jsonl.gz")
            try:
                with open(sub, "rb") as src, gzip.open(gzs, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                sub.unlink()
            except OSError:
                gzs.unlink(missing_ok=True)
