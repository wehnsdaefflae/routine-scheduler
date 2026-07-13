# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through one JSON
action per turn. **A second AGENT LOOP in the path is banned**: it fights this harness and hides the
conversation. Endpoints are model TRANSPORTS only (see Endpoints). Routines have **no shell** — the
only way to run code is a global util (a reserved `shell` util exists behind the `shell`
permission). The instruction contains only the task; conduct prose lives in the routine's own
`traits/` (adapted in at creation); schedule, PERMISSIONS, workdir, budgets, and model roles are
routine config (`routine.yaml` / UI).

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
`engine/interact.py` the user-conversing handlers (`ask_user`, grant-gated `write_util`). Each turn:
check budgets → pause gate → drain injected user messages (`inbox.py`) → announce finished subruns →
get ONE valid action from the model (up to 3 schema-retries) → dispatch → append the observation →
repeat until `finish`.
- **One action per turn** is enforced: the model returns a single JSON object matching `ACTION_SCHEMA`;
  `normalize_action` + `validate_action` (`engine/actions.py`) repair grammar debris from weak/constrained
  models and return precise per-kind errors. `actions.py` is the single source of truth for what a turn
  may do — adapters, UI, and the CLI event renderer all key off it. A workflow's `tools:` allowlist AND
  the routine's permission **grants** (`grants.py`) are enforced there too: allowed kinds = workflow tools
  ∩ (base ∪ held grants), plus path gates (runs/ needs run-history; a run NEVER writes its own
  main.md / steps/ / traits/ / instruction.md / routine.yaml — not a permission but a fixed rule,
  unlocked only when a user-granted fs_write_root covers the routine dir, the routine-improver's
  case; executor.py backstops absolute paths and scopes `runs: last`).
  A disallowed/ungranted call is corrected inside the schema-retry cycle with an error naming the
  granting permission, and never becomes a turn.
- **The system prompt is composed once at boot** (`engine/composer.py`): harness contract → action schema
  + example → workflow body (the routine's own `main.md`, ending in a `## Standing practices` tail that
  references `traits/*.md` — practice prose is NEVER inlined) → instruction → **capabilities** (model +
  context window, the action kinds usable this run, held permissions + their short capability notes,
  spawnable workflow patterns, the util catalog at name+summary altitude — ONE util's usage on demand via
  `util name=list args=["<name>"]`) → **state digest** (phase, `state/`, step + trait modules, last result,
  LEDGER tail, open/answered questions, inbox messages). Effect actions (`util`/`read_file`/`write_file`/
  `edit_file`/`llm`) run through `engine/executor.py`. A default routine's composed prompt is ~25k chars;
  everything else is reachable on demand (read_file steps/traits/history, util name=list, memory_read).
- **The message list is a prompt-caching contract**: composed once, appended-to only, never mutated —
  so providers serve each turn's prefix from cache (~0.1x). Per-turn boilerplate is banned: the util
  reminder is ONE-SHOT on the kickoff/resume note, the history pointer re-appears only every 10th turn,
  and schema-retry debris is dropped from the live prompt once a retry succeeds (the transcript keeps
  the error events). Cache traffic reports as usage `cached_in`/`cache_write` (kept OUT of `in`, so
  token budgets keep their meaning); the loop hands every completion a stable `session` key (str(run_dir))
  that adapters may use as a cache hint.
- **Compaction archives context to a navigable on-disk history** (`history.compact_to_history`): when
  the prompt exceeds ~60% of the endpoint's `context_chars` — ~80% once cache hits are observed
  (compaction rewrites the prefix and invalidates the cache, so carried context is cheaper than
  re-archiving) — the middle turns are reorganized into markdown files (~≤100 lines each) under
  `runs/<ts>/history/` + `INDEX.md`; the prompt keeps only a pointer. The archival call runs on the
  routine's `tool_call` model when its window fits (machine work; main model is the fallback) and its
  spend is folded into the run's usage. Falls back to the deterministic one-line digest
  (`history.maybe_compact`) on any failure. The on-disk transcript keeps everything regardless.
- **A run resumes where it left off** (`run_routine(resume_from=…)`, `EngineLoop(resume=True)`): the
  transcript is replayed into the message list (`history.replay_messages`) with a fresh budget window
  (`budget_base_turn`); usage REPORTING stays cumulative across legs (`history.prior_usage` →
  `ctx.usage_base`; budgets ignore it). The **model can be switched mid-run** — a `control.json`
  `switch_model` signal applied at the turn boundary (`for_model` re-resolves every turn).

## Core contracts — extend, never repurpose

- **Actions** (`engine/actions.py` — flat schema on purpose; weak models and Ollama grammars handle flat
  far better than `oneOf`): `util, write_util, read_file, write_file, edit_file, memory_read,
  memory_write, llm, spawn, subruns, kill, wait, ask_user, finish`. Every action carries `say` (ONE
  terse sentence of narration) + `kind`. `read_file` batches related reads via `paths` (one turn, one
  observation section per file); `edit_file` anchor-replaces in place so revisions cost the diff, not
  the document.
  `ask_user` carries an optional `default` — what the run DOES when a blocking ask times out.
  `memory_*` are the ONLY way into `.memory/` (generic file actions are rejected there); the engine
  owns `.memory/INDEX.md` (built from each write's `about`) and the 100-line note cap.
- **The prompt surface is documented** in `docs/prompt-anatomy.md` (rendered on the Help tab). Revise
  it with ANY change to composer/loop/actions/schema_guard wording — `tests/test_prompt_anatomy.py`
  pins the load-bearing strings and fails on drift.
- **Transcript events** (`engine/transcript.py` — append-only JSONL, the engine is the only writer):
  `header, assistant_action, observation, question, answer, user_injection, subrun_start, subrun_end,
  compaction, error, finish`. This vocabulary is consumed by the web renderer AND the meta routine.

## Endpoints (endpoints/) — transports, not agents

Chat-completion adapters implementing one `ChatEndpoint.complete(...)` (`base.py` — tenacity retries on
retryable `EndpointError`s; a 200 with an unparseable body is one of them). All three honor
`ModelRef.effort` and report prompt-cache traffic as usage `cached_in`/`cache_write` (kept out of `in`).
`complete()` takes an optional `session` caching hint (a stable key per run) adapters may ignore. Three kinds:
- **openai** — any OpenAI-compatible API (OpenRouter, vLLM, Ollama). Schema via json_schema / json_object
  / ollama-native; degrades gracefully (retries without `response_format`/`reasoning` on a 400). Caching
  is the provider's implicit prefix caching; `cached_tokens` is surfaced from usage details.
- **anthropic** — Messages API, METERED per-token billing. Schema via a single forced tool-use; effort via
  `output_config`, degraded on a 400 that names it. Always sets `cache_control` breakpoints (tools +
  system static, a moving one on the last message) — ~0.1x reads on the whole prefix every turn; a 400
  naming cache_control gets a degraded retry without the markers.
- **claude-cli** — `claude -p` fully stripped (`--tools ""`, no MCP/settings, our `--system-prompt`
  replacing its own, `--json-schema`), SUBSCRIPTION-billed via `CLAUDE_CODE_OAUTH_TOKEN`. Metered-auth env
  vars are scrubbed so it can't silently fall back to API billing. With a `session` key it keeps ONE CLI
  session per run (`--session-id` / `--resume`, stable cwd under `~/.cache/rsched/claude-cli/`) and sends
  per-turn deltas so Anthropic's cache serves the prior turns; any prefix change (compaction, resume in a
  new process) or resume failure reseeds a fresh session from the full conversation. Without a session
  key: one-shot, temp cwd, `--no-session-persistence` (unchanged).

Each **routine sets its own three models** (`routine.yaml` `models:`): `main` (the loop),
`subroutine` (a spawned child's main model), `tool_call` (the `llm` action). A model a routine
leaves unset falls back to the server's single `system_model` — the ONE model for pre-routine
machine work (the clarify wizard + workflow generation/suggestion). There is no server "roles"
concept. `EndpointRegistry.for_model(kind, routine.models)` / `.for_system()` resolve them.

## Routines on disk

A routine dir (`~/routines/<slug>`) owns its recipe — the workflow library is NEVER read at run time:
- `routine.yaml` — `description` (one-line UI summary, always present), schedule (cron + tz + catchup),
  `workflow: {library_slug, library_commit}` (provenance only), `models:` (main / subroutine / tool_call),
  `permissions:` (held capability grants — user-changeable only, all surfaced on the routine page),
  `budgets:` (max_turns / wall_clock_min / total_tokens (-1 = unlimited, the default) / subruns /
  subrun_depth / ask_timeout_min — all editable in the UI, wizard + routine page), `fs_read_roots` / `fs_write_roots`, retention —
  budgets/fs-roots/schedules are resources, never grants; `improve: false` opts the routine
  out of the routine-improver's passes (default: included).
- `main.md` — the workflow **decomposed and materialized into this routine** (an entry state-machine that
  routes to `steps/<name>.md` modules, read on demand, and ends with a Standing practices tail
  referencing `traits/`). `instruction.md` — the task. `traits/*.md` — the routine's OWN practice
  modules, ADAPTED from library traits at creation (self-refined afterwards; no post-creation toggle).
- `state/`, `LEDGER.md`, `inbox/` (daemon/web drop messages + answers here), `questions/pending/`
  (the ONE decision-record shape: {mode, type, default, expires} — asks and util approvals alike),
  `runs/<ts>/` (transcripts + status.json incl. usage/turns/elapsed_s — the dashboard's sortable
  per-routine stats; gitignored, keep-last-N with gzip). The engine commits the working dir
  automatically — routines never run git themselves.

## Subruns, questions, injection

- **spawn** materializes a child routine on disk and runs its `EngineLoop` in a **thread** (not a
  subprocess); children get half the parent's remaining budget, a cap of 4 parallel, and are killed at
  parent finish (they never outlive the parent). Monitor via `subruns` / `wait` / `kill`; exits
  auto-announce at the next turn boundary and fold usage into the parent.
- **ask_user** is `blocking` (poll `inbox/answer-<qid>.json` up to `ask_timeout_min`, then the run
  CONTINUES on the action's stated `default` and the record stays open as deferred) or `deferred`
  (filed to `questions/pending/`, surfaced in a later run's state digest). Blocking asks are durable
  records too, and — when the routine holds the `communication` permission — are mirrored to Discord by
  the ENGINE (`engine/decisions.py`): a reply on either surface resolves everywhere and the other side
  is notified. The web layer posts answers into `inbox/`. Every finished (sub)run appends to
  `~/routines/.control/workflow-usage.jsonl` — the meta-workflows routine's evidence stream.

## Conversations (interactive sessions)

A **conversation** is a routine-shaped dir under its OWN home (`conversations_home`, default
`~/conversations`): schedule-less, `kind: conversation`, and — unlike routines — **never
git-versioned** (no `.git`, so the engine autocommit no-ops; delete means gone). The user's first
message IS `instruction.md`; the `converse` library workflow is materialized in verbatim at
creation (no LLM in the path — `conversations.py`; title + editable tags arrive off-path via the
system model). **Finish-per-reply**: every reply ends in an authored finish whose summary IS the
chat message; the next user message resumes the SAME run in place (fresh budget window —
`max_turns: 10` per reply; the engine's 85% warning cues a wrap-up-and-offer-continue).
- Runner: conversation replies draw from a **reserved interactive slot pool** (`INTERACTIVE_SLOTS`,
  3) — cron can't queue a chat reply and vice versa; `engine_cmd` targets `cfg.dir` (a path),
  which `_routine_dir` accepts. Run resolution in `api_runs`/`api_questions` is home-aware.
- Web: `web/api_conversations.py` (create/message are multipart — **attachments** land in
  `<conv>/attachments/` and ride the message text as an `[attached files]` block; vision util for
  images). **Artifacts**: deliverables the model `write_file`s into `<conv>/artifacts/` are
  listed/served here and rendered in the chat's side panel (html sandboxed, md/img/pdf/csv/json
  inline). UI: `static/views/conversations.js` + `components/chat.js` (work folded per reply,
  `[new-topic]` first-line marker → warn + one-click fork) + `components/artifacts.js`.
- Defaults: routine default permissions, shell OFF (one-click grant; `run-history*` greyed —
  routine-only); traits = ask-policy/global-utils/web-research/ledger-discipline/**git-checkpoint**
  (checkpoint commits in external project repos — the conversation dir itself is unversioned).
  Conversations feed workflow-usage + health events; they are EXCLUDED from the dashboard,
  scheduler, and instance-export. `bootstrap.sync_seed_library_docs` (every boot) lands new seed
  workflows/traits/permissions — how `converse`/`git-checkpoint` reach existing instances.

## Libraries & seeds

ONE git-backed library repo (`libraries_home`, default `~/.local/share/routine-scheduler-libraries`),
seedable from the repo and syncable to a remote, holding **workflows/** (control-flow patterns),
**traits/** (reusable practice prose, adapted per routine at creation), **permissions/** (capability
docs whose `grants:` frontmatter the engine enforces), and **utils/** (the ONLY way routines run
code, with the `gu` dispatcher at the root). Repo seeds: `library-seed/` (workflows + traits +
permissions),
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
- **Traits** (`library-seed/traits/`, `# trait:` heading, NO grants — lint-enforced): reusable practice
  prose. Selected at creation (the wizard preselects via `suggest_traits_permissions` from the refined
  instruction + chosen pattern), ADAPTED to the task by `adapt.decompose` (schema carries a `traits`
  array), written to `<routine>/traits/`, referenced from main.md's Standing practices tail
  (`scaffold._with_practices_tail` guarantees it) — the routine's own files from then on, never toggled.
  The set: `ask-policy / global-utils / web-research / ledger-discipline` + the five **after-run
  improvement passes** `improve-bugfix / -research / -features / -ui / -efficiency` (each infers the
  routine's intention from the run just completed and acts in its lens, asking a deferred question when
  unsure). `DEFAULT_TRAITS` (config) is the no-LLM fallback selection.
- **Permissions** (`library-seed/permissions/`, `# permission:` heading + machine-read `grants:` —
  {actions, utils, confirm, runs}): the routine's engine-enforced capability surface,
  held via `routine.yaml` `permissions:`, user-changeable ONLY (`grants.py` reads the LIBRARY copy;
  nothing under a routine dir is consulted, so routines can't self-grant). The set: `util-authoring`
  (confirm: true, default), `util-authoring-autonomous` (revisions-only), `util-authoring-full-auto`
  (false), `memory` (memory_read/memory_write — indexed ≤100-line notes in `.memory/`; INDEX.md
  engine-maintained, surfaced in the state digest; default),  `communication` (reserves `discord`; also turns on engine-side Discord mirroring of
  blocking decisions), `run-history` / `run-history-full` (read the last / all previous runs under
  runs/), `shell` (reserves the `shell` util — the escape hatch). Permission bodies are SHORT (≤14
  lines reach the prompt's CAPABILITIES section when held). Any future permission-ish lever becomes a
  `grants:` key here, not a new yaml key. See docs/traits-permissions.md. `DEFAULT_PERMISSIONS`
  (config) is the source of truth; defaults added after routines exist reach them once via
  `bootstrap.adopt_permissions` at daemon boot; `bootstrap.migrate_fragments_split` converts pre-split
  instances (fragments/ dirs + `fragments:` keys) at boot.
- **Utils** are self-contained PEP 723 scripts: a docstring header (`<name> — summary`, `usage:`,
  `calls:`, `tags:`, `secrets: NAME,…` — the docstring is the ONLY machine-read surface; comment-form
  declarations above it are invisible), and a `--selftest` the engine runs before saving. `write_util`
  is gated twice: `utils_lib.header_problems` rejects a missing `tags:` line or a credential env var
  the code reads but `secrets:` doesn't declare (the Settings page can only prompt for declared
  secrets), then the selftest; approval rides the held util-authoring permission's `confirm:` grant.
  Discover with the `util` action `name: list`.
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

## Versioning

`src/rsched/__init__.py` `__version__` is the single source (pyproject reads it via hatch's
version hook) — bump the minor on every user-facing revision. `/api/status` pairs it with the
running checkout's git commit stamp; the header's brand shows `v<version>` (tooltip = commit).

## Deploy

`deploy/install.sh` (idempotent host install: venv, config + token, seeds, systemd user service + linger)
or Docker (`docker compose up -d` — a disposable engine-only image; source, config, `~/.credentials`,
`~/routines`, and the library repo are all bind-mounted, so the whole system migrates as a tarball of
those dirs). Server config: `~/.config/routine-scheduler/config.yaml` (generated with a random token on
first boot by `bootstrap.ensure_config`, so a fresh deploy is never an open API). Web UI on `:8321`,
bearer-token auth; `RSCHED_BIND` / `RSCHED_PORT` override for containers. First launch redirects to
Settings until setup (secrets, endpoints + system model, GitHub device-flow, the library) is finished.
