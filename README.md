# routine-scheduler

Self-hosted scheduler for LLM agent **routines** with a web UI to manage them and to watch
or intervene in running conversations.

A **routine** = one **instruction** (a user prompt refined through clarifying questions in
the wizard) + one **workflow** (a natural-language control-flow pattern from a git-synced
library) + a schedule. Each routine lives in its own git repository under `~/routines/<slug>`,
defined by markdown files, with self-audit / self-improvement / self-healing standards on by
default (each toggleable in `routine.yaml`).

**The workflow is the harness.** Runs execute on a provider-agnostic engine: the
orchestrator LLM follows the workflow document and acts only by returning one JSON action
per turn. Routines have **no shell** — the only way to run code is a global util (the `util`
action); if none fits, the routine writes one (`write_util`, selftest-gated, optionally
requiring your approval). Other actions: read/write a file, a scoped `llm` subcall, `spawn`
parallel sub-workflows from the library (monitor with `subruns`, `kill`, `wait`; exits are
announced automatically and children never outlive the parent), ask the user (blocking or
deferred), or finish. The engine commits each routine's working dir automatically. Endpoints are
model **transports** only: OpenRouter, local Ollama, other OpenAI-compatible servers, the
Anthropic Messages API — or the Claude Code CLI in fully stripped print mode (`--tools ""`,
no settings/MCP/session, our system prompt replacing its own) as a subscription-billed
completion function. What is banned is a second *agent loop* in the path: this scheduler is
the only harness.

A **meta routine** (`~/routines/meta-workflows`) periodically ingests the top-level
transcripts and LEDGERs of all routines, fixes workflow defects directly (lint-gated,
version-bumped, committed), files bigger changes as proposals you approve in the UI, and
drafts new workflows for recurring instruction shapes.

## Install

```bash
./deploy/install.sh   # uv sync, config + token, workflow library seed, systemd user service, linger
```

Web UI: `http://127.0.0.1:8321` — token in `~/.config/routine-scheduler/config.yaml`
(set `bind: 0.0.0.0` there for LAN access). API keys live in `~/.credentials/*.env`
(`OPENROUTER_KEY` in `open_router.env`, `ANTHROPIC_KEY` in `anthropic.env`); Ollama needs none.

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
**abort**, and answer **blocking or deferred questions** in the Questions inbox. Answers to
deferred questions reach the routine's next run automatically.

## CLI

`uv run rsched --help` — `daemon` (what systemd runs: scheduler + web in one process),
`run-once`, `engine-run` (internal), `validate`, `lint`, `suggest`, `scaffold`, `abort`.

## Layout

- `src/rsched/` — `engine/` (the run loop), `endpoints/` (direct API adapters),
  `daemon/` (cron scheduler + subprocess runner), `web/` (FastAPI + SSE),
  `workflows/` (library, lint, adapt, scaffold, suggest, generate)
- `static/` — no-build vanilla-JS frontend
- `library-seed/` — seeded to `~/.local/share/workflow-library` (its own git repo with a
  best-effort auto-push hook; add an `origin` remote to enable backup)
- Routine dirs: `routine.yaml`, `instruction.md`, `main.md` (the workflow, materialized with
  provenance) + `steps/` modules, `fragments/`, `state/`, `LEDGER.md`, `inbox/`, `questions/`,
  `runs/<ts>/` (transcripts, gitignored, keep-last-N with gzip)

See `CLAUDE.md` for working conventions and the transcript/action contracts.
