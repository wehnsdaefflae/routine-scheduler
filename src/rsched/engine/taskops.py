"""The TASK layer, engine-side: the `task` action, the run's processing LEDGER, and the gates
that hold a run to the tasks it owes — GATED PROCESSING (docs/tasks.md). The store and the
due computation are `rsched/tasks.py`.

One run's life with tasks, in order:

- BOOT (`boot`): a fresh run computes which tasks are DUE — from its run gate's answers, each
  task's own clock and what earlier runs left over — and writes that into the store's `run`
  ledger; a resumed leg reads the ledger back, the open task included. The digest names the
  due tasks in order (`digest_section`).
- OPEN: `task verb=open` makes one task the run's focus. Its workspace becomes the run's
  working directory (`RunContext.work_dir`) and the observation briefs the run on it: why it
  is due, its brief, its own recipe, its own state digest, its last checkpoint. Opening a task
  while another is open is refused — processing is one gate at a time.
- CHECKPOINT: `task verb=checkpoint` closes the open task with an outcome and a summary, and
  — when the task has a recipe with a `## Done when` — an accounting of its lines, checked the
  way a finish's is. The next due task opens in the same observation.
- FINISH (`finish_owed`, a rung of engine/finishgate.py): a finish is set aside while a due
  task has no checkpoint. Never on the reserved finish turn, never in a child, never in a
  conversation (whose replies are not a schedule's due list).
- END (`close_out`): whatever the run was owed and did not checkpoint is CARRIED — the next
  run processes it whatever its gate checks say.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import tasks
from ..ids import now_iso
from ..paths import read_json
from . import accounting, donewhen, notes
from .run_context import RunContext
from .runkind import is_conversation

RECIPE_MAX_CHARS = 24_000
LEDGER_TAIL = 20
MEMORY_INDEX_LINES = 40


def enabled(ctx: RunContext) -> bool:
    """Does this run keep tasks? The setting, at the top level only: a child works on its
    parent's brief and never sees the task kind (its capabilities are off).
    """
    return ctx.depth == 0 and ctx.grants is not None and ctx.grants.tasks_on


def _clock(ctx: RunContext) -> tuple[date, datetime]:
    # the ROUTINE's calendar, as the gate's `dates` checks read it (gate_prepare)
    now = datetime.now(ZoneInfo(ctx.routine.tz))
    return now.date(), now


def _set_open(ctx: RunContext, task: dict | None) -> None:
    if task is None:
        ctx.task_id, ctx.task_dir = "", None
        return
    ctx.task_id = task["id"]
    ctx.task_dir = tasks.workspace(ctx.routine.dir, task)
    ctx.task_dir.mkdir(parents=True, exist_ok=True)


def _orphaned(doc: dict, ledger: dict) -> None:
    """A previous run that never reached its close-out (killed before `close_out`): what it
    was owed and did not checkpoint is carried, exactly as an ordinary ending would have.
    """
    if not ledger.get("run_id") or ledger.get("ended"):
        return
    done = set(ledger.get("done") or {})
    for item in ledger.get("due") or []:
        task = tasks.find(doc, str(item.get("id")))
        if task is not None and task["id"] not in done and task.get("state") == "active":
            task["carry"] = (f"run {ledger['run_id']} stopped before its close-out without "
                             "checkpointing it")


def boot(ctx: RunContext) -> str:
    """Open this run's ledger (fresh) or reload it (resumed), restore an open task, and return
    the digest section. "" when the run keeps no tasks.
    """
    if not enabled(ctx):
        return ""
    today, now = _clock(ctx)
    with tasks.editing(ctx.routine.dir) as doc:
        ledger = doc.get("run") or {}
        if ledger.get("run_id") != ctx.run_id:
            _orphaned(doc, ledger)
            gate = read_json(ctx.run_dir / "gate.json")
            due, resting = tasks.due_for_run(
                doc, gate=gate if isinstance(gate, dict) else None,
                checks=list(ctx.routine.run_gate.checks), today=today, now=now)
            ledger = {"run_id": ctx.run_id, "started": now_iso(), "due": due,
                      "resting": resting, "done": {}, "open": ""}
            doc["run"] = ledger
    _set_open(ctx, tasks.find(doc, str(ledger.get("open") or "")))
    return digest_section(doc)


def _line(task: dict) -> str:
    last = task.get("last") or {}
    seen = (f"last processed {str(last.get('at'))[:10]} ({last.get('outcome')})"
            if last.get("at") else "never processed")
    extra = [f"wakes {task['wake']}"] if task.get("wake") else []
    extra += [f"quiet limit {task['quiet_days']}d"] if task.get("quiet_days") else []
    return (f"{task['id']} — {task.get('title') or task['id']} · {seen}"
            + (f" · {', '.join(extra)}" if extra else ""))


def digest_section(doc: dict) -> str:
    """The TASKS section of the boot digest: what this run owes, in order, and why."""
    ledger = doc.get("run") or {}
    by_id = {t["id"]: t for t in doc.get("tasks") or []}
    out = [("TASKS — this routine's standing work (the `task` action manages them). GATED "
            "PROCESSING: every task DUE this run must be opened and checkpointed before the "
            "engine lets you finish — one at a time, in this order unless the work says "
            "otherwise.")]
    due = [d for d in ledger.get("due") or [] if d.get("id") in by_id]
    if due:
        out.append("Due this run:")
        for n, item in enumerate(due, 1):
            out.append(f"  {n}. {_line(by_id[item['id']])}")
            out.append(f"     due because: {'; '.join(item.get('why') or [])}")
    else:
        out.append("Due this run: none — nothing in the task list is owed. Do the routine's "
                   "own work, and open a task only where you find work for it.")
    resting = [r for r in ledger.get("resting") or [] if r.get("id") in by_id]
    if resting:
        out.append("Resting this run (not due — open one only if you find work for it):")
        out += [f"  - {_line(by_id[r['id']])}" for r in resting]
    other = [t for t in doc.get("tasks") or [] if t.get("state", "active") != "active"]
    if other:
        out.append("Paused / done: " + "; ".join(f"{t['id']} ({t.get('state')})"
                                                  for t in other))
    if ledger.get("open"):
        out.append(f"OPEN NOW (resumed): {ledger['open']} — its workspace is your working "
                   "directory until you checkpoint it.")
    return "\n".join(out)


# ── the briefing a task opens with ────────────────────────────────────────────────────────

def _recipe(ws: Path) -> str:
    try:
        text = (ws / "main.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > RECIPE_MAX_CHARS:
        text = (text[:RECIPE_MAX_CHARS]
                + f"\n[… recipe truncated at {RECIPE_MAX_CHARS} chars — read_file main.md]")
    return text


def _task_digest(ws: Path) -> str:
    """The task's own state, the way a routine's boot digest shows a routine's."""
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
        parts.append(".memory/ index (THIS task's notebook — memory_read before "
                     "re-discovering anything):\n" + "\n".join(lines[:MEMORY_INDEX_LINES]) + more)
    return "\n\n".join(parts)


def open_briefing(ctx: RunContext, task: dict, ledger: dict) -> str:
    ws = tasks.workspace(ctx.routine.dir, task)
    rel = tasks.workspace_rel(task)
    due = [d.get("id") for d in ledger.get("due") or []]
    why = next((d.get("why") for d in ledger.get("due") or [] if d.get("id") == task["id"]),
               None) or ["opened by the run"]
    place = (f"due task {due.index(task['id']) + 1} of {len(due)}" if task["id"] in due
             else "not due this run — opened by you")
    out = [f"TASK OPEN — {task['id']}: {task.get('title') or task['id']} ({place})",
           f"Due because: {'; '.join(why)}",
           f"Brief: {task.get('brief') or '(none — set one with task verb=update)'}",
           (f"Workspace: {rel}/ ({ws}) — your WORKING DIRECTORY until you checkpoint this "
            "task: relative paths, scripts, shell and util calls and memory_read / "
            "memory_write resolve here. The routine's own files stay reachable by their "
            "absolute path.")]
    if task.get("carry"):
        out.append(f"Carried over: {task['carry']}")
    last = task.get("last") or {}
    if last:
        out.append(f"Last checkpoint ({str(last.get('at'))[:16]}, {last.get('outcome')}, run "
                   f"{last.get('run')}):\n{last.get('summary')}")
    if recipe := _recipe(ws):
        # a task's recipe is often a former routine's, written for a run of its own
        out.append("ITS RECIPE (main.md in the workspace — authoritative for this task's work; "
                   "its stages/ are read on demand. Where it says to finish or close the run, "
                   "CHECKPOINT this task instead: the run goes on with the next due task, and "
                   "the summary it describes is the checkpoint's):\n" + recipe)
    if digest := _task_digest(ws):
        out.append("ITS STATE:\n" + digest)
    owed = donewhen.parse(recipe) if recipe else []
    acct = (f", accounting: one entry per Done-when line ({', '.join(d['id'] for d in owed)})"
            if owed else "")
    out.append(f"Close it with task verb=checkpoint id={task['id']} (outcome, summary{acct}).")
    return "\n\n".join(out)


# ── the action ────────────────────────────────────────────────────────────────────────────

def _obs(verb: str, message: str, **extra) -> dict:
    return {"kind": "task", "verb": verb, "message": message, **extra}


def _refuse(verb: str, message: str) -> dict:
    return {"kind": "task", "verb": verb, "rejected": True, "reason": message}


def _next_due(doc: dict) -> dict | None:
    ledger = doc.get("run") or {}
    done = ledger.get("done") or {}
    for item in ledger.get("due") or []:
        task = tasks.find(doc, str(item.get("id")))
        if task is not None and task["id"] not in done and task.get("state") == "active":
            return task
    return None


def handle_task(loop, action: dict) -> dict:
    """Route one `task` action. Every verb edits the store under its lock and answers with an
    observation carrying its whole message, so a resumed leg replays exactly what was read.
    """
    ctx = loop.ctx
    verb = str(action.get("verb") or "")
    if not enabled(ctx):
        return _refuse(verb, "this routine keeps no tasks (the task layer is off)")
    handler = {"list": _list, "create": _create, "update": _update, "open": _open,
               "checkpoint": _checkpoint, "delete": _delete}[verb]
    return handler(ctx, action)


def _list(ctx: RunContext, _action: dict) -> dict:
    doc = tasks.load(ctx.routine.dir)
    ledger = doc.get("run") or {}
    due = {d.get("id") for d in ledger.get("due") or []}
    done = ledger.get("done") or {}
    rows = []
    for t in doc["tasks"]:
        mark = ("OPEN" if t["id"] == ctx.task_id else
                f"done this run ({done[t['id']]})" if t["id"] in done else
                "DUE" if t["id"] in due else t.get("state", "active"))
        rows.append(f"- [{mark}] {_line(t)} · workspace {tasks.workspace_rel(t)}/"
                    + (f" · carried: {t['carry']}" if t.get("carry") else ""))
    return _obs("list", "\n".join(rows) or "(no tasks yet — create one with verb=create)")


def _create(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    rel = str(action.get("path") or "").strip()
    if rel and (why := tasks.workspace_problem(rel)):
        return _refuse("create", why)
    with tasks.editing(ctx.routine.dir) as doc:
        if tasks.find(doc, tid) is not None:
            return _refuse("create", f"a task {tid!r} already exists — update it instead")
        task = tasks.new_task(tid, str(action["title"]).strip(), str(action["brief"]).strip(),
                              by=ctx.run_id, workspace=rel,
                              quiet_days=action.get("quiet_days"),
                              wake=str(action.get("wake") or ""),
                              state=str(action.get("state") or "active"))
        doc["tasks"].append(task)
    tasks.workspace(ctx.routine.dir, task).mkdir(parents=True, exist_ok=True)
    return _obs("create", f"task {tid!r} created ({task['state']}), workspace "
                f"{tasks.workspace_rel(task)}/. It is not due this run unless you open it; "
                "from the next run on its clock and gate checks decide.", id=tid)


def _update(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    changed = []
    with tasks.editing(ctx.routine.dir) as doc:
        task = tasks.find(doc, tid)
        if task is None:
            return _refuse("update", f"no task {tid!r} (task verb=list shows them)")
        state = action.get("state")
        if state and state != task.get("state", "active") and tid == ctx.task_id:
            return _refuse("update", f"{tid!r} is open — checkpoint it before changing its "
                           "state")
        if (rel := str(action.get("path") or "").strip()) and (why := tasks.workspace_problem(
                rel)):
            return _refuse("update", why)
        if rel and tid == ctx.task_id:
            return _refuse("update", f"{tid!r} is open — checkpoint it before moving its "
                           "workspace")
        for field, value in (("title", action.get("title")), ("brief", action.get("brief")),
                             ("workspace", rel or None), ("state", state),
                             ("quiet_days", action.get("quiet_days"))):
            if value not in (None, ""):
                task[field] = value
                changed.append(field)
        if "wake" in action:
            if action["wake"]:
                task["wake"] = str(action["wake"])
            else:
                task.pop("wake", None)
            changed.append("wake")
        if state in ("paused", "done"):
            task.pop("carry", None)
            ledger = doc.get("run") or {}
            if any(d.get("id") == tid for d in ledger.get("due") or []):
                ledger.setdefault("done", {})[tid] = state   # a decision settles it this run
        task["updated"] = now_iso()
    return _obs("update", f"task {tid!r} updated: {', '.join(changed) or 'nothing changed'}.",
                id=tid)


def _open(ctx: RunContext, action: dict) -> dict:
    tid = str(action.get("id") or "")
    with tasks.editing(ctx.routine.dir) as doc:
        ledger = doc.setdefault("run", {})
        if ctx.task_id and ctx.task_id != tid:
            return _refuse("open", f"task {ctx.task_id!r} is still open — checkpoint it first "
                           "(outcome deferred, saying why, if you are setting it aside)")
        task = tasks.find(doc, tid) if tid else _next_due(doc)
        if task is None:
            return _refuse("open", f"no task {tid!r} (task verb=list shows them)" if tid
                           else "no due task is left unprocessed this run")
        if task.get("state", "active") != "active":
            return _refuse("open", f"task {task['id']!r} is {task.get('state')} — update its "
                           "state to active first if there is work for it")
        if not any(d.get("id") == task["id"] for d in ledger.get("due") or []):
            ledger.setdefault("due", []).append({"id": task["id"], "why": ["opened by the run"]})
        ledger["open"] = task["id"]
    _set_open(ctx, task)
    return _obs("open", open_briefing(ctx, task, ledger), id=task["id"])


def _checkpoint(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    outcome = str(action.get("outcome") or "")
    today, _now = _clock(ctx)
    with tasks.editing(ctx.routine.dir) as doc:
        task = tasks.find(doc, tid)
        if task is None:
            return _refuse("checkpoint", f"no task {tid!r} (task verb=list shows them)")
        if ctx.task_id and ctx.task_id != tid:
            return _refuse("checkpoint", f"task {ctx.task_id!r} is the open one — checkpoint "
                           "it first; tasks are processed one at a time")
        owed = donewhen.parse(_recipe(tasks.workspace(ctx.routine.dir, task)))
        verdicts = accounting.parse(action.get("accounting"))
        if owed and outcome != "deferred":
            found = accounting.problems(verdicts, owed, [])
            if any(found.values()):
                return _refuse("checkpoint", (
                    f"the checkpoint's `accounting` is incomplete: {accounting.gaps(found)}. "
                    "Carry one entry per line of the task recipe's Done when — `d<n> met: "
                    "<evidence>`, `d<n> unmet: <what remains>`, `d<n> not due: <how "
                    "established>` — then checkpoint again (outcome deferred needs none)."))
        tasks.record_checkpoint(task, run_id=ctx.run_id, outcome=outcome,
                                summary=str(action["summary"]),
                                accounting=list(action.get("accounting") or []),
                                wake=action.get("wake"), today=today)
        ledger = doc.setdefault("run", {})
        ledger.setdefault("done", {})[tid] = outcome
        nxt = _next_due(doc)
        ledger["open"] = nxt["id"] if nxt else ""
    _set_open(ctx, nxt)
    head = f"task {tid!r} checkpointed ({outcome})."
    if nxt is not None:
        return _obs("checkpoint", head + " The next due task opens now — the working "
                    "directory moved to its workspace.\n\n" + open_briefing(ctx, nxt, ledger),
                    id=tid, opened=nxt["id"])
    return _obs("checkpoint", head + " Every due task is processed; the working directory is "
                "the routine's own again. Do the routine's closing steps, then finish.", id=tid)


def _delete(ctx: RunContext, action: dict) -> dict:
    tid = str(action["id"])
    if tid == ctx.task_id:
        return _refuse("delete", f"{tid!r} is open — checkpoint it first")
    with tasks.editing(ctx.routine.dir) as doc:
        task = tasks.find(doc, tid)
        if task is None:
            return _refuse("delete", f"no task {tid!r}")
        doc["tasks"] = [t for t in doc["tasks"] if t["id"] != tid]
        doc["deleted"] = [{**task, "deleted": now_iso(), "deleted_by": ctx.run_id},
                          *(doc.get("deleted") or [])][:tasks.DELETED_KEEP]
        ledger = doc.get("run") or {}
        if any(d.get("id") == tid for d in ledger.get("due") or []):
            ledger.setdefault("done", {})[tid] = "deleted"
    return _obs("delete", f"task {tid!r} deleted from the list; its workspace "
                f"{tasks.workspace_rel(task)}/ stays on disk.", id=tid)


def format_task(obs: dict, kind: str) -> str | None:
    """The observation wording for `task` — the handler wrote the whole message."""
    if kind != "task":
        return None
    return f"OBSERVATION (task {obs.get('verb')}): {obs.get('message', '')}"


# ── the gates ─────────────────────────────────────────────────────────────────────────────

def finish_owed(loop) -> str | None:
    """The finish rung: the message a finish is set aside with while a due task has no
    checkpoint, or None. Asked at depth 0 of a scheduled routine and never on the reserved
    finish turn — the engine never ends a run the model could have ended itself, and it never
    keeps one alive past its budget either.
    """
    ctx = loop.ctx
    if not enabled(ctx) or loop._finish_reserved or is_conversation(ctx):
        return None
    doc = tasks.load(ctx.routine.dir)
    ledger = doc.get("run") or {}
    if ledger.get("run_id") != ctx.run_id:
        return None
    done = ledger.get("done") or {}
    owed = [d["id"] for d in ledger.get("due") or []
            if d.get("id") not in done
            and (t := tasks.find(doc, str(d.get("id")))) is not None
            and t.get("state", "active") == "active"]
    if not owed:
        return None
    named = ", ".join(f"{t} (open)" if t == ctx.task_id else t for t in owed)
    return ("OBSERVATION (finish deferred): GATED PROCESSING — task(s) due this run have no "
            f"checkpoint yet: {named}. Each due task is opened (task verb=open) and closed "
            "with task verb=checkpoint before the run may end; when you set one aside, "
            "checkpoint it with outcome deferred and the reason, and the next run is owed it. "
            "Then finish again.")


def close_out(ctx: RunContext, status: str) -> str:
    """At the run's end: carry what it was owed and did not checkpoint. Returns the line the
    run's summary gains, "" when nothing was carried.
    """
    if not enabled(ctx):
        return ""
    try:
        carried = tasks.carry_unprocessed(ctx.routine.dir, ctx.run_id,
                                          f"ended ({status})")
    except OSError:
        return ""
    _set_open(ctx, None)
    if not carried:
        return ""
    return (f"\n[Task(s) carried to the next run — due this run and never checkpointed: "
            f"{', '.join(carried)}.]")
