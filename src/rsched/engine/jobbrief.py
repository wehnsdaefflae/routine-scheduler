"""What a run READS about its jobs: the digest section, and the briefing a job opens with.

Split out of `engine/jobops.py` when D174 renamed the layer (the ≤350-line house bar — the
module held both the ACTION and everything the run is TOLD, and the two change for different
reasons: a verb's semantics versus the wording a run is briefed in).

Everything here is pure READING — it never edits the store. `jobops` owns the verbs, the
ledger and the gates, and calls in here for the prose; nothing in here calls back, so the
dependency runs one way (jobops → jobbrief).
"""

from __future__ import annotations

from pathlib import Path

from .. import jobs
from ..paths import read_json
from . import donewhen, notes
from .run_context import RunContext

RECIPE_MAX_CHARS = 24_000
LEDGER_TAIL = 20
MEMORY_INDEX_LINES = 40


def job_line(job: dict) -> str:
    last = job.get("last") or {}
    seen = (f"last processed {str(last.get('at'))[:10]} ({last.get('outcome')})"
            if last.get("at") else "never processed")
    extra = [f"wakes {job['wake']}"] if job.get("wake") else []
    extra += [f"quiet limit {job['quiet_days']}d"] if job.get("quiet_days") else []
    return (f"{job['id']} — {job.get('title') or job['id']} · {seen}"
            + (f" · {', '.join(extra)}" if extra else ""))


def digest_section(doc: dict) -> str:
    """The JOBS section of the boot digest: what this run owes, in order, and why."""
    ledger = doc.get("run") or {}
    by_id = {t["id"]: t for t in doc.get("jobs") or []}
    out = [("JOBS — this routine's standing work (the `job` action manages them). GATED "
            "PROCESSING: every job DUE this run must be opened and checkpointed before the "
            "engine lets you finish — one at a time, in this order unless the work says "
            "otherwise.")]
    due = [d for d in ledger.get("due") or [] if d.get("id") in by_id]
    if due:
        out.append("Due this run:")
        for n, item in enumerate(due, 1):
            out.append(f"  {n}. {job_line(by_id[item['id']])}")
            out.append(f"     due because: {'; '.join(item.get('why') or [])}")
    else:
        out.append("Due this run: none — nothing in the job list is owed. Do the routine's "
                   "own work, and open a job only where you find work for it.")
    resting = [r for r in ledger.get("resting") or [] if r.get("id") in by_id]
    if resting:
        out.append("Resting this run (not due — open one only if you find work for it):")
        out += [f"  - {job_line(by_id[r['id']])}" for r in resting]
    other = [t for t in doc.get("jobs") or [] if t.get("state", "active") != "active"]
    if other:
        out.append("Paused / done: " + "; ".join(f"{t['id']} ({t.get('state')})"
                                                  for t in other))
    if ledger.get("open"):
        out.append(f"OPEN NOW (resumed): {ledger['open']} — its workspace is your working "
                   "directory until you checkpoint it.")
    return "\n".join(out)


# ── the briefing a job opens with ────────────────────────────────────────────────────────

def recipe_text(ws: Path) -> str:
    try:
        text = (ws / "main.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > RECIPE_MAX_CHARS:
        text = (text[:RECIPE_MAX_CHARS]
                + f"\n[… recipe truncated at {RECIPE_MAX_CHARS} chars — read_file main.md]")
    return text


def job_digest(ws: Path) -> str:
    """The job's own state, the way a routine's boot digest shows a routine's."""
    from .composer import _dir_listing, _plan_text

    parts = []
    phase = read_json(ws / "state" / "phase.json")
    if phase:
        parts.append(f"Current phase (state/phase.json): {phase}")
    if plan := _plan_text(ws):
        parts.append("WORKING PLAN (state/plan.md):\n" + plan)
    if (ws / "state").is_dir():
        parts.append("state/ (newest first): " + (_dir_listing(ws / "state") or "(empty)"))
    if noted := notes.tail(ws):
        parts.append("Recent notes (state/notes.md tail):\n" + noted)
    stages = ws / "stages"
    if stages.is_dir() and (names := sorted(p.name for p in stages.glob("*.md"))):
        parts.append("stages/ (read the one for where you are, on demand): " + ", ".join(names))
    if (ws / "artifacts").is_dir() and (arts := _dir_listing(ws / "artifacts")):
        parts.append("artifacts/ (newest first): " + arts)
    ledger_md = ws / "LEDGER.md"
    if ledger_md.is_file():
        lines = ledger_md.read_text(encoding="utf-8", errors="replace").splitlines()
        parts.append(f"LEDGER tail (of {len(lines)} lines):\n" + "\n".join(lines[-LEDGER_TAIL:]))
    index = ws / ".memory" / "INDEX.md"
    if index.is_file():
        lines = index.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        more = (f"\n[… {len(lines) - MEMORY_INDEX_LINES} more — memory_read a topic]"
                if len(lines) > MEMORY_INDEX_LINES else "")
        parts.append(".memory/ index (THIS job's notebook — memory_read before "
                     "re-discovering anything):\n" + "\n".join(lines[:MEMORY_INDEX_LINES]) + more)
    return "\n\n".join(parts)


def open_briefing(ctx: RunContext, job: dict, ledger: dict) -> str:
    ws = jobs.workspace(ctx.routine.dir, job)
    rel = jobs.workspace_rel(job)
    due = [d.get("id") for d in ledger.get("due") or []]
    why = next((d.get("why") for d in ledger.get("due") or [] if d.get("id") == job["id"]),
               None) or ["opened by the run"]
    place = (f"due job {due.index(job['id']) + 1} of {len(due)}" if job["id"] in due
             else "not due this run — opened by you")
    out = [f"JOB OPEN — {job['id']}: {job.get('title') or job['id']} ({place})",
           f"Due because: {'; '.join(why)}",
           f"Brief: {job.get('brief') or '(none — set one with job verb=update)'}",
           (f"Workspace: {rel}/ ({ws}) — your WORKING DIRECTORY until you checkpoint this "
            "job: relative paths, scripts, shell and util calls and memory_read / "
            "memory_write resolve here. The routine's own files stay reachable by their "
            "absolute path.")]
    if job.get("carry"):
        out.append(f"Carried over: {job['carry']}")
    last = job.get("last") or {}
    if last:
        out.append(f"Last checkpoint ({str(last.get('at'))[:16]}, {last.get('outcome')}, run "
                   f"{last.get('run')}):\n{last.get('summary')}")
    if recipe := recipe_text(ws):
        # a job's recipe is often a former routine's, written for a run of its own
        out.append("ITS RECIPE (main.md in the workspace — authoritative for this job's work; "
                   "its stages/ are read on demand. Where it says to finish or close the run, "
                   "CHECKPOINT this job instead: the run goes on with the next due job, and "
                   "the summary it describes is the checkpoint's):\n" + recipe)
    if digest := job_digest(ws):
        out.append("ITS STATE:\n" + digest)
    owed = donewhen.parse(recipe) if recipe else []
    acct = (f", accounting: one entry per Done-when line ({', '.join(d['id'] for d in owed)})"
            if owed else "")
    out.append(f"Close it with job verb=checkpoint id={job['id']} (outcome, summary{acct}).")
    return "\n\n".join(out)

