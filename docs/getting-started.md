# Getting started

rsched runs **routines**: recurring tasks executed by an LLM agent on a schedule, each in
its own directory with its own files, memory, and git history. You describe a task once;
the system turns it into a self-contained agent recipe, runs it on a cron schedule, shows
you everything it does, and asks you only for the decisions that are genuinely yours.

This page is the tour: the concepts, then the step-by-step from an empty install to a
running routine. The **Examples** guide walks four complete, realistic setups; for
interactive, do-it-now work driven by you rather than a schedule, see **Conversations**.

## What a routine is

One routine = one directory under `~/routines/<slug>`, holding:

| piece | file(s) | what it is |
|---|---|---|
| **Instruction** | `instruction.md` | The TASK — goal, deliverable, constraints, done-criteria. Nothing else: no schedule, no conduct rules. |
| **Workflow** | `main.md` + `steps/*.md` | The control flow the agent follows, generated from a library *pattern* applied to your instruction. `main.md` is a small state machine; step detail lives in modules read on demand. |
| **Traits** | `traits/*.md` | Reusable practices (when to ask you, research discipline, LEDGER hygiene, git-checkpointing a project repo), **adapted to the task at creation**. The routine's own files from then on — refined over time by the routine-improver meta routine, never self-edited mid-run. |
| **Permissions** | `routine.yaml` | What the routine is ALLOWED to do — writing utils, Discord, memory, reading previous runs, shell. Engine-enforced on every action; only you change them (a run can never grant itself anything, nor edit its own recipe). |
| **Budgets** | `routine.yaml` | Hard per-run ceilings: turns, minutes, tokens (unlimited by default — turns and wall-clock are the effective bound), sub-workflows and their depth, and how long a blocking question waits for you. |
| **State & memory** | `state/`, `LEDGER.md`, `.memory/` | What carries between runs: working files, the append-only change journal, and the notebook of hard-won surprises. |
| **Runs** | `runs/<ts>/` | Every run's full transcript, status, and result — the conversation is the audit trail. |

Two design rules explain most of the system's shape:

- **The workflow is the harness.** There is no hidden agent framework: the model reads the
  workflow and acts through exactly ONE JSON action per turn (run a util, read/write a
  file, ask you, spawn a sub-workflow, finish). Every action and its result is in the
  transcript you can read.
- **Routines have no shell.** The only way a routine runs code is a **global util** — a
  named, selftested script shared by all routines (`websearch`, `gmail`, `page-fetch`, …).
  Routines can write new utils (permission-gated), and every util they build is available
  to every other routine. (There IS a `shell` util — reserved behind its own permission,
  for the rare routine that genuinely needs the escape hatch.)

## The pieces around routines

- **Endpoints** (Settings) are model *transports*: OpenAI-compatible APIs (OpenRouter,
  vLLM, Ollama), the Anthropic API, or your Claude subscription via the Claude Code CLI. A
  **model** is a named catalog entry bound to an endpoint, carrying its own context window,
  vision support, effort, and temperature. Each routine picks its own three models by name —
  the main loop, spawned sub-workflows, and the `llm` tool-call action — or falls back to the
  one **system model**. A model can be **multimodal**: it views images and PDFs natively
  (default on for Anthropic/Claude models, a per-model toggle for OpenAI-compatible vision
  models), otherwise through the `vision` util.
- **The library** (Library tab) is one git repo holding the shared building blocks:
  workflow **patterns**, **traits**, **permissions**, **utils**, and **playbooks** (reusable
  one-shot briefs for Conversations). Routines are built FROM it but never depend on it at
  run time.
- **Decisions** (Decisions tab) is the one inbox for everything routines need from you:
  blocking questions (a run is waiting), deferred ones (the next run picks the answer up),
  util approvals, and self-audit decisions. A blocking question waits up to the routine's
  configured timeout, then the run **continues on the default the model stated** — the
  question stays open for a future run. Routines with the *communication* permission
  mirror blocking questions to Discord; answer on whichever surface is closer, and the
  other one is told. **Browser notifications** are opt-in under Settings → Notifications:
  OS notifications while a console tab is open, and — per browser — Web Push that reaches
  you with the console closed (needs HTTPS or a localhost tunnel).

## Step by step: from zero to a running routine

**1 · Connect a model.** Settings → LLM endpoints → add one (an OpenRouter key is the
fastest start; the Claude subscription needs no per-token billing). Set it as the system
model. Add any secrets your future utils need (Settings → Secrets — write-only store,
injected into utils at run time).

**2 · Describe the task.** *+ new routine* → write the TASK in your own words — what to
produce or tend, what "done" looks like. Not when it runs, not how to behave; those come
later and live elsewhere. Example draft:

> Watch arxiv for new papers on LLM agent evaluation. Keep `state/reading-list.md`
> fresh: newest first, one-line take each, link. Flag anything that looks like a
> must-read for me.

**3 · Answer the clarifier.** A short chat sharpens the draft into a precise instruction
and marries it to a workflow pattern from the library. It asks only what it cannot infer —
scope, deliverable shape, hard constraints.

**4 · Review the create page.** Everything is preselected from your task, everything is
editable before anything is created:

- the **workflow pattern** (or generate a new one when nothing fits),
- the **traits** — the practice set that will be adapted into the routine,
- the **permissions** — granted conservatively; you can widen them any time later,
- the **budgets** — the per-run ceilings,
- slug, name, tags, and the **schedule** (or manual-only).

**5 · Create.** The system decomposes the pattern against your instruction into the
routine's own `main.md` + `steps/`, adapts each selected trait into `traits/`, writes the
config, and git-inits the directory. Optionally the first run fires immediately.

**6 · Watch a run.** The dashboard card pulses while it runs; *watch live* streams the
conversation — every model action, every observation, in order. You can pause, abort,
inject a message mid-run, or switch the model mid-flight.

**7 · Decide.** When the routine needs you, the Decisions tab (and the dashboard badge)
shows it. Blocking questions pause their run and show when the run will continue without
you; deferred ones feed the next run. Everything is answerable inline.

**8 · Tune.** On the routine's page: schedule, permissions, budgets, models, the
instruction, every step and trait file, the LEDGER, all runs with their cost/turns/tokens/
duration. The overview sorts and filters on those run stats — card grid or detail table.

## What routines are good at

- **Radars** — scan sources on a schedule, rank against your profile, surface a shortlist
  (jobs, grants, papers, tenders). See *Freelance radar* and *Grants radar* in Examples.
- **Pipelines with a human gate** — the routine researches, drafts, and prepares
  everything up to the irreversible step (send / submit / publish), which waits for your
  one-word go on the Decisions page. See *Grants radar*'s application pipeline.
- **Standing projects** — a long-running goal advanced one increment per run, with state,
  worklog, and self-improvement between runs. See *Project steward* in Examples.
- **Event planning / iterative convergence** — propose, collect your feedback, learn,
  propose better. See *Birthday planner* in Examples.

The common thread: **the files are the memory.** A run reads the state its predecessors
left, does one increment, records what changed and why (LEDGER), notes what surprised it
(`.memory/`), and finishes with a summary the next run starts from. No chat history, no
session to lose.

## Where to go next

- **Examples** — four complete routine setups, from draft instruction to daily operation.
- **Conversations** — the interactive counterpart to routines: work with an agent turn by turn.
- **Traits & permissions** — how conduct and capability are split, and why.
- **Playbooks** — save a conversation as a reusable one-shot brief, and reuse it to seed new ones.
- **Prompt anatomy** — exactly what the orchestrator model sees, message by message.
- **Endpoints** — configuring the model transports.
- **API reference** — the generated reference for the `rsched` package itself.
