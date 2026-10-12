"""JOBS — the standing units of work a routine with the `jobs` setting keeps, and the one store
that holds them (`state/jobs.json`, engine-owned).

A routine whose job is several pieces of ongoing work — five projects stewarded side by side,
say — used to be five routines: five configs to keep in step, five pages, five lane members, and
nothing that saw the whole. With `capabilities.jobs: on` it is ONE routine whose regular duties
are its JOBS: the run creates, revises, opens, checkpoints and deletes them with the `job`
action as work comes up, and the engine enforces that every job due this run is PROCESSED —
opened, worked, and closed with a checkpoint — before the run may finish (docs/jobs.md).

A job is a record, not a process:

    {id, title, brief, state: active|paused|done, workspace, quiet_days, wake, carry, origin,
     created, updated, by, last: {run, at, outcome, summary, accounting}, history: [...]}

`origin` is the routine this job WAS, when a routine was merged into this one as a job (docs/
jobs.md, "Merging routines"). The run never writes it — the operator records it (the routine
page's job PATCH) — and it is what lets a report or a shared-store note addressed to that
switched-off routine be refused WITH the way here: `recipients.suggest` names this routine and
this job first.

Its WORKSPACE (default `jobs/<id>/`) is a directory inside the routine. While a job is OPEN the
run works in it: relative paths, scripts, shell and util calls and the memory notebook resolve
there (`RunContext.work_dir`). That is what lets a routine's whole working tree — its recipe
(`main.md` + `stages/`), its state files, its scripts, its notes — become a job unchanged.

WHICH jobs are due this run is decided ONCE, at a fresh boot, from three sources, and FAIL-OPEN
like every gate read (a wrong "due" costs a few turns, a wrong "not due" silently loses work):

1. the run gate's own answers (`runs/<ts>/gate.json`): a check carrying `job: <id>` that found
   work makes that job due; a check carrying no job that found work makes every job due; a
   fire whose checks were never asked (a manual run, a resume, no gate) makes every job due;
2. the job's own CLOCK (`clock_reasons`): work an earlier run left unprocessed (`carry`), a
   wake date that has come, a quiet limit passed, or no gate check watching it at all;
3. the run itself, which may open any active job it finds work for.

This module is the STORE and the due computation — read by the engine (engine/jobops.py), the
daemon's admission gate (a job due by its clock admits a fire) and the web layer (the routine
page lists the jobs). It imports nothing from the engine.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from . import jobs_compat as compat
from .ids import is_slug, now_iso
from .paths import atomic_write_json, file_lock, read_json, repo_lock_path

JOBS_FILE = "state/jobs.json"
#: Where a job's workspace lives unless it names another one.
JOBS_DIR = "jobs"
STATES = ("active", "paused", "done")
#: What a checkpoint says became of the job THIS run. `deferred` is the one that leaves it
#: owed: work exists and the run set it aside, so the next run carries it.
OUTCOMES = ("advanced", "no-work", "blocked", "deferred")
#: The capability setting's two values (grants.SETTING_DEFAULTS) — the user's words, on or off.
LEVELS = ("off", "on")
HISTORY_KEEP = 12
DELETED_KEEP = 20
SUMMARY_MAX = 4000
TITLE_MAX = 160
BRIEF_MAX = 6000


def store_path(routine_dir: Path) -> Path:
    """Where the store is WRITTEN — always the canonical name."""
    return Path(routine_dir) / JOBS_FILE


def read_path(routine_dir: Path) -> Path:
    """Where the store is READ — the canonical file, or the pre-D174 one (jobs_compat)."""
    return compat.read_path(routine_dir, store_path(routine_dir))


def _lock_path(routine_dir: Path) -> Path:
    # beside the repo's commit lock, inside the git dir — never a file in the tree an
    # autocommit would sweep up (paths.repo_lock_path explains the placement)
    return repo_lock_path(Path(routine_dir)).with_name("rsched-jobs.lock")


def empty() -> dict:
    return {"version": 1, "jobs": [], "deleted": [], "run": {}}


def load(routine_dir: Path) -> dict:
    """The store, normalized. Never raises: an absent or unreadable file is an empty store —
    and a store that does not parse is reported by `problems`, never silently rewritten (a save
    only ever follows a load that understood it).
    """
    raw = read_json(read_path(routine_dir))
    doc = empty()
    if not isinstance(raw, dict):
        return doc
    doc["jobs"] = [t for t in compat.records(raw)
                   if isinstance(t, dict) and t.get("id")]
    doc["deleted"] = [t for t in raw.get("deleted") or [] if isinstance(t, dict)]
    doc["run"] = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    return doc


def problems(routine_dir: Path) -> list[str]:
    """What is wrong with the store on disk, as sentences — empty when it is sound or absent."""
    path = read_path(routine_dir)
    if not path.exists():
        return []
    raw = read_json(path)
    name = path.name
    if not isinstance(raw, dict):
        return [f"{name} does not parse as a JSON object"]
    out = []
    seen: set[str] = set()
    for i, t in enumerate(compat.records(raw)):
        if not isinstance(t, dict) or not is_slug(str(t.get("id") or "")):
            out.append(f"{name}: job #{i + 1} has no kebab-case id")
            continue
        if t["id"] in seen:
            out.append(f"{name}: duplicate job id {t['id']!r}")
        seen.add(t["id"])
        if t.get("state", "active") not in STATES:
            out.append(f"{name}: job {t['id']!r} has state {t.get('state')!r}")
        if "origin" in t and not is_slug(str(t.get("origin") or "")):
            out.append(f"{name}: job {t['id']!r} has origin {t.get('origin')!r}, "
                       "which is not a routine slug")
    return out


def save(routine_dir: Path, doc: dict) -> None:
    atomic_write_json(store_path(routine_dir), doc)


@contextlib.contextmanager
def editing(routine_dir: Path) -> Iterator[dict]:
    """Load, hand the caller the store to change, save — under the store's lock, so the run
    and the routine page (an operator pausing a job) never write over each other.
    """
    with file_lock(_lock_path(routine_dir), timeout=10.0):
        doc = load(routine_dir)
        yield doc
        save(routine_dir, doc)


def find(doc: dict, tid: str) -> dict | None:
    return next((t for t in doc.get("jobs") or [] if t.get("id") == tid), None)


def workspace_rel(job: dict) -> str:
    return str(job.get("workspace") or f"{JOBS_DIR}/{job['id']}").strip("/")


def workspace(routine_dir: Path, job: dict) -> Path:
    return Path(routine_dir) / workspace_rel(job)


def workspace_problem(rel: str) -> str:
    """Why `rel` cannot be a workspace, or "". It must stay inside the routine and may not be
    one of the routine's own engine- or operator-owned places.
    """
    parts = Path(rel.strip("/")).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        return "a workspace is a directory INSIDE the routine, given relative to it"
    if parts[0] in ("runs", "state", "stages", ".memory", ".git", "inbox", "questions",
                    ".util_outputs", ".doc_cache", "scripts"):
        return f"{parts[0]}/ belongs to the routine itself — use {JOBS_DIR}/<id>/"
    return ""


def new_job(tid: str, title: str, brief: str, *, by: str, workspace: str = "",
             quiet_days: int | None = None, wake: str = "", state: str = "active") -> dict:
    job: dict = {"id": tid, "title": title[:TITLE_MAX], "brief": brief[:BRIEF_MAX],
                  "state": state, "created": now_iso(), "updated": now_iso(), "by": by}
    if workspace:
        job["workspace"] = workspace.strip("/")
    if quiet_days:
        job["quiet_days"] = int(quiet_days)
    if wake:
        job["wake"] = wake
    return job


def parse_day(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


# ── due ───────────────────────────────────────────────────────────────────────────────────

def checks_by_job(checks: list[dict]) -> dict[str, list[str]]:
    """Job id → the ids of the run-gate checks that watch it (`job:` on the check)."""
    out: dict[str, list[str]] = {}
    for i, c in enumerate(checks or []):
        if jid := compat.check_job(c):
            out.setdefault(jid, []).append(str(c.get("id") or f"c{i + 1}"))
    return out


def clock_reasons(job: dict, *, today: date, now: datetime, watched: bool) -> list[str]:
    """Why `job` is due by its OWN clock — nothing a gate check measured. Empty = not due by
    clock. `watched` says whether any run-gate check carries this job.
    """
    if job.get("state", "active") != "active":
        return []
    out = []
    if carry := str(job.get("carry") or ""):
        out.append(f"carried over: {carry}")
    wake = parse_day(job.get("wake"))
    if wake is not None and wake <= today:
        out.append(f"its wake date {wake.isoformat()} has come")
    last = (job.get("last") or {}).get("at")
    if quiet := int(job.get("quiet_days") or 0):
        try:     # both sides are aware (ids.now_iso); a stamp that is not reads as never
            idle: int | None = (now - datetime.fromisoformat(str(last))).days
        except (TypeError, ValueError):
            idle = None
        if idle is None:
            out.append("it has never been processed")
        elif idle >= quiet:
            out.append(f"not processed for {idle} day(s) — its quiet limit is {quiet}")
    if not watched and (wake is None or wake <= today) and not out:
        out.append("no run-gate check watches it, so every run is its run (set a wake date "
                   "or quiet days, or give it a gate check, to let it rest)")
    return out


def clock_due(routine_dir: Path, checks: list[dict], *, today: date,
              now: datetime) -> list[tuple[str, str]]:
    """`[(job id, first reason)]` for every active job due by its own clock — what the
    admission gate reads, so a job that is owed work admits a fire its checks would skip.
    """
    watched = checks_by_job(checks)
    return [(job["id"], reasons[0]) for job in load(routine_dir)["jobs"]
            if (reasons := clock_reasons(job, today=today, now=now,
                                         watched=job["id"] in watched))]


def due_for_run(doc: dict, *, gate: dict | None, checks: list[dict], today: date,
                now: datetime) -> tuple[list[dict], list[dict]]:
    """`(due, resting)` for a fresh run: each `{id, why: [...]}` in job order.

    Fail-open: when the gate did not ANSWER its checks for this fire (no gate, a bypass — a
    manual run, a resume, a trigger — or a built-in reason that admitted it before any check
    ran), every active job is due, because nothing measured which ones are quiet.
    """
    watched = checks_by_job(checks)
    owner = {cid: tid for tid, cids in watched.items() for cid in cids}
    answers = (gate or {}).get("checks") if isinstance(gate, dict) else None
    measured = bool(answers) and (gate or {}).get("decision") == "run"
    unwatched_work = []
    job_work: dict[str, list[str]] = {}
    for row in answers or []:
        if not isinstance(row, dict) or not row.get("work"):
            continue
        cid = str(row.get("id") or "")
        line = f"gate check {cid} ({row.get('kind')}): {row.get('reason') or 'work'}"
        if cid in owner:
            job_work.setdefault(owner[cid], []).append(line)
        elif cid != "script":
            unwatched_work.append(line)
    due: list[dict] = []
    resting: list[dict] = []
    for job in doc.get("jobs") or []:
        if job.get("state", "active") != "active":
            continue
        why = clock_reasons(job, today=today, now=now, watched=job["id"] in watched)
        if not measured:
            reason = (gate or {}).get("reason") if isinstance(gate, dict) else ""
            why.append("this fire's gate checks were not asked"
                       + (f" ({reason})" if reason else " (no gate, or a manual run)")
                       + " — so every active job is due")
        why += job_work.get(job["id"], []) + unwatched_work
        (due if why else resting).append(
            {"id": job["id"], "why": why or ["its gate checks found nothing new"]})
    return due, resting


# ── recording ─────────────────────────────────────────────────────────────────────────────

def record_checkpoint(job: dict, *, run_id: str, outcome: str, summary: str,
                      accounting: list[str] | None = None, wake: str | None = None,
                      today: date) -> None:
    """Fold one checkpoint into the job: the last outcome, the history, and what the next run
    owes it — `deferred` carries the job, every other outcome settles what was carried.
    """
    entry: dict = {"run": run_id, "at": now_iso(), "outcome": outcome,
                   "summary": summary[:SUMMARY_MAX]}
    if accounting:
        entry["accounting"] = [str(a)[:600] for a in accounting]
    job["last"] = entry
    job["history"] = [entry, *(job.get("history") or [])][:HISTORY_KEEP]
    if outcome == "deferred":
        job["carry"] = f"deferred by {run_id}: {summary[:300]}"
    else:
        job.pop("carry", None)
    if wake is not None:
        if wake:
            job["wake"] = wake
        else:
            job.pop("wake", None)
    elif (day := parse_day(job.get("wake"))) is not None and day <= today:
        job.pop("wake", None)       # a wake date that has come is spent by processing
    job["updated"] = now_iso()


def carry_unprocessed(routine_dir: Path, run_id: str, why: str) -> list[str]:
    """At a run's end: every job the run was owed and did not checkpoint is CARRIED, so the
    next run processes it whatever its checks say. Returns the ids carried.
    """
    carried: list[str] = []
    with editing(routine_dir) as doc:
        ledger = doc.get("run") or {}
        if ledger.get("run_id") != run_id:
            return carried
        done = set(ledger.get("done") or {})
        for item in ledger.get("due") or []:
            job = find(doc, str(item.get("id")))
            if job is None or job["id"] in done or job.get("state") != "active":
                continue
            job["carry"] = f"run {run_id} {why} before checkpointing it"
            carried.append(job["id"])
        ledger["open"] = ""
        ledger["ended"] = now_iso()
    return carried
