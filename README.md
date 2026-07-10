# routine-scheduler

Self-hosted ops console for **routines** — scheduled LLM agent tasks with a web UI to
manage them, watch every conversation live, and intervene mid-run. The system is built to
**improve itself through use**: every run ends with reflection passes that sharpen the
routine, and the same machinery — pointed at this very codebase — audits and improves the
system as a whole.

A **routine** = one **instruction** (a user prompt refined through clarifying questions in
the wizard) + one **workflow** (a Python control-flow pattern from a git-synced library,
decomposed into the routine's own markdown) + a schedule. Each routine lives in its own git
repository under `~/routines/<slug>`, with reusable standards (**fragments**) active by
default — the active set is per-routine config in `routine.yaml`.

**The workflow is the harness.** Runs execute on a provider-agnostic engine: the
orchestrator LLM follows the workflow document and acts only by returning one JSON action
per turn. Routines have **no shell** — the only way to run code is a global util (the `util`
action); if none fits, the routine writes one (`write_util`, selftest-gated, optionally
requiring your approval). Other actions: read/write a file, a scoped `llm` subcall, `spawn`
parallel sub-workflows from the library (monitor with `subruns`, `kill`, `wait`; exits are
announced automatically and children never outlive the parent), ask the user (blocking or
deferred), or finish. The engine commits each routine's working dir automatically. Endpoints
are model **transports** only: OpenRouter, local Ollama, other OpenAI-compatible servers,
the Anthropic Messages API — or the Claude Code CLI in fully stripped print mode
(`--tools ""`, no settings/MCP/session, our system prompt replacing its own) as a
subscription-billed completion function. What is banned is a second *agent loop* in the
path: this scheduler is the only harness.

## How the system improves itself

- **Per routine**: the `improve-*` fragments run after every run — five fresh-eyes passes
  (bugfix, research, features, ui, efficiency) that infer the routine's intention from the
  run just completed and improve it along four dimensions: technology, method, function,
  aesthetics — grounding changes in online research. When a pass is unsure, it files a
  deferred question to the **Decisions** page; answers are remembered in the routine's
  LEDGER, so user interaction shrinks over time.
- **As a whole**: three bundled meta routines use the exact same building blocks —
  `self-audit` (audits this codebase, logs, and outputs; applies test-gated fixes and files
  bigger decisions to the **Audit** page), `meta-workflows` (fixes and drafts library
  workflows from all routines' transcripts; big changes become proposals you approve in the
  UI), and `library-sync` (exports the entire instance — routines, workflows, fragments,
  utils, sanitized config — into one GitHub repo and syncs it). They ship **disabled**; the
  dashboard says so until you enable them, because self-improvement costs tokens.
- **Across routines**: workflows and global utils live in one shared library repo, so what
  one routine learns transfers to all — and utils compose (`gu` utils may call other utils),
  so capability compounds.

## Install

```bash
./deploy/install.sh   # uv sync, config + token, library seed, systemd user service, linger
```

Web UI: `http://127.0.0.1:8321` — token in `~/.config/routine-scheduler/config.yaml`
(set `bind: 0.0.0.0` there for LAN access). Credentials are managed in **Settings**: model
endpoints (with inline key or env var), the central secrets store every util can read, and
the GitHub device flow for library sync. Ollama needs no key; the Claude Code CLI transport
reads its subscription token from `~/.credentials/claude-code-oauth.env`.

## Creating a routine

Click **+ New routine**: the wizard interrogates your draft (a real engine run of the
`clarify-instruction` workflow), suggests a library workflow (or generates a draft one),
and scaffolds the routine — its own git repo, materialized workflow with the standard
fragments inlined, seeded LEDGER, chosen cron. Or from the shell:

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
automatically. A routine with the `communication` fragment active may additionally ask
blocking questions through Discord (one batched, phone-answerable message per run) — the UI
stays the only channel otherwise.

## CLI

`uv run rsched --help` — `daemon` (what systemd runs: scheduler + web in one process),
`run-once` (`--model kind=endpoint:model` overrides a model role), `engine-run` (internal),
`validate`, `lint`, `suggest`, `scaffold`, `abort`.

## Layout

- `src/rsched/` — `engine/` (the run loop), `endpoints/` (direct API adapters),
  `daemon/` (cron scheduler + subprocess runner), `web/` (FastAPI + SSE),
  `workflows/` (library, lint, adapt, scaffold, suggest, generate)
- `static/` — no-build vanilla-JS frontend
- `library-seed/` + `util-seed/` — seeded to `~/.local/share/routine-scheduler-libraries`,
  ONE git repo holding `workflows/`, `fragments/` and `utils/` (with the `gu` dispatcher at
  the root); `routine-seed/` — the three meta routines, installed disabled
- Routine dirs: `routine.yaml`, `instruction.md`, `main.md` (the workflow, materialized with
  provenance) + `steps/` modules, `fragments/`, `state/`, `LEDGER.md`, `inbox/`, `questions/`,
  `runs/<ts>/` (transcripts, gitignored, keep-last-N with gzip)

See `CLAUDE.md` for working conventions and the transcript/action contracts.
