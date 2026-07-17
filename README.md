# routine-scheduler

Self-hosted ops console for **routines** — scheduled LLM agent tasks with a web UI to
manage them, watch every conversation live, and intervene mid-run. The system is built to
**improve itself through use**: every run ends with reflection passes that sharpen the
routine, and the same machinery — pointed at this very codebase — audits and improves the
system as a whole.

A **routine** = one **instruction** (a user prompt refined through clarifying questions in
the wizard) + one **workflow** (a Python control-flow pattern from a git-synced library,
decomposed into the routine's own markdown) + a schedule. Each routine lives in its own git
repository under `~/routines/<slug>`, with reusable practice **traits** (adapted in at creation)
and user-set **permissions** — the held set is per-routine config in `routine.yaml`.

**The workflow is the harness.** Runs execute on a provider-agnostic engine: the
orchestrator LLM follows the workflow document and acts only by returning one JSON action
per turn. Routines have **no shell** — the only way to run code is a global util (the `util`
action), and every util subprocess runs inside a Landlock sandbox scoped to the run's declared
filesystem roots, declared secrets, and declared network need (`docs/sandboxing.md`); if none
fits, the routine writes one (`write_util`, selftest-gated, optionally
requiring your approval — and never silently recreating a util you deleted). Other actions: read/write a file, a scoped `llm` subcall, decompose the
work into child tasks — `spawn` parallel sub-workflows (monitor with `subruns`, `kill`, `wait`) or
run ordered `subtask` steps sequentially (each a fresh-context child on its own pattern + budget) —
ask the user (blocking or deferred), or finish. Children never outlive the parent; the recursive
task tree is shown live in the run rail. A conversation can also `detach` a LONG job that OUTLIVES a
reply (its own daemon-managed process, reporting back on completion). The engine commits each routine's working dir automatically. Endpoints
are model **transports** only: any OpenAI-compatible API (OpenRouter, Featherless, vLLM,
local Ollama), the Anthropic Messages API — or the Claude Code CLI in fully stripped print
mode (`--tools ""`, no settings/MCP/session, our system prompt replacing its own) as a
subscription-billed completion function. Setup guide with per-provider recipes:
[docs/endpoints.md](docs/endpoints.md). What is banned is a second *agent loop* in the
path: this scheduler is the only harness.

## How the system improves itself

- **Across routines**: the bundled `routine-improver` meta routine sweeps every routine
  that hasn't opted out with `improve: false` — **itself included** — and improves
  each through five lenses (bugfix, research, features, ui, efficiency) plus a fresh-eyes
  de-clutter pass that hunts what accumulated over many revisions. It infers each routine's
  intention from its recent runs, grounds changes in online research, applies the safe
  reversible ones directly (committed per routine), and files a deferred question to the
  **Decisions** page when unsure; answers are remembered in the LEDGERs, so user
  interaction shrinks over time.
- **As a whole**: three more bundled meta routines use the exact same building blocks —
  `self-audit` (audits this codebase, logs, and outputs; reporting is unconditional, acting
  is lens-scoped and test-gated, with bigger decisions on the **Audit** page),
  `workflow-curator` (fixes and drafts library workflows from all routines' transcripts —
  applied directly, lint-gated and committed; you can edit or delete any workflow on the
  Library tab), and `token-lab` (measures token usage and A/B-tests efficiency methods via
  `llm` subcalls only — never integrating — and publishes a report). They ship **disabled**; the
  dashboard says so until you enable them, because self-improvement costs tokens. The
  instance itself syncs to one GitHub repo — routines, workflows, traits, utils, sanitized
  config — via the scheduled **Library sync** job in Settings (a plain daemon job, no LLM).
- **Across routines**: workflows and global utils live in one shared library repo, so what
  one routine learns transfers to all — and utils compose (`gu` utils may call other utils),
  so capability compounds.

## Install

```bash
./deploy/install.sh    # host install: uv sync, config + token, seeds, systemd user service
docker compose up -d   # or containerized (deploy/DOCKER.md): engine-only image, everything
                       # mutable bind-mounted — the instance migrates as a tarball
```

Web UI: `http://127.0.0.1:8321`. A bearer token is generated into
`~/.config/routine-scheduler/config.yaml` on first boot, so a fresh deploy is never an
open API (set `bind: 0.0.0.0` there for LAN access; `RSCHED_BIND` / `RSCHED_PORT` override
in containers). First launch lands on **Settings** until setup is finished: model
endpoints, the central Secrets store, GitHub, the library repo.

## The console

- **Search** — one box in the header (press `/` or `Ctrl-K`) over everything the instance
  ever wrote: run transcripts, finish reports, decisions, ledgers, memory notes, and
  recipes, across routines and conversations. Hits group by routine and deep-link into
  the exact run. SQLite FTS5 under the hood — a pure cache of the flat files on disk.
- **Decisions** — one inbox for everything the system is asking you: blocking questions
  (a run is waiting), deferred ones, and open self-audit decisions. Keyboard-first;
  answers flow back into the asking routine's next turn or next run.
- **Routines** — the catalog: each routine's state, schedule, budgets, models, permissions,
  and run history; drill into any run to watch its conversation live.
- **Library** — browse and edit the shared workflows, traits, permissions, playbooks, and global utils; every
  save is lint/selftest-gated.
- **Settings** — LLM endpoints (with a live test call), the write-only Secrets store
  every util reads, GitHub device flow, library/source remotes, graceful server restart.
- **Log** — a live, filterable activity feed across all routines; expand a row to tail
  that run's transcript inline.
- **Audit** — the self-audit routine's report on the scheduler itself: changelog,
  findings, and decisions, with a feedback loop into its next run. Every `F63`/`D14`
  mention in the report is a link to the card it references.
- **Help** — documentation generated from this very source at every boot: hand-written
  guides (`docs/*.md`, e.g. endpoint setup) plus an API reference rendered from the
  code's docstrings by pdoc.

## Creating a routine

Click **+ New routine**: describe the task, and the clarifier interrogates your draft — a
real run of the protected `clarification` routine, living on the **standard run page**
(`#/run/clarification:<ts>`, resumable from the setup banner). When the chat finishes, the
same page becomes the create form: it suggests a library workflow (or generates a draft
one) and scaffolds the routine — its own git repo, materialized workflow with the standard
traits adapted in, seeded LEDGER, chosen cron. Or from the shell:

```bash
uv run rsched scaffold my-routine --workflow general-task --cron "0 7 * * 1" \
    --instruction-file instruction.md
uv run rsched run-once my-routine          # manual run with live event stream
```

## Intervening

Every run is a transparent conversation: watch it live in the run view, **inject** a
message (picked up at the next turn boundary, or at the next run's boot), **pause/resume**,
**abort**, **switch the model mid-run**, and answer **blocking or deferred questions** on
the Decisions page. Answers to deferred questions reach the routine's next run
automatically. A routine with the `communication` permission held may additionally ask
blocking questions through Discord (one batched, phone-answerable message per run) — the UI
stays the only channel otherwise.

## Event triggers

Besides cron and the run-now button, a routine can fire on an **external event**: add a
**webhook trigger** on its routine page and POST anything to the generated
`/api/hooks/<slug>/<token>` URL (CI finished, a monitor alerted, a form landed). The URL's
server-generated token is the only auth — no console bearer for third parties — and the
payload reaches the run verbatim as an injected message. Bursts coalesce: events arriving
while a run is active (or within the trigger's cooldown) queue into **one** follow-up run
that receives every payload, so a noisy or leaked URL can't burn budget. `imap` and
`watch_path` trigger types are reserved in the same config shape. See `docs/triggers.md`
(also on the Help tab).

## CLI

`uv run rsched --help` — `daemon` (what the service/container runs: scheduler + web in one
process), `run-once` (`--model kind=name` overrides a model role with a catalog model),
`engine-run` (internal), `validate`, `lint`, `suggest`, `scaffold`, `abort`.

## Development

`uv sync`, then `uv run pytest -q` — the suite is fast and offline (`RSCHED_LIVE_TESTS=1`
adds live endpoint smoke tests). Quality gates are strict and enforced:

- `uv run ruff check` — lint with `select = ALL`; every ignore in `pyproject.toml` names
  the deliberate house-style reason. Zero findings on the full repo is the only passing
  state. The seed trees (`library-seed/`, `util-seed/`) are excluded: workflow patterns are
  never-executed ast-parsed depictions and utils are PEP 723 scripts — each has its own gate
  (`workflows/lint.py`; util header checks + `--selftest`).
- `uv run mypy` — type check of `src/rsched`.
- `uv run pre-commit install` once — both gates then run on every commit.
- `uv run pytest --cov` — coverage report (branch coverage on; `fail_under` is a ratchet).
- `tests/ui/` — Playwright browser tests that drive the real console against fixture
  state and a stub runner (no LLM). One-time setup: `uv run playwright install chromium`.

Working conventions, the action/transcript contracts, and the module standards live in
`CLAUDE.md`; the Help tab's API reference regenerates from docstrings at every daemon
boot, so docstrings are user-facing here.

## Layout

- `src/rsched/` — `engine/` (the run loop), `endpoints/` (direct API adapters),
  `daemon/` (cron scheduler + subprocess runner), `web/` (FastAPI + SSE),
  `workflows/` (library, lint, adapt, scaffold, suggest, generate)
- `static/` — no-build vanilla-JS frontend; `docs/` — hand-written guides, rendered into
  the Help tab next to the pdoc-generated API reference (`docs_build.py`, at boot)
- `library-seed/` + `util-seed/` — seeded to `~/.local/share/routine-scheduler-libraries`,
  ONE git repo holding `workflows/`, `traits/`, `permissions/`, `playbooks/` and `utils/` (with the `gu` dispatcher at
  the root); `routine-seed/` — the four meta routines, installed disabled
- Routine dirs: `routine.yaml`, `main.md` (the workflow, materialized) + `stages/` modules
  (the routine's sole source of truth — no persisted instruction, no recompile), `traits/`,
  `state/`, `LEDGER.md`, `inbox/`, `questions/`, `runs/<ts>/` (transcripts, gitignored,
  keep-last-N with gzip)

See `CLAUDE.md` for working conventions and the transcript/action contracts.
