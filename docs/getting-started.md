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
| **Instruction** | *(consumed at creation)* | The TASK — goal, deliverable, constraints, done-criteria — you describe once. It seeds the workflow below and isn't kept as a separate file; no schedule or conduct rules live here. |
| **Workflow** | `main.md` + `stages/*.md` | The control flow the agent follows, generated from a library *pattern* applied to your instruction — and the routine's sole source of truth once created. `main.md` is a small state machine; each stage's detail lives in a module read on demand. |
| **Rules** | `rules:` in `routine.yaml` | The general rules that bind it (when to ask you, research discipline, what to record, git-checkpointing a project repo). Each states a principle and the run applies it to its own case. The prose lives ONCE in the shared library, so revising it there reaches every routine holding it; the routine holds slugs, not copies. |
| **Permissions & capabilities** | `routine.yaml` | What the routine is ALLOWED to do — writing utils, Discord, memory, reading previous runs, shell. *Capabilities* are the engine-enforced switches; *permissions* are the conduct docs that ride along (the *Rules, permissions & capabilities* guide has the full model). Only you change either — a run can never grant itself anything, nor edit its own recipe. |
| **Budgets** | `routine.yaml` | Hard per-run ceilings: turns, minutes, tokens (unlimited by default — turns and wall-clock are the effective bound), sub-workflows and their depth, and how long a blocking question waits for you. |
| **State & memory** | `state/`, `LEDGER.md`, `.memory/` | What carries between runs: working files, the append-only change journal, and the notebook of hard-won surprises. |
| **Runs** | `runs/<ts>/` | Every run's full transcript, status, and result — the conversation is the audit trail. |

Two design rules explain most of the system's shape:

- **The workflow is the harness.** There is no hidden agent framework: the model reads the
  workflow and acts through exactly ONE JSON action per turn (run a util, read/write a
  file, ask you, spawn a sub-workflow, finish). Every action and its result is in the
  transcript you can read.
- **Routines run code through utils.** The way a routine runs code is a **global util** — a
  named, selftested script shared by all routines (`websearch`, `gmail`, `page-fetch`, …).
  Routines can write new utils (permission-gated), and every util they build is available
  to every other routine. (There IS a `shell` action too — the escape hatch, off unless you
  grant it, for the rare routine that genuinely needs an ad-hoc command. It runs in the same
  sandbox a util does and gets no secret at all; anything a routine runs twice should become
  a util.)

## The pieces around routines

- **Endpoints** (Settings) are model *transports*, and there are two kinds: OpenAI-compatible
  APIs (OpenRouter, vLLM, Ollama) and the Anthropic Messages API — which is also how a Claude or
  Codex SUBSCRIPTION is billed, through CLIProxyAPI on the `anthropic` kind (see *The Claude
  proxy cutover*). A
  **model** is a named catalog entry bound to an endpoint, carrying its own context window,
  vision support, effort, temperature, output limit (`max_tokens` — Settings flags models
  where it's unset or implausible), and an optional ordered **fallback chain** — other
  catalog models the engine fails over to when this one's provider fails hard (transitively:
  the named list first, then each entry's own fallbacks), with a
  cooldown so a flapping provider isn't hammered. Each routine picks its models by name —
  `main` (the loop, and every child run by default), `tool_call` (the `llm` action and the
  engine's own subcalls) and an optional `uncensored` refusal-clarification harness — or falls
  back to the one **system model**. A model can be
  **multimodal**: it views images and PDFs natively
  (default on for Anthropic/Claude models, a per-model toggle for OpenAI-compatible vision
  models), otherwise through the `vision` util.
- **The library** (Library tab) is one git repo holding the shared building blocks:
  workflow **patterns**, **rules**, **permissions**, **utils**, **playbooks** (reusable
  one-shot briefs for Conversations), **settings templates**, the global **reminders** store,
  and the shared **web kit** status pages are built on. Routines are built FROM it, and a rule
  or util revised there reaches every holder at its next run.
- **Decisions** (Decisions tab) is the one inbox for everything routines need from you:
  blocking questions (a run is waiting), deferred ones (the next run picks the answer up),
  util approvals, and self-audit decisions. A blocking question waits up to the routine's
  configured timeout, then the run **continues on the default the model stated** — the
  question stays open for a future run. The console is the only decision surface — no
  channel mirrors a question anywhere else. **Browser notifications** are opt-in under
  Settings → Notifications:
  OS notifications while a console tab is open, and — per browser — Web Push that reaches
  you with the console closed (needs HTTPS or a localhost tunnel).

## Step by step: from zero to a running routine

**1 · Connect a model.** Settings → LLM endpoints → add one (an OpenRouter key is the
fastest start; the Claude subscription needs no per-token billing). Set it as the system
model. Add any secrets your future utils need (Settings → Secrets — the shared write-only store; a credential meaningful to ONE routine belongs in that routine's own *Own secrets* section instead, where it needs no exposure grant; at
run time a util receives ONLY the secrets its docstring declares — see the sandboxing guide).

**2 · Describe the task.** Open a **conversation** (the Conversations tab is the landing
page) and write the TASK in your own words — what to produce or tend, what "done" looks like.
Not when it runs, not how to behave; those come later and live elsewhere. There is no create
page and no wizard: a routine is designed in a conversation, because a scheduled run has
nobody to design with. Example opening message:

> Watch arxiv for new papers on LLM agent evaluation. Keep `state/reading-list.md`
> fresh: newest first, one-line take each, link. Flag anything that looks like a
> must-read for me.

**3 · Answer the numbered questions.** The agent sharpens the draft into a precise
instruction and marries it to a workflow pattern from the library, asking only what it cannot
infer — scope, deliverable shape, hard constraints. Each open point arrives as its own
question carrying OPTIONS, which the console renders as numbered picks, so you answer with a
number rather than composing a paragraph. Three things must be SETTLED before it drafts: what
the routine PRODUCES each run, what DONE looks like for ONE run, and which pattern it is built
on. The conversation is resumed in place, so you can leave and come back; its questions also
appear on the Decisions page.

**4 · Confirm the draft in the chat.** There is no create page: the conversation shows you a
DRAFT — slug, name, workflow pattern (or `generate` when nothing in the catalog fits), the
instruction it compiled, and what DONE looks like for one run in your own words — and creates
nothing until you answer. Every point still open comes back as its own numbered question.

**5 · Create.** On your confirmation the agent emits `create_routine` and the system decomposes
the pattern against your instruction into the routine's own `main.md` + `stages/`, records the
pattern's rules and the default permissions in `routine.yaml`, seeds the stopping conditions
from your own words, writes the config, and git-inits the directory; the daemon's registry
rescan picks the new dir up shortly after. Setup is tuned AFTERWARDS on the
routine page, where the *Recommend* button reads the finished recipe and puts advice beside every
rule and permission toggle — you flip the switches. The instruction was only the compile seed — from here
on the stage modules are the routine's recipe, edited directly. Optionally the first run
fires immediately.

**6 · Watch a run.** The dashboard card pulses while it runs; *watch live* streams the
conversation — every model action, every observation, in order. You can pause, abort,
inject a message mid-run, or switch the model mid-flight.

**7 · Decide.** When the routine needs you, the Decisions tab (and the dashboard badge)
shows it. Blocking questions pause their run and show when the run will continue without
you; deferred ones feed the next run. Everything is answerable inline.

**8 · Tune.** On the routine's page: schedule, permissions, budgets, models, the **Recipe**
file-tree (its `main.md` and every stage file), the LEDGER, all runs with their
cost/turns/tokens/duration. The overview sorts and filters on those run stats — card grid or
detail table, and both carry a **heartbeat strip**: the last ~15 runs as colored bars
(green ok · amber partial · red failed · grey aborted, bar height tracking token spend) —
hover for a run's stats, click to open it. A routine that failed four of its last ten runs
no longer looks identical to one that's been green for a month.

## What routines are good at

- **Radars** — scan sources on a schedule, rank against your profile, surface a shortlist
  (jobs, grants, papers, tenders). See *Freelance radar* and *Grants radar* in Examples.
- **Pipelines with a human gate** — the routine researches, drafts, and prepares
  everything up to the irreversible step (send / submit / publish), which waits for your
  one-word go on the Decisions page. See *Grants radar*'s application pipeline.
- **Standing projects** — a long-running goal advanced as far as each run can take it, with
  state, worklog, and self-improvement between runs. A project with a real END (a submission, a
  migration, an event) declares it as a GOAL-scoped stopping condition and RETIRES itself once
  it is met. See *Project steward* in Examples.
- **Event planning / iterative convergence** — propose, collect your feedback, learn,
  propose better. See *Birthday planner* in Examples.

The common thread: **the files are the memory.** A run reads the state its predecessors
left, does as much of the job as it can finish and verify, records what changed and why
(LEDGER), notes what surprised it (`.memory/`), and finishes with a summary the next run starts
from. No chat history, no session to lose.

How much is "as much"? The turn budget is a runaway backstop, never a ration: a run that leaves
work it could have finished has paced itself against a counter instead of against the job. Where
work really is serialized — one submission per round, one training job on a shared GPU — the
recipe says so and names the constraint. Where it is not, the run keeps going to a clean
boundary.

## Where to go next

- **Examples** — four complete routine setups, from draft instruction to daily operation.
- **Conversations** — the interactive counterpart to routines: work with an agent turn by turn.
- **Rules, permissions & capabilities** — how conduct and capability are split, and why.
- **Notifications** — the one way agents reach you, and which channels you switch on.
- **Playbooks** — save a conversation as a reusable one-shot brief, and reuse it to seed new ones.
- **Prompt anatomy** — exactly what the orchestrator model sees, message by message.
- **Endpoints** — configuring the model transports.
- **API reference** — the generated reference for the `rsched` package itself.
