# Jobs — a routine whose regular duties are several pieces of standing work

Some routines do one job. Others steward a portfolio: five projects for the same employer, each
with its own partners, deadlines, documents and status page. Before this layer existed the
portfolio was five routines, and that cost what five of anything costs — five configs drifting
apart, five lane members, five steward pages, five copies of the same conventions, and nothing
that saw the whole. The `jobs` setting makes such a portfolio ONE routine whose standing work is
a list of **jobs** it keeps itself.

## The setting

`capabilities.jobs: off | on` in `routine.yaml`, set on the routine page under **Abilities →
Run history, reminders & jobs**. It is a SETTING, like `reminders` and `runs`, not a permission:
it changes how a routine ORGANISES its own work, not what it may reach — every act a job leads
to still passes the same capabilities, rules, reminders and sandbox as before. Off (the default),
nothing below exists for the routine: the `job` kind is projected out of its schema
(`kindsurface`), and none of the gates run. A child run never holds it.

YAML 1.1 reads a bare `on` as a boolean; the loader takes that as the same word
(`grants.normalize_capabilities`), and a save writes the canonical string.

## A job

A record in the engine-owned store `state/jobs.json` (`rsched/jobs.py`):

| field | meaning |
|---|---|
| `id`, `title`, `brief` | what it is; the brief is self-contained — a later run reads nothing else about it |
| `state` | `active` (processed when due) · `paused` (kept, never due) · `done` (finished for good) |
| `workspace` | a directory inside the routine, default `jobs/<id>/` |
| `quiet_days` | due whenever it has not been processed for this long |
| `wake` | rest until this date, unless one of its gate checks finds work |
| `carry` | work an earlier run left unprocessed — the next run owes it |
| `last`, `history` | the last checkpoint (`run`, `at`, `outcome`, `summary`, `accounting`) and the twelve before it |
| `origin` | the routine this job WAS, when a routine was merged in as a job — see "Merging routines" |

The store is sealed against generic file actions (`fileops._jobs_gate`, and the removal gate):
a write that could clear a carry or fake a checkpoint would defeat the gate that reads them. The
run changes it with the `job` action; the operator through the routine page.

## The WORKSPACE is the working directory

While a job is OPEN, the run works in its workspace: relative paths, `script` (the workspace's
own `scripts/<name>.py`, run from there, with its own `.venv`; the routine's own scripts stay
reachable after it), `shell` and util calls, `memory_read` / `memory_write` (the workspace's own
`.memory/`, its INDEX engine-owned like the routine's) and the `note` channel (the workspace's
`state/notes.md`) all resolve there (`run_context.work_dir`, `resolve_action_path`). The
routine's own files stay reachable by their absolute path. Every seal stays anchored on the
routine's directory, which contains the workspace, so moving the working directory moves nothing
a seal guards; a `.memory/` anywhere under the routine is the engine's.

That is what lets a whole routine become a job UNCHANGED: its recipe (`main.md` + `stages/`),
its state files, its scripts (which read `Path(__file__).parent.parent` and cwd-relative paths),
its notebook. A job's `main.md` and `stages/` are part of the routine's RECIPE and sealed on the
same terms (`grantpolicy.is_recipe_path`): the operator's instructions for the job, editable by a
run only under `write_recipe`.

## Gated processing

One run's life with jobs (`engine/jobops.py`):

1. **Boot** decides which jobs are DUE — once, at a fresh boot; a resumed leg reads the same
   ledger back (`state/jobs.json` → `run`), open job included. The digest's `JOBS` section
   lists them in order with why, then the ones resting.
2. **Open** (`job verb=open`, no id = the next due) makes one job the focus and answers with
   its briefing: why it is due, its brief, its workspace, its last checkpoint, its recipe and its
   own state digest. Opening a second while one is open is refused.
3. **Checkpoint** closes it: an `outcome` — `advanced`, `no-work`, `blocked` or `deferred` — a
   `summary` that becomes the job's memory of the run, and, when its recipe has a `## Done
   when`, an `accounting` of those lines checked the way a finish's is. The next due job opens
   in the same observation.
4. **Finish** is set aside while a due job has no checkpoint (`finishgate._unanswered`, before
   the accounting rung). Never on the reserved finish turn, never in a child, never in a
   conversation. One action settles each line, so it cannot trap a run.
5. **End**: what the run was owed and never checkpointed is CARRIED (`jobs.carry_unprocessed`),
   and the summary says so. A run killed before its close-out is caught at the next boot
   (`jobops._orphaned`).

A run may open a job that is not due when it finds work for it, and may create, reword, pause
or delete jobs as its duties change. Deleting keeps the workspace on disk and the record in the
store's `deleted` list.

## Which jobs are due

Decided fail-open, like every gate read — a wrong "due" costs a few turns, a wrong "not due"
silently loses work (`jobs.due_for_run`):

- **The run gate's answers.** A check carrying `job: <id>` that found work makes that job due;
  a check carrying none that found work makes every job due. When the fire's checks were never
  asked — no gate, a manual run, a resume, a trigger — every active job is due.
- **The job's own clock** (`jobs.clock_reasons`): a carry, a wake date that has come, a quiet
  limit passed, or no check watching it at all (then every run is its run, until it is given a
  wake date, quiet days or a check).

A routine that keeps jobs asks its checks even when a built-in reason has already admitted the
fire (inbox freight, an answer, a store note, a changed config, a job's clock): the kit gets
that reason as `admit_reason`, decides "run" and still answers every check, so the engine learns
WHICH jobs have work instead of making all of them due (`gate_prepare.keeps_jobs`). A job due
by its own clock admits a fire its checks would skip — nothing else would bring the routine back
for it (`gate_prepare.jobs_clock_reason`).

Two check details exist for jobs (docs/run-gates.md): `job:` on any check except the routine's
own `max_quiet` backstop (a job's rest is its `quiet_days`), and `label` on `hub_feedback`, which
counts only the rows whose control id names it as a whole segment (`fau · ards/doc/x` names `fau`,
`ards`, `doc` and `x`, never `standards` for `ards`) — one job's section of a page several share.

## On the routine page

The **Jobs** panel in the overview zone lists every job with its state, whether the current or
last run owed it and what came of it, its workspace and the checks that watch it
(`GET /api/routines/{slug}/jobs`). Pause, resume and done are the operator's
(`PATCH /api/routines/{slug}/jobs/{id}`, between runs only — a live run decided at boot what it
owes, and its finish is gated on exactly that). Creating and deleting are the run's. A job that
used to be a routine says so on its row (`formerly the routine <slug>`).

## On the run page

The run's rail carries a **jobs** section: which of the routine's jobs THIS run has to do with —
the one it is working on right now, what is still due, and what it checkpointed, with the
outcome. The run page is where a run is actually watched, and the routine page's panel shows the
STORE rather than this run (operator order: *"routines with tasks … need to display the task
they're currently working on in the right sidebar"*).

It reads the same `GET /api/routines/{slug}/jobs`. For the ledger's own run the open job leads;
for a run that has ended, each job whose history still holds an entry for that run id reports the
outcome it recorded then — so yesterday's run page never claims the job today's run has open. A
run's `status.json` carries the open job's id too, but the engine clears it at close-out, which is
why the rail reads the store instead. A routine without the job layer gains no section at all.

## Merging routines

The layer exists so that several routines doing related standing work can become ONE: each
former routine becomes a job whose workspace holds its whole working tree unchanged (recipe,
state, scripts, notes), and the old routine is switched off — never deleted, so its runs and
history stay where they were. The first merge (2026-10-07) made five FAU project stewards the
jobs of `fau`.

What the old routine RECEIVED does not stop arriving: other routines still know its slug and keep
addressing reports — and shared-store notes — to it. A switched-off routine starts no run, so both
channels refuse at sending (docs/items.md, "A target that would never READ it"; docs/lanes-tags.md
for notes) and name where the message should go instead (`rsched/recipients.py`). The job's
`origin` is what makes that answer exact: a report for `nanogeofeld` is refused with *"'fau'
(carries 'nanogeofeld' as its job 'nanogeofeld' — address 'fau' and name the job
'nanogeofeld' in the title)"* first, before the lane-mates, store-sharers and tag-sharers. The run
never writes `origin`; whoever merges records it (`PATCH /api/routines/{slug}/jobs/{id}`
`{"origin": "<old slug>"}`, which refuses a slug that is not another routine here).
