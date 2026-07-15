# Subtasks — sequential task decomposition

A run structures its work three ways, and they are deliberately different:

- **Phases** — the milestone states from the routine's own workflow (`state/phase.json`,
  shown in the state-graph rail). Cross-run, coarse.
- **Subtasks** — a *sequential* decomposition of THIS run's work into ordered steps, each run
  to completion before the next. New in this feature.
- **Subruns** — *parallel* child routines (`spawn`), running concurrently.

The key idea: **a subtask and a subroutine are the same thing** — a *child task materialized
from a workflow pattern and run recursively* — differing only in **scheduling** (sequential vs
parallel) and **budget**. Both are real routines on disk under `runs/<ts>/sub/<n>/` while they
run, with their own context window, pattern, and finish summary. Decomposition is **recursive**:
a child can decompose again (bounded by `max_subrun_depth`).

## The `subtask` action

```json
{"say": "Drafting the report from the gathered sources.",
 "kind": "subtask",
 "prompt": "<self-contained brief — fold in the previous subtask's result>",
 "workflow": "general-task",   // optional: a library pattern for this step's purpose
 "label": "draft",             // optional: the tree node name
 "turns": 12}                  // optional: this child's turn budget (default: half your remainder)
```

`subtask` is **non-blocking** — it starts the child in the background (its own thread, its own
`EngineLoop`) and returns immediately, so the conversation stays live while it runs. Mechanically
it is a subrun tagged `sequential` with a `turns` budget. To keep sequential order you **wait**
for it — `wait n=N` — before starting the next subtask, and fold its result into that brief. The
wait is **responsive**: it wakes on the child's completion (the finished-hook) but *yields* the
moment a user message arrives, handing control back so the parent can reply, then you `wait` again.
Its completion is delivered by the turn-boundary "SUBTASK FINISHED" hook (`announce_finished_subruns`)
whether you are waiting or doing other work. Unlike a plain workflow step, a subtask gets its own
fresh context window and pattern; unlike `spawn` (parallel, disjoint outputs) it is meant to run
one-at-a-time with results forwarded.

## The decomposition gate

Concrete subtasks are never known statically — you can't put them in the workflow file. Instead
the workflow pattern carries a **decomposition decision**: a step that decides *whether* to split
the current work and *how*. The default `general-task` pattern's `decompose_decision()` returns
one of:

- **inline** — do it directly in this run's turns (the common case; most runs are small);
- **sequential** — one large task that splits into ORDERED steps where each depends on the
  previous (research → draft → review) → a `subtask` per step;
- **parallel** — many INDEPENDENT items with disjoint outputs → `spawn` per item.

Decompose only when it earns the coordination cost; a few steps you can verify inline should stay
inline.

## Pattern sourcing: catalog vs generate

Each subtask is matched to a workflow pattern. The baseline (always on) is **pick from the
catalog** — you name any library pattern in `workflow` (the CAPABILITIES section lists them). When
none fits and the routine holds the `workflows: generate` capability, set `workflow` to
`"generate"` and the engine **drafts a new pattern** for the child's brief (lint-gated, committed
to the library, its system-model spend folded into your run's budget). Generation is off by
default (a user-set capability, covered by the `workflow-generation` permission) and is skipped
when the token budget is nearly spent.

## Budgets

Every budget in the system — the whole run, a conversation reply window, a subtask, a subrun —
is the same primitive: a **stop condition** (a limit, and whether tripping it is hard) over a
**resource** (turns, tokens, wall-clock, cost), with an 85% warning. A subtask gets its own
ledger, sliced from the parent's remainder (or pinned by `turns`). Enforcement is **soft at the
parent**: a subtask that overruns its own turn cap force-finishes `partial` (like a subrun), its
85% warning fires inside the child so its model wraps up first, and the parent gets the partial
summary and re-plans — only the run-level budget hard-stops the whole tree.

## Visualization

The run and conversation rails carry a **task-tree** card (below the state graph): the recursive
tree of sequential subtasks (→) and parallel subruns (⇉), each a node with a state icon, its
workflow pattern, and a per-node turn-budget meter (amber ≥85%, red over), children nested. It is
a read-model over the on-disk `sub/` transcripts — live while the run runs. In the transcript,
each child unfolds in place; `run-once` prints the same tree as `↳ subtask …` / `↰ subtask …`
lines.

## Resume

Completed subtasks are already in the parent transcript (as the wait / finished-hook messages),
so a resume replays them untouched. Children are **threads** in the parent process, so a run
interrupted with children still running finds them dead on resume — the engine marks each aborted
in the transcript and adds an ENGINE NOTE listing them, so the model re-issues what it needs
instead of `wait`-ing forever for a child that can never finish. Because children die with the
process, a subtask does **not** survive across conversation reply-finishes — a job that must
outlive a reply (a long, fire-and-forget scrape) is a different capability, the **`detach`** action
(a detached background task that runs as its own daemon process and reports back to the conversation
on completion). See [background-tasks.md](background-tasks.md).

## Process model — why children are threads (decision record, 2026-07)

We evaluated moving `spawn`/`subtask` children onto the daemon-subprocess pattern the detached
machinery uses, with the goal of deleting the resume-orphan handling (the aborted-on-resume
notes and the synthesized dangling-subtask observation). **Decision: children stay in-process
threads.** The two models are complementary by design, not rivals:

|                | in-process child (`spawn`/`subtask`)         | detached task (`detach`)                    |
|----------------|----------------------------------------------|---------------------------------------------|
| start latency  | milliseconds (a thread)                      | seconds (intent file → daemon tick → boot)  |
| budget         | sliced live from the parent's remainder; usage folds back at exit | its own background budget; never folds back |
| lifetime       | dies with the parent — an invariant, not a bug | must outlive the reply — its whole purpose |
| conversation   | parent yields to a user message instantly (in-memory events) | reports back asynchronously via the inbox |

Why the subprocess trade-off fails for within-run children:

- **Latency.** A subprocess child either routes through the daemon (intent file + scheduler tick
  + interpreter boot: seconds per child, against milliseconds today, over a recursive tree with
  up to 4 parallel children per node — subtasks are usually a handful of turns, so the overhead
  would often exceed the work) or the engine spawns processes itself, creating a second
  process-owner beside the daemon — a deeper invariant break than the one being fixed.
- **Budget folding.** Budgets are one live in-memory primitive: `child_budgets()` slices half
  the parent's *remainder* at spawn, and the child's usage folds back into the parent's meter
  and per-turn `status.json` (single writer) at exit. Across processes, both directions become
  disk-polling protocols, and the single-writer status contract needs a merge layer.
- **Responsiveness is a feature, not an orphan workaround.** The wait-loop's inbox yield keeps
  the conversation live while a child runs. It survives any process model — with subprocesses
  the wake signals just get slower (disk polling; the event bus is lossy by design).
- **The orphan code is smaller than its replacement.** Aborted-on-resume plus the
  dangling-subtask note are ~60 lines with tests. Subprocess children would need intake,
  reattach-on-resume, cross-process kill, usage reconciliation, registry/retention visibility,
  and a reaper for children whose parent died without a finish — threads make "children never
  outlive the parent" free; subprocesses violate it on every parent crash. That is the detached
  manager's ~370 lines again, justified there by the one thing threads cannot do: outlive the
  process.

The escape hatch already exists: work that must survive a restart or a reply-finish is
`detach`, not a subtask. Revisit only if cross-restart subtask survival becomes a concrete
need — and then by widening `detach`, not by migrating `spawn`/`subtask`.
