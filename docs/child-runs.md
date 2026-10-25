# Child runs — decomposing a run's work

A run structures its work two ways, and they are deliberately different:

- **Phases** — the routine's own stage modules (`stages/*.md`); the engine tracks which
  one the run is in from its stage-module reads (shown in the phase-graph rail). Coarse.
- **Child runs** — separate runs the parent starts to carry a piece of the work.

## One concept, three scheduling modes

**A child run is: an isolated run with its own directory, its own budget, its own recipe or
pattern, and a declared relationship to its parent.** That is the whole concept, defined once in
`engine/child.py`. `spawn`, `subtask`, (F325) a conversation `branch` and the escalation ladder's
`oversight` rung are four scheduling **modes** of it:

| mode | action | when it runs | who drives it |
|------|--------|--------------|---------------|
| `parallel` | `spawn` | concurrently — the parent keeps working | the engine |
| `sequential` | `subtask` | the parent folds its result into the next step | the engine |
| `branch` | a conversation fork (F325) | forked from a message, alongside the original | the user |
| `oversight` | no action — the ladder fires it (`engine/ladder.py`), or a person asks for one | at a turn boundary, while the parent waits | the engine, or the operator |

`oversight` is the one mode no action starts: a rung is dispatched BY the engine to supervise the
run that started it, and its finish summary comes back as a directive binding on that run. So it
inverts the usual direction — every other child works FOR its parent, a rung reports ON it. The
tree draws it `↑` for that reason, and its directive reaches the worker on the `oversight` inbox
channel, recorded as a `user_injection` carrying `via: "oversight"` so a reader can tell a rung
from the operator.

A PERSON can also ask a live run for a rung — `POST /api/runs/{run_id}/supervise`, the run view's
`👁 supervise` button (F750). It is a request on the RUN, not a second run of the routine: the
daemon refuses two concurrent runs of one routine (`Runner.fire_blocker`) because they would race
its state, which is why pressing run-now on a busy routine answered "routine fau is busy" and why
supervising a run in flight has to happen inside it. The ask is recorded in the run's
`control.json`, read at the next turn boundary by `engine.switches.person_asked`, and it does
**not** require `ladder.enabled` — the knob is off by default, so requiring it would shut the door
on exactly the run being watched. The ask is the authorization, one ask is one rung, and the rung
reports `person_requested` as its reason so a reader can tell it from one the ladder produced.

They are modes, **not** three concepts and **not** a fourth action kind. The action names stay,
because each names a different scheduling intent a run genuinely chooses between; what they share
is the contract underneath. This unification is F338, and it exists because three names had
invited three mental models: the prompt copy drifted between them until it claimed something
false — that children share the parent's working directory (R409/R410) — which cost a run a
recovery detour. `engine/child.py` owns the mode vocabulary the prompt renders (`mode_noun`)
and the WHOLE hand-back — the landing directory (`handback_dirname`), the copy
(`collect_handback`) and the wording (`handback_text`) — so the kind copy, the observations and
these docs cannot drift apart again.

**The contract, obeyed by every mode:**

1. **Isolation.** A child gets its OWN directory and never writes into its parent's, so
   concurrent siblings cannot race a shared tree and the engine arbitrates nothing.
2. **A budget of its own**, sliced from the parent's remainder as the child starts — no single
   child can outspend the run that started it. Parallel siblings are each sliced from the
   remainder they see and are NOT netted against each other (a child's usage folds into the
   parent when it exits), so a fan-out can together spend more than the parent had left — kept
   that way on purpose (operator, 2026-10-01); the run-level budget still ends the tree once
   their usage has folded back.
3. **A declared hand-back.** Summary always; FILES by writing them into its own `artifacts/`
   (below).

The two in-engine modes are real routines on disk under `runs/<ts>/sub/<n>/` while they run, with
their own context window, pattern and finish summary. Decomposition is **recursive**: a child can
decompose again (bounded by `max_subrun_depth`).

A child's number `n` NAMES that directory, so it is unique across every leg of the run — read
off the child directories already there (`childrun.claim_child`) — while the `max_subruns`
allowance COUNTS the children of the current leg and starts afresh with each one. The two were
one counter until a conversation's second reply numbered its first child `#1` again: it ran
inside the first reply's child directory, appended to its transcript, and handed that child's
artifacts back as its own. Admission is one step under the tree's lock (`subruns._admit`) — the
allowance check and the claim together — so two parallel siblings can never both take the last
child.

A child's exit is announced under ONE headline whatever its mode —
`CHILD RUN FINISHED (<mode noun>) — #<n> …` — with the mode named inside it rather than changing
the noun. Only the follow-on instruction differs, because only that genuinely differs: a
sequential child's result feeds the next one.

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
it is a child run tagged `sequential` with a `turns` budget — the same child `spawn` starts. To keep sequential order you **wait**
for it — `wait n=N` — before starting the next subtask, and fold its result into that brief. The
wait is **responsive**: it wakes on the child's completion (the finished-hook) but *yields* the
moment a user message arrives, handing control back so the parent can reply, then you `wait` again.
Its completion is delivered by the turn-boundary `CHILD RUN FINISHED` hook
(`announce_finished_subruns`) whether you are waiting or doing other work. Unlike a plain workflow
step, a child run gets its own fresh context window and pattern; unlike the `parallel` mode
(disjoint outputs) the `sequential` one is meant to run one-at-a-time with results forwarded.

## What a child can reach

A child's OWN dir is `runs/<ts>/sub/<n>/` — its materialized `main.md`, its instruction, its
scratch files — but its allowed fs roots **extend** the parent's rather than replace them
(`childrun._sub_routine`): the parent routine dir and every configured or one-time-granted
read/write root stay reachable, and the extension chains for grandchildren. A subtask briefed on
the parent's `state/` files therefore reads and writes them directly — before this (F185, R13:
ards sub/1, 2026-07-24) moving the child's dir to `sub/<n>` silently dropped the routine dir
from the allowed roots and stranded exactly such children. Children inherit the parent's
RESOURCES only (fs roots, secrets, connections, machines — one-time grants included), never its
capability-class grants: permissions and capabilities are off at depth > 0. Keep parallel
children's outputs disjoint by convention — they share the tree, and the harness contract
already forbids them `LEDGER.md` / `state/phase.json`.

Reaching the tree is not owning it. Every file-action seal holds on each routine dir a child
can reach — its workspace, each ancestor child's, and the routine's — not on its workspace
alone (`reach.routine_dirs`): the routine's recipe, its `.memory/`, its finish line, its
`.util_outputs/`, its `.doc_cache/` and its `runs/` stay closed to a child exactly as they are to the routine's
own runs. The one opening is the child's own workspace tree under `runs/<ts>/sub/<n>/`,
where it works. Before this the seals were anchored on the workspace, so a sub-workflow
could rewrite the routine's recipe and memory index, write the run's `control.json` (whose
`config_change` the parent adopts live — grants, budgets) and read earlier runs it has no
history for.

**Running the routine's scripts.** A `script` action in a child looks in the child's own
workspace first (a script it wrote for itself), then in the ROUTINE's `scripts/`
(`reach.routine_root`), and runs from the home it was found in — the routine's dir, its venv. A
child holds no capability, so no `shell` either; before this its only reachable scripts were the
ones in its empty workspace, so a child briefed to build and TEST one item of a parallel batch
could edit files and run nothing — not even its routine's own test gate. It is reach, not
authority: the script runs in the child's own jail, whose roots already extend to the
routine's dir, with the secrets the child already inherits.

**Handing a FILE back** (the contract's third part; from R409/R410). Isolation is deliberate — a
shared writable dir between concurrent children is a race the engine would have to arbitrate —
but it left the parent to know the child's path, search it and copy files out by hand, a
procedure every routine reinvented and one the spawn contract used to describe *wrongly* (it
claimed children share the parent's working directory; they never have). A child now hands a
file back by WRITING it into its own `artifacts/` — the same deliverable convention the
Artifacts panel lists and a detached background task already uses. On the child's exit the
engine copies that into the PARENT's `artifacts/from-sub-<n>/` and the finished-notification
names the landed paths, so nothing greps the runs tree. A child that writes nothing hands back
only its summary: the hand-back is opt-in by writing, needing no new action field, so a run with
no children pays nothing for it.

**Three landing places, one implementation.** `from-sub-<n>` is the in-engine modes';
`from-branch-<slug>` is a branch's (`branches.hand_back`) and `from-bg-<taskid>` a detached
task's delivery (`daemon/detached_delivery`). A detached task is not a MODE — its budget never
folds back (§ Process model) — so the three nouns are their own table in `engine/child.py`
rather than keys of the mode vocabulary; what they share is the copy, the namespacing and the
wording. Every one of them tells the parent the landed PATHS: the branch and the background
delivery used to report a count, so a parent that was handed three files had to list a
directory to find out what they were.

**Speaking mid-flight, in both directions** (D152 step 3, F695 as a case of hooks —
`engine/runtree.py`). The hand-back above is a child's word at its EXIT; until 0.434.2 it was
its only one, so a child that learned at turn 5 that its brief named a file which does not
exist had to spend its whole budget before saying so, and a parent's `prompt` at spawn time was
its only word down. Both are now the ordinary `report` action with a RUN-TREE target:

- a child writes `target: "parent"`, and the parent reads it at its next turn boundary;
- a parent writes `target: "child:<label>"` — the label it passed to `spawn`/`subtask`.

A SIBLING is not addressable, and that refusal is the design's first rule: *the run tree is the
only addressing scheme*. A child says `parent`, and the parent — the only run that holds the
labels — re-addresses downward. So no run can reach a run it does not own, and nothing has to
resolve a name outside its own subtree.

Three properties follow from delivery riding a boundary the addressee was going to take anyway
(`control.drain_injections`, whose `ctx.depth > 0` early return this lifted): nothing blocks or
polls, a messaged child stays killable, and being reachable buys it no extra turn. A hook to a
child that has already exited is REFUSED rather than filed, because that run will take no
further boundary. The freight arrives on the `hook` channel (`engine/channels.VIAS`), labelled
`HOOK (injected mid-run)` and never as the operator — a run that cannot tell a machine from the
person answers the wrong party. It gets no `R<n>`: an item is owed an answer by a ROUTINE, and
both of these runs are gone in minutes, so the row would sit open forever. What it does get is
a row in `.control/hooks.jsonl` like every other hook, with `addressee` = `parent` or
`child:<label>`.

**Asking the user.** A child never blocks a run on a person (`interact.handle_ask` forces
`mode: deferred` at depth > 0 — a child has no speaking turn with the user), so its question
becomes a durable DECISION RECORD like any other. That record is filed into the ROUTINE's own
`questions/pending/`, never the child's dir: `runs/<ts>/sub/<n>/questions/pending/` is a path no
surface scans, and four live records sat there unseen while the child was being told "the user
will see it in the UI". Two consequences of filing it where a person reads it:

- the question text is prefixed `[child task #<n> '<label>']`, because the Decisions page shows
  the ROUTINE and a child has no page of its own — without the prefix the reader cannot tell
  which piece of work is blocked;
- the id is allocated against the ROOT pending dir. A child shares its parent's `run_ts` and the
  id is `q-<run_ts>-<turn>`, so allocating against the child's own (empty) dir handed a parent
  and a child asking on the same turn the same id, and `file_question` is an unconditional write.

The answer reaches the routine's next run, like every deferred answer; the child itself continues
on its `default` and says so in its summary.

Collection happens in `subruns._collect`, the child's single finalization point — **not** in
either reporter. Two paths report an exit (`wait`, which consumes finished children directly, and
the turn-boundary announcement), so collecting in a reporter meant a child that finished during a
wait handed nothing back. Anything that must happen once per child belongs at that one point.
Both reporters name the landed paths in the ONE spelling `child.handback_paths_line` owns, and
the `subrun_end` event records them (`collected`), so the announcement a resumed leg rebuilds
from the event can name them too.

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

## Pattern sourcing

Each child is matched to a workflow pattern from the catalog — you name any library pattern in
`workflow` (the CAPABILITIES section lists them), or omit it for the default. A child never
drafts a new pattern: a pattern is drafted only when a person picks `generate` while creating a
routine, where somebody chose it.

## Budgets

Every budget in the system — the whole run, a conversation reply window, a subtask, a subrun —
is the same primitive: a **stop condition** (a limit, and whether tripping it is hard) over a
**resource** (turns, tokens, wall-clock, cost), with a warning at 85% and again at 95% — each
said ONCE, because a warning repeated on every turn above the line reads as a countdown and
makes a run wrap up at the ceiling whatever its recipe's Done when says. A subtask gets its own
ledger, sliced from the parent's remainder (or pinned by `turns`). Enforcement is **soft at the
parent**: a subtask that overruns its own turn cap force-finishes `partial` (like a subrun), its
85% warning fires inside the child so its model wraps up first, and the parent gets the partial
summary and re-plans — only the run-level budget hard-stops the whole tree.

## Visualization

The run and conversation rails carry a **sub-run tree** card (below the phase graph): the recursive
tree of sequential subtasks (→), parallel subruns (⇉) and oversight rungs (↑), each a node with a state icon, its
workflow pattern, and a per-node turn-budget meter (amber ≥85%, red over), children nested. It is
a read-model over the on-disk `sub/` transcripts — live while the run runs. A row whose
`start_turn` is known is a LINK to the parent turn that started that child (D193,
`components/turnlink.js` — the same mechanism a phase node uses), so the tree answers "where
did this come from" and not only "what ran". In the transcript,
each child unfolds in place; `run-once` prints the same tree as `↳ subtask …` / `↰ subtask …`
lines. The rails' **files** card folds children in too: a path a subtask read or wrote counts
into the parent run's per-file rows, marked `↳`.

## Resume

Completed subtasks are already in the parent transcript (as the wait / finished-hook messages),
so a resume replays them untouched. Children are **threads** in the parent process, so a run
interrupted with children still running finds them dead on resume — the engine marks each aborted
in the transcript and adds an ENGINE NOTE listing them, so the model re-issues what it needs
instead of `wait`-ing forever for a child that can never finish.

The **registry** (`SubrunManager.subruns`) is in-memory too, so a resumed leg starts with an empty
one while every `sub/<n>/` directory the earlier leg made is still on disk — one of them possibly
holding a finished hand-back in `artifacts/`. `subruns` therefore reports those directories as
rows in state `orphaned-by-resume`, each naming its path and what landed in its `artifacts/`
(`engine/subsalvage.py`), and `wait`/`kill` on such a number refuse with that reason instead of
"no sub-workflow N" — which read as "it never existed" and sent a run off to redo finished work.
This is **salvage, not recovery**: the thread died with its leg, so nothing will be announced or
collected, and the directory is the deliverable. Because children die with the
process, a subtask does **not** survive across conversation reply-finishes — a job that must
outlive a reply (a long, fire-and-forget scrape) is a different capability, the **`detach`** action
(a detached background task that runs as its own daemon process and reports back to the conversation
on completion). See [background-tasks.md](background-tasks.md).

## Aborting or pausing a child

A parent controls its running children with two actions and one invariant:

- **Abort** — `kill n=N` terminates child run *N* immediately (its partial state is marked in
  the transcript). A util or script call the child has in flight ends with it: the jailed
  runner asks the child's abort check — its own kill event as well as the run-wide abort flag —
  while the command runs (`utils_run.run_jailed`). This is how a parent stops a child run it no
  longer needs; there is nothing else to add for "abort".
- **Gather** — `wait n=N` / `wait all=true` blocks until a child (or all) finishes; a finished
  child is also announced at the next turn boundary whether or not you are waiting.
- **Reap on finish** — a parent's `finish` kills every child still running, telling them all at
  once and giving them ONE shared `subruns.KILL_JOIN_S` (12 s) to stop, however many there are.
  Children never outlive the parent (the in-process-thread invariant above), so a parent can
  always stop the whole subtree by finishing — and neither does a command a child started: a
  child whose thread is abandoned while it is still ending one leaves the group's SIGKILL to
  `procgroup.terminate`'s backstop. A `kill` whose child is still inside a model call (which no
  abort interrupts) says so — "still winding down" — and the exit is announced when it lands.

There is **no pause/resume** of a running child, by design: a subtask is usually a handful of turns,
so the coordination cost of holding one mid-flight and resuming it later exceeds just letting it run
(or `kill`-ing and re-issuing it with a refined brief). If a genuine need to hold a child mid-flight
ever appears, it is a new engine surface to weigh then — `kill` + re-issue covers the cases seen so
far.

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
