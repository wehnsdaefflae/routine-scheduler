# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through one JSON
action per turn. **A second AGENT LOOP in the path is banned**: it fights this harness and hides the
conversation. Endpoints are model TRANSPORTS only (see Endpoints). Routines have **no shell** — the
only way to run code is a global util. The instruction contains only the task; schedule, active
fragments (standards), workdir, budgets, and model roles are routine config (`routine.yaml` / UI).

## Commands

- `uv sync` — install/refresh the venv
- `uv run pytest -q` — full suite (fast, no network). Single test: `uv run pytest tests/test_loop.py -q`
  or `-k <name>`. Live endpoint smoke tests run only with `RSCHED_LIVE_TESTS=1`.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=endpoint:model` overrides a model role; `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>]`
  — see `rsched --help`. `engine-run` is internal (daemon-spawned).

## How a run works (engine/)

The turn loop (`engine/loop.py`) is the heart; `engine/runtime.py` is the entry above it
(`run_routine`, workflow loading/decomposition), `engine/control.py` the between-turns control plane
(abort, pause gate, `control.json` model switch, injection drain, subrun announcements), and
`engine/interact.py` the user-conversing handlers (`ask_user`, approval-gated `write_util`). Each turn:
check budgets → pause gate → drain injected user messages (`inbox.py`) → announce finished subruns →
get ONE valid action from the model (up to 3 schema-retries) → dispatch → append the observation →
repeat until `finish`.
- **One action per turn** is enforced: the model returns a single JSON object matching `ACTION_SCHEMA`;
  `normalize_action` + `validate_action` (`engine/actions.py`) repair grammar debris from weak/constrained
  models and return precise per-kind errors. `actions.py` is the single source of truth for what a turn
  may do — adapters, UI, and the CLI event renderer all key off it. A workflow's `tools:` allowlist is
  enforced there too: a disallowed kind is corrected inside the schema-retry cycle, never becomes a turn.
- **The system prompt is composed once at boot** (`engine/composer.py`): harness contract → action schema
  + example → workflow body (the routine's own `main.md`) → instruction → active fragments → **state
  digest** (phase, `state/`, step modules, last result, LEDGER tail, open/answered questions, inbox
  messages). Effect actions (`util`/`read_file`/`write_file`/`llm`) run through `engine/executor.py`.
- **Compaction archives context to a navigable on-disk history** (`history.compact_to_history`): when
  the prompt exceeds ~60% of the endpoint's `context_chars`, the middle turns are reorganized by the model
  into a set of markdown files (~≤100 lines each) under `runs/<ts>/history/` + `INDEX.md`; the prompt keeps
  only a pointer, and every later turn is reminded to consult the index (read_file). Falls back to the
  deterministic one-line digest (`history.maybe_compact`) on any failure. The on-disk transcript keeps
  everything regardless.
- **A run resumes where it left off** (`run_routine(resume_from=…)`, `EngineLoop(resume=True)`): the
  transcript is replayed into the message list (`history.replay_messages`) with a fresh budget window
  (`budget_base_turn`). The **model can be switched mid-run** — a `control.json` `switch_model` signal
  applied at the turn boundary (`for_model` re-resolves every turn).

## Core contracts — extend, never repurpose

- **Actions** (`engine/actions.py` — flat schema on purpose; weak models and Ollama grammars handle flat
  far better than `oneOf`): `util, write_util, read_file, write_file, llm, spawn, subruns, kill, wait,
  ask_user, finish`. Every action carries `say` (narration) + `kind`.
- **Transcript events** (`engine/transcript.py` — append-only JSONL, the engine is the only writer):
  `header, assistant_action, observation, question, answer, user_injection, subrun_start, subrun_end,
  compaction, error, finish`. This vocabulary is consumed by the web renderer AND the meta routine.

## Endpoints (endpoints/) — transports, not agents

Chat-completion adapters implementing one `ChatEndpoint.complete(...)` (`base.py` — tenacity retries on
retryable `EndpointError`s; a 200 with an unparseable body is one of them). All three honor
`ModelRef.effort`. Three kinds:
- **openai** — any OpenAI-compatible API (OpenRouter, vLLM, Ollama). Schema via json_schema / json_object
  / ollama-native; degrades gracefully (retries without `response_format`/`reasoning` on a 400).
- **anthropic** — Messages API, METERED per-token billing. Schema via a single forced tool-use; effort via
  `output_config`, degraded on a 400 that names it.
- **claude-cli** — `claude -p` fully stripped (`--tools ""`, no MCP/settings/session, our `--system-prompt`
  replacing its own, `--json-schema`), SUBSCRIPTION-billed via `CLAUDE_CODE_OAUTH_TOKEN`. Metered-auth env
  vars are scrubbed so it can't silently fall back to API billing.

Each **routine sets its own three models** (`routine.yaml` `models:`): `main` (the loop),
`subroutine` (a spawned child's main model), `tool_call` (the `llm` action). A model a routine
leaves unset falls back to the server's single `system_model` — the ONE model for pre-routine
machine work (the clarify wizard + workflow generation/suggestion). There is no server "roles"
concept. `EndpointRegistry.for_model(kind, routine.models)` / `.for_system()` resolve them.

## Routines on disk

A routine dir (`~/routines/<slug>`) owns its recipe — the workflow library is NEVER read at run time:
- `routine.yaml` — `description` (one-line UI summary, always present), schedule (cron + tz + catchup),
  `workflow: {library_slug, library_commit}` (provenance only), `models:` (main / subroutine / tool_call),
  `fragments:` (active standards), `budgets:` (max_turns / wall_clock_min / total_tokens / subruns /
  subrun_depth / ask_timeout_h), `fs_read_roots` / `fs_write_roots`, retention.
- `main.md` — the workflow **decomposed and materialized into this routine** (an entry state-machine that
  routes to `steps/<name>.md` modules, read on demand). `instruction.md` — the task. `fragments/*.md` —
  editable routine-local copies of the active fragments.
- `state/`, `LEDGER.md`, `inbox/` (daemon/web drop messages + answers here), `questions/pending/`,
  `runs/<ts>/` (transcripts, gitignored, keep-last-N with gzip). The engine commits the working dir
  automatically — routines never run git themselves.

## Subruns, questions, injection

- **spawn** materializes a child routine on disk and runs its `EngineLoop` in a **thread** (not a
  subprocess); children get half the parent's remaining budget, a cap of 4 parallel, and are killed at
  parent finish (they never outlive the parent). Monitor via `subruns` / `wait` / `kill`; exits
  auto-announce at the next turn boundary and fold usage into the parent.
- **ask_user** is `blocking` (poll `inbox/answer-<qid>.json` up to `ask_timeout_h`, then degrade to
  deferred) or `deferred` (filed to `questions/pending/`, surfaced in a later run's state digest). The web
  layer posts answers into `inbox/`.

## Libraries & seeds

ONE git-backed library repo (`libraries_home`, default `~/.local/share/routine-scheduler-libraries`),
seedable from the repo and syncable to a remote, holding **workflows/** (control-flow patterns),
**fragments/** (reusable standards inlined per routine), and **utils/** (the ONLY way routines run
code, with the `gu` dispatcher at the root). Repo seeds: `library-seed/` (workflows + fragments),
`util-seed/` (utils), `routine-seed/` (bundled meta routines `self-audit`, `library-sync`,
`meta-workflows` — installed **disabled**; the dashboard shows a notice until enabled). `library-sync`
syncs the WHOLE instance into that one repo: `instance-export` copies each routine's working tree
(minus `runs/`, `.git`, transient inbox/question state) into `routines/<slug>/` and the server config —
token and api_key values redacted — into `config/`, then `git-sync` pushes. `bootstrap.py` seeds on
first boot; `deploy/install.sh` for host installs.
- **Workflows** are self-contained **Python pattern files** (`.py`) that DEPICT a routine's control flow —
  never executed, parsed statically with `ast` (`workflows/pyworkflow.py`). Each has a `META = {...}` dict
  (`slug / name / description / when_to_use / version / status / tags / includes`, optional `tools:`
  allowlist), `PHASES` / `COMPLETION` literals, a top-level `run()` whose body is the per-run control flow,
  one function per step, and dummy parameter imports (`from routine.params import …`) naming the routine's
  parameters by type+meaning. The runtime is
  unchanged — routines are still the markdown `main.md`+`steps/` the orchestrator interprets: `adapt.decompose`
  turns a Python pattern into that markdown at scaffold; `materialize` renders it whole (sub-routines/fallback).
  A `tools:` list restricts action kinds (`finish` always allowed) — how `clarify-instruction` is held to
  ask/read/write/finish. `workflows/lint.py` gates every change; `suggest`/`generate` rank/draft via the
  `system_model`. The **new-routine wizard** runs `clarify-instruction`, which now SUGGESTS a pattern (or
  asks to generate one) and MARRIES the task to it — asking questions that overlay the task on the pattern's
  control flow + parameters (candidates written to the session's `state/candidates.md`).
- **Fragments**: reusable standards inlined per routine. The improvement standards are
  five **after-run passes** — `improve-bugfix / -research / -features / -ui / -efficiency` — each infers the
  routine's intention from the run just completed and acts in its lens (fresh-eyes throughout), asking a
  deferred question (→ Decisions page) when unsure. `ledger-discipline` (cross-run memory) + `ask-policy` /
  `global-utils` / `web-research` / `communication` are the standing standards. `DEFAULT_FRAGMENTS` (config)
  is the source of truth.
- **Utils** are self-contained PEP 723 scripts: a docstring header (`<name> — summary`, `usage:`, `calls:`),
  a `secrets: NAME,…` declaration line, and a `--selftest` the engine runs before saving (`write_util` is
  selftest-gated, and approval-gated when `confirm_util_changes`). Discover with the `util` action `name: list`.
- **Secrets** are one central, write-only KEY→VALUE store injected into every util, endpoint, and the
  subscription at run time; utils declare which vars they need and the UI flags unset ones.

## Ownership, concurrency, restart (daemon/)

- The **engine subprocess** owns `runs/<ts>/*`, `status.json` (atomic, single writer), and git commits in
  its routine dir. The **daemon** only writes `inbox/`. The **web layer** edits routine config only when
  no run is active (409 otherwise).
- The daemon (`scheduler.py` + `runner.py`) fires cron via croniter and spawns one `engine-run` subprocess
  per routine (never two of the same at once) under `max_concurrent_runs`; a run that blocks on a user
  question **releases its slot**. `registry.py` derives the catalog and run-index live from the filesystem
  every rescan — no cache, no database; indexes are in-memory.
- **Self-update restart** (`restart.py`): a sentinel triggers a drain (parked `waiting_user`/`paused` runs
  don't block it), then a clean exit; systemd `Restart=always` relaunches on the committed code (`uv run`
  re-syncs deps). Orphaned runs claiming to be alive are closed out at boot.

## Standards

- One responsibility per file, ≤ ~350 lines. Split rather than grow.
- Prefer a fitting, well-maintained package over hand-rolled plumbing (pydantic validates config,
  tenacity retries, python-frontmatter parses frontmatter, sse-starlette speaks SSE). The bar is net
  reduction AND net clarity — `paths.atomic_write` and `schema_guard` stay bespoke on purpose.
- Cross-process files are written atomic (tmp+rename) via `paths.atomic_write` — never ad-hoc.
- `static/` is no-build vanilla-JS ES modules (no bundler, no node, no external assets). Keep it that way.
- Tests accompany every module in the same commit; `ScriptedEndpoint` in `tests/conftest.py` replays
  canned actions and is the main engine harness. Endpoint adapters are mock-tested; anything touching the
  network hides behind `RSCHED_LIVE_TESTS=1`.

## Deploy

`deploy/install.sh` (idempotent host install: venv, config + token, seeds, systemd user service + linger)
or Docker (`docker compose up -d` — a disposable engine-only image; source, config, `~/.credentials`,
`~/routines`, and the library repo are all bind-mounted, so the whole system migrates as a tarball of
those dirs). Server config: `~/.config/routine-scheduler/config.yaml` (generated with a random token on
first boot by `bootstrap.ensure_config`, so a fresh deploy is never an open API). Web UI on `:8321`,
bearer-token auth; `RSCHED_BIND` / `RSCHED_PORT` override for containers. First launch redirects to
Settings until setup (secrets, endpoints + system model, GitHub device-flow, the library) is finished.
