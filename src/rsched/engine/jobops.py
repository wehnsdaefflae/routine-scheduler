"""The JOB layer, engine-side: the `job` action, the run's processing LEDGER, and the gates
that hold a run to the jobs it owes — GATED PROCESSING (docs/jobs.md). The store and the
due computation are `rsched/jobs.py`.

One run's life with jobs, in order:

- BOOT (`boot`): a fresh run computes which jobs are DUE — from its run gate's answers, each
  job's own clock and what earlier runs left over — and writes that into the store's `run`
  ledger; a resumed leg reads the ledger back, the open job included. The digest names the
  due jobs in order (`digest_section`).
- OPEN: `job verb=open` makes one job the run's focus. Its workspace becomes the run's
  working directory (`RunContext.work_dir`) and the observation briefs the run on it: why it
  is due, its brief, its own recipe, its own state digest, its last checkpoint. Opening a job
  while another is open is refused — processing is one gate at a time.
- CHECKPOINT: `job verb=checkpoint` closes the open job with an outcome and a summary, and
  — when the job has a recipe with a `## Done when` — an accounting of its lines, checked the
  way a finish's is. The next due job opens in the same observation.
- FINISH (`finish_owed`, a rung of engine/finishgate.py): a finish is set aside while a due
  job has no checkpoint. Never on the reserved finish turn, never in a child, never in a
  conversation (whose replies are not a schedule's due list).
- END (`close_out`): whatever the run was owed and did not checkpoint is CARRIED — the next
  run processes it whatever its gate checks say.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .. import jobs
from ..ids import now_iso
from ..paths import read_json
from . import accounting, donewhen
from .jobbrief import digest_section, job_line, open_briefing, recipe_text
from .run_context import RunContext
from .runkind import is_conversation


def enabled(ctx: RunContext) -> bool:
    """Does this run keep jobs? The setting, at the top level only: a child works on its
    parent's brief and never sees the job kind (its capabilities are off).
    """
    return ctx.depth == 0 and ctx.grants is not None and ctx.grants.jobs_on


def _clock(ctx: RunContext) -> tuple[date, datetime]:
    # the ROUTINE's calendar, as the gate's `dates` checks read it (gate_prepare)
    now = datetime.now(ZoneInfo(ctx.routine.tz))
    return now.date(), now


def _set_open(ctx: RunContext, job: dict | None) -> None:
    if job is None:
        ctx.job_id, ctx.job_dir = "", None
        return
    ctx.job_id = job["id"]
    ctx.job_dir = jobs.workspace(ctx.routine.dir, job)
    ctx.job_dir.mkdir(parents=True, exist_ok=True)


def _orphaned(doc: dict, ledger: dict) -> None:
    """A previous run that never reached its close-out (killed before `close_out`): what it
    was owed and did not checkpoint is carried, exactly as an ordinary ending would have.
    """
    if not ledger.get("run_id") or ledger.get("ended"):
        return
    done = set(ledger.get("done") or {})
    for item in ledger.get("due") or []:
        job = jobs.find(doc, str(item.get("id")))
        if job is not None and job["id"] not in done and job.get("state") == "active":
            job["carry"] = (f"run {ledger['run_id']} stopped before its close-out without "
                             "checkpointing it")


def boot(ctx: RunContext) -> str:
    """Open this run's ledger (fresh) or reload it (resumed), restore an open job, and return
    the digest section. "" when the run keeps no jobs.
    """
    if not enabled(ctx):
        return ""
    today, now = _clock(ctx)
    with jobs.editing(ctx.routine.dir) as doc:
        ledger = doc.get("run") or {}
        if ledger.get("run_id") != ctx.run_id:
            _orphaned(doc, ledger)
            gate = read_json(ctx.run_dir / "gate.json")
            due, resting = jobs.due_for_run(
                doc, gate=gate if isinstance(gate, dict) else None,
                checks=list(ctx.routine.run_gate.checks), today=today, now=now)
            ledger = {"run_id": ctx.run_id, "started": now_iso(), "due": due,
                      "resting": resting, "done": {}, "open": ""}
            doc["run"] = ledger
    _set_open(ctx, jobs.find(doc, str(ledger.get("open") or "")))
    return digest_section(doc)


# ── the action ────────────────────────────────────────────────────────────────────────────

def _obs(verb: str, message: str, **extra) -> dict:
    return {"kind": "job", "verb": verb, "message": message, **extra}


def _refuse(verb: str, message: str) -> dict:
    return {"kind": "job", "verb": verb, "rejected": True, "reason": message}


def _next_due(doc: dict) -> dict | None:
    ledger = doc.get("run") or {}
    done = ledger.get("done") or {}
    for item in ledger.get("due") or []:
        job = jobs.find(doc, str(item.get("id")))
        if job is not None and job["id"] not in done and job.get("state") == "active":
            return job
    return None


def handle_job(loop, action: dict) -> dict:
    """Route one `job` action. Every verb edits the store under its lock and answers with an
    observation carrying its whole message, so a resumed leg replays exactly what was read.
    """
    ctx = loop.ctx
    verb = str(action.get("verb") or "")
    if not enabled(ctx):
        return _refuse(verb, "this routine keeps no jobs (the job layer is off)")
    handler = {"list": _list, "create": _create, "update": _update, "open": _open,
               "checkpoint": _checkpoint, "delete": _delete}[verb]
    return handler(ctx, action)


def _list(ctx: RunContext, _action: dict) -> dict:
    doc = jobs.load(ctx.routine.dir)
    ledger = doc.get("run") or {}
    due = {d.get("id") for d in ledger.get("due") or []}
    done = ledger.get("done") or {}
    rows = []
    for t in doc["jobs"]:
        mark = ("OPEN" if t["id"] == ctx.job_id else
                f"done this run ({done[t['id']]})" if t["id"] in done else
                "DUE" if t["id"] in due else t.get("state", "active"))
        rows.append(f"- [{mark}] {job_line(t)} · workspace {jobs.workspace_rel(t)}/"
                    + (f" · carried: {t['carry']}" if t.get("carry") else ""))
    return _obs("list", "\n".join(rows) or "(no jobs yet — create one with verb=create)")


def _create(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    rel = str(action.get("path") or "").strip()
    if rel and (why := jobs.workspace_problem(rel)):
        return _refuse("create", why)
    with jobs.editing(ctx.routine.dir) as doc:
        if jobs.find(doc, tid) is not None:
            return _refuse("create", f"a job {tid!r} already exists — update it instead")
        job = jobs.new_job(tid, str(action["title"]).strip(), str(action["brief"]).strip(),
                              by=ctx.run_id, workspace=rel,
                              quiet_days=action.get("quiet_days"),
                              wake=str(action.get("wake") or ""),
                              state=str(action.get("state") or "active"))
        doc["jobs"].append(job)
    jobs.workspace(ctx.routine.dir, job).mkdir(parents=True, exist_ok=True)
    return _obs("create", f"job {tid!r} created ({job['state']}), workspace "
                f"{jobs.workspace_rel(job)}/. It is not due this run unless you open it; "
                "from the next run on its clock and gate checks decide.", id=tid)


def _update(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    changed = []
    with jobs.editing(ctx.routine.dir) as doc:
        job = jobs.find(doc, tid)
        if job is None:
            return _refuse("update", f"no job {tid!r} (job verb=list shows them)")
        state = action.get("state")
        if state and state != job.get("state", "active") and tid == ctx.job_id:
            return _refuse("update", f"{tid!r} is open — checkpoint it before changing its "
                           "state")
        if (rel := str(action.get("path") or "").strip()) and (why := jobs.workspace_problem(
                rel)):
            return _refuse("update", why)
        if rel and tid == ctx.job_id:
            return _refuse("update", f"{tid!r} is open — checkpoint it before moving its "
                           "workspace")
        for field, value in (("title", action.get("title")), ("brief", action.get("brief")),
                             ("workspace", rel or None), ("state", state),
                             ("quiet_days", action.get("quiet_days"))):
            if value not in (None, ""):
                job[field] = value
                changed.append(field)
        if "wake" in action:
            if action["wake"]:
                job["wake"] = str(action["wake"])
            else:
                job.pop("wake", None)
            changed.append("wake")
        if state in ("paused", "done"):
            job.pop("carry", None)
            ledger = doc.get("run") or {}
            if any(d.get("id") == tid for d in ledger.get("due") or []):
                ledger.setdefault("done", {})[tid] = state   # a decision settles it this run
        job["updated"] = now_iso()
    return _obs("update", f"job {tid!r} updated: {', '.join(changed) or 'nothing changed'}.",
                id=tid)


def _open(ctx: RunContext, action: dict) -> dict:
    tid = str(action.get("id") or "")
    with jobs.editing(ctx.routine.dir) as doc:
        ledger = doc.setdefault("run", {})
        if ctx.job_id and ctx.job_id != tid:
            return _refuse("open", f"job {ctx.job_id!r} is still open — checkpoint it first "
                           "(outcome deferred, saying why, if you are setting it aside)")
        job = jobs.find(doc, tid) if tid else _next_due(doc)
        if job is None:
            return _refuse("open", f"no job {tid!r} (job verb=list shows them)" if tid
                           else "no due job is left unprocessed this run")
        if job.get("state", "active") != "active":
            return _refuse("open", f"job {job['id']!r} is {job.get('state')} — update its "
                           "state to active first if there is work for it")
        if not any(d.get("id") == job["id"] for d in ledger.get("due") or []):
            ledger.setdefault("due", []).append({"id": job["id"], "why": ["opened by the run"]})
        ledger["open"] = job["id"]
    _set_open(ctx, job)
    return _obs("open", open_briefing(ctx, job, ledger), id=job["id"])


def _checkpoint(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    outcome = str(action.get("outcome") or "")
    today, _now = _clock(ctx)
    with jobs.editing(ctx.routine.dir) as doc:
        job = jobs.find(doc, tid)
        if job is None:
            return _refuse("checkpoint", f"no job {tid!r} (job verb=list shows them)")
        if ctx.job_id and ctx.job_id != tid:
            return _refuse("checkpoint", f"job {ctx.job_id!r} is the open one — checkpoint "
                           "it first; jobs are processed one at a time")
        owed = donewhen.parse(recipe_text(jobs.workspace(ctx.routine.dir, job)))
        verdicts = accounting.parse(action.get("accounting"))
        if owed and outcome != "deferred":
            found = accounting.problems(verdicts, owed, [])
            if any(found.values()):
                return _refuse("checkpoint", (
                    f"the checkpoint's `accounting` is incomplete: {accounting.gaps(found)}. "
                    "Carry one entry per line of the job recipe's Done when — `d<n> met: "
                    "<evidence>`, `d<n> unmet: <what remains>`, `d<n> not due: <how "
                    "established>` — then checkpoint again (outcome deferred needs none)."))
        jobs.record_checkpoint(job, run_id=ctx.run_id, outcome=outcome,
                                summary=str(action["summary"]),
                                accounting=list(action.get("accounting") or []),
                                wake=action.get("wake"), today=today)
        ledger = doc.setdefault("run", {})
        ledger.setdefault("done", {})[tid] = outcome
        nxt = _next_due(doc)
        ledger["open"] = nxt["id"] if nxt else ""
    _set_open(ctx, nxt)
    head = f"job {tid!r} checkpointed ({outcome})."
    if nxt is not None:
        return _obs("checkpoint", head + " The next due job opens now — the working "
                    "directory moved to its workspace.\n\n" + open_briefing(ctx, nxt, ledger),
                    id=tid, opened=nxt["id"])
    return _obs("checkpoint", head + " Every due job is processed; the working directory is "
                "the routine's own again. Do the routine's closing steps, then finish.", id=tid)


def _delete(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    if tid == ctx.job_id:
        return _refuse("delete", f"{tid!r} is open — checkpoint it first")
    with jobs.editing(ctx.routine.dir) as doc:
        job = jobs.find(doc, tid)
        if job is None:
            return _refuse("delete", f"no job {tid!r}")
        doc["jobs"] = [t for t in doc["jobs"] if t["id"] != tid]
        doc["deleted"] = [{**job, "deleted": now_iso(), "deleted_by": ctx.run_id},
                          *(doc.get("deleted") or [])][:jobs.DELETED_KEEP]
        ledger = doc.get("run") or {}
        if any(d.get("id") == tid for d in ledger.get("due") or []):
            ledger.setdefault("done", {})[tid] = "deleted"
    return _obs("delete", f"job {tid!r} deleted from the list; its workspace "
                f"{jobs.workspace_rel(job)}/ stays on disk.", id=tid)


def format_job(obs: dict, kind: str) -> str | None:
    """The observation wording for `job` — the handler wrote the whole message."""
    if kind != "job":
        return None
    return f"OBSERVATION (job {obs.get('verb')}): {obs.get('message', '')}"


# ── the gates ─────────────────────────────────────────────────────────────────────────────

def finish_owed(loop) -> str | None:
    """The finish rung: the message a finish is set aside with while a due job has no
    checkpoint, or None. Asked at depth 0 of a scheduled routine and never on the reserved
    finish turn — the engine never ends a run the model could have ended itself, and it never
    keeps one alive past its budget either.
    """
    ctx = loop.ctx
    if not enabled(ctx) or loop._finish_reserved or is_conversation(ctx):
        return None
    doc = jobs.load(ctx.routine.dir)
    ledger = doc.get("run") or {}
    if ledger.get("run_id") != ctx.run_id:
        return None
    done = ledger.get("done") or {}
    owed = [d["id"] for d in ledger.get("due") or []
            if d.get("id") not in done
            and (t := jobs.find(doc, str(d.get("id")))) is not None
            and t.get("state", "active") == "active"]
    if not owed:
        return None
    named = ", ".join(f"{t} (open)" if t == ctx.job_id else t for t in owed)
    return ("OBSERVATION (finish deferred): GATED PROCESSING — job(s) due this run have no "
            f"checkpoint yet: {named}. Each due job is opened (job verb=open) and closed "
            "with job verb=checkpoint before the run may end; when you set one aside, "
            "checkpoint it with outcome deferred and the reason, and the next run is owed it. "
            "Then finish again.")


def close_out(ctx: RunContext, status: str) -> str:
    """At the run's end: carry what it was owed and did not checkpoint. Returns the line the
    run's summary gains, "" when nothing was carried.
    """
    if not enabled(ctx):
        return ""
    try:
        carried = jobs.carry_unprocessed(ctx.routine.dir, ctx.run_id,
                                          f"ended ({status})")
    except OSError:
        return ""
    _set_open(ctx, None)
    if not carried:
        return ""
    return (f"\n[Job(s) carried to the next run — due this run and never checkpointed: "
            f"{', '.join(carried)}.]")
