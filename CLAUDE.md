# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through
one JSON action per turn. Endpoints are DIRECT model APIs only (OpenAI-compatible, Anthropic
Messages); wrapped agent runtimes such as headless Claude Code are banned from the execution
path — a second harness both fights this one (fabricated finishes) and hides the conversation.

## Commands

- `uv sync` — install/refresh the venv
- `uv run pytest -q` — full test suite (fast, no network). Live endpoint smoke tests only with `RSCHED_LIVE_TESTS=1`.
- `uv run rsched run-once <slug>` — execute one routine run from the CLI (no daemon)
- `uv run rsched daemon` — scheduler + web UI (systemd runs this in production)
- `uv run rsched validate|lint|suggest|scaffold|abort` — see `rsched --help`

## Layout

- `src/rsched/` — the package. `endpoints/` (chat-completion adapters), `engine/` (the run loop),
  `daemon/` (cron scheduler + subprocess runner), `web/` (FastAPI + SSE), `workflows/` (library,
  lint, adapt, scaffold, suggest).
- `static/` — no-build vanilla-JS ES modules (no bundler, no node). Keep it that way.
- `library-seed/` — workflow library seed, copied to `~/.local/share/workflow-library` by install.
- `tests/` — pytest; `ScriptedEndpoint` in conftest replays canned actions and is the main engine harness.

## Standards

- One responsibility per file, ≤ ~350 lines. Split rather than grow.
- The action schema in `engine/actions.py` is the single source of truth; UI and adapters key off it.
- Cross-process files are written atomic (tmp+rename) via `paths.atomic_write` — never ad-hoc.
- Ownership: the engine subprocess owns `runs/<ts>/*` and git commits in its routine dir; the daemon
  only writes `inbox/`; the web layer edits routine config only when no run is active (409 otherwise).
- Transcript JSONL event types are a contract consumed by the web renderer AND the meta routine —
  extend, never repurpose.
- No database. State derives from the filesystem (gu-style derived catalogs); indexes are in-memory.
- Tests accompany every module in the same commit. Endpoint adapters are mock-tested; anything
  touching the network hides behind `RSCHED_LIVE_TESTS=1`.
