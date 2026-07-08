# routine-scheduler

Self-hosted scheduler for LLM agent **routines** with a web UI to manage them and to watch or
intervene in running conversations.

A routine is **one instruction** (a user prompt, refined through clarifying questions in a wizard)
plus **one workflow** (a natural-language control-flow pattern from a git-synced library) plus a
schedule. Each routine lives in its own git repository under `~/routines/<slug>`, defined by
markdown files, with self-audit / self-improvement / self-healing standards on by default.

Runs execute on a **provider-agnostic engine**: the workflow document is the harness. The
orchestrator LLM follows it and acts only by returning one JSON action per turn — run a global
util (`gu …`), read/write a file, make a scoped LLM subcall, spawn a sub-instruction, ask the
user, or finish. Any chat-completion endpoint works: local Ollama, OpenAI-compatible servers,
the Anthropic API, or the Claude Code CLI used purely as a subscription-billed completion
endpoint (never as an agentic harness).

A **meta routine** periodically ingests the top-level transcripts of all routines' runs,
identifies flaws and optimization potential, and revises or creates workflows in the library.

## Install

```bash
./deploy/install.sh     # uv sync, config + token, systemd user service, linger, library seed
```

Web UI: http://127.0.0.1:8321 (token printed by the installer; see `~/.config/routine-scheduler/config.yaml`).

## Status

Under construction — see CLAUDE.md for conventions and `rsched --help` for the CLI.
