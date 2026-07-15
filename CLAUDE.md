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
  or `-k <name>`. Live endpoint smoke tests run only with `RSCHED_LIVE_TESTS=1`. The suite
  includes the browser UI tests in `tests/ui/` (Playwright driving the REAL console over a
  stub runner — no scheduler, no engine, no LLM; see `tests/ui/conftest.py`). One-time per
  machine: `uv run playwright install chromium`. EVERY UI change gets exercised here — it is
  the safety net that lets the frontend be reworked boldly.
- `uv run ruff check` + `uv run mypy` — the strict quality gates (ruff runs `select = ALL`;
  every ignore in pyproject.toml carries its house-style reason). Both MUST be green in every
  commit; `uv run pre-commit install` wires them into git.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=endpoint:model` overrides a model role; `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>]`
  — see `rsched --help`. `engine-run` is internal (daemon-spawned).

## How a run works (engine/)

The turn loop (`engine/loop.py`) is the heart; `engine/runtime.py` is the entry above it
(`run_routine`, workflow loading/decomposition), `engine/boot.py` the initial message list
(kickoff or resume rehydration), `engine/completion.py` the get-one-valid-action side (schema
retries, refusal referral, media fallback, the compaction gate), `engine/control.py` the
between-turns control plane (abort, pause gate, `control.json` model switch, injection drain,
subrun announcements), and
`engine/interact.py` the user-conversing handlers (`ask_user`, grant-gated `write_util`). Each turn:
check budgets → pause gate → drain injected user messages (`inbox.py`) → announce finished subruns →
get ONE valid action from the model (up to 3 schema-retries) → dispatch → append the observation →
repeat until `finish`. **Budgets are one unified primitive** (`engine/budget.py`: a `Budget` = a stop
condition over a resource, a `BudgetLedger` over them, `allocate()` for child slices) shared by the run,
a conversation reply window, a subtask, and a subrun — `RunContext` holds the live meter, the ledger holds
the limits (single-writer status.json preserved).
- **One action per turn** is enforced: the model returns a single JSON object matching `ACTION_SCHEMA`;
  `normalize_action` + `validate_action` (`engine/actions.py`) repair grammar debris from weak/constrained
  models and return precise per-kind errors. `actions.py` is the single source of truth for what a turn
  may do — adapters, UI, and the CLI event renderer all key off it. A workflow's `tools:` allowlist AND
  the routine's **capabilities** (`grants.py`) are enforced there too: allowed kinds = workflow tools
  ∩ (base ∪ enabled capabilities), plus path gates (runs/ needs the previous-runs depth; a run NEVER
  writes its own recipe — main.md / stages/ / traits/ — a fixed rule unlocked only when a user-granted
  fs_write_root covers the routine dir (the routine-improver's case); `routine.yaml` is NEVER writable
  by any run, even under an fs_write_root — config is the user's; executor.py backstops absolute paths
  and scopes `runs: last`).
  A disallowed/switched-off call is corrected inside the schema-retry cycle with an error naming the
  covering permission, and never becomes a turn.
- **The system prompt is composed once at boot** (`engine/composer.py`; the CAPABILITIES
  section in `engine/capabilities.py`, observation rendering in `engine/observations.py`):
  harness contract → action schema
  + example → workflow body (the routine's own `main.md`, ending in a `## Standing practices` tail that
  references `traits/*.md` — practice prose is NEVER inlined; a SUBRUN inserts its INSTRUCTION brief
  here, a top-level routine does NOT — its task is baked into the recipe) → **capabilities** (model +
  context window, the action kinds usable this run, enabled capabilities + held permissions' short
  conduct notes,
  spawnable workflow patterns, the util catalog at name+summary altitude — ONE util's usage on demand via
  `util name=list args=["<name>"]`) → **state digest** (phase, `state/`, stage + trait modules, last result,
  LEDGER tail, open/answered questions, inbox messages). Effect actions (`util`/`read_file`/`write_file`/
  `edit_file`/`llm`) run through `engine/executor.py`. A default routine's composed prompt is ~25k chars;
  everything else is reachable on demand (read_file stages/traits/history, util name=list, memory_read).
- **The message list is a prompt-caching contract**: composed once, appended-to only, never mutated —
  so providers serve each turn's prefix from cache (~0.1x). Per-turn boilerplate is banned: the util
  reminder is ONE-SHOT on the kickoff/resume note, the history pointer re-appears only every 10th turn,
  and schema-retry debris is dropped from the live prompt once a retry succeeds (the transcript keeps
  the error events). Cache traffic reports as usage `cached_in`/`cache_write` (kept OUT of `in`, so
  token budgets keep their meaning); the loop hands every completion a stable `session` key (str(run_dir))
  that adapters may use as a cache hint.
- **Compaction archives context to a navigable on-disk history** (`history.compact_to_history`): when
  the prompt exceeds ~60% of the resolved model's `context_chars` — ~80% once cache hits are observed
  (compaction rewrites the prefix and invalidates the cache, so carried context is cheaper than
  re-archiving) — the middle turns are reorganized into markdown files (~≤100 lines each) under
  `runs/<ts>/history/` + `INDEX.md`; the prompt keeps only a pointer. The archival call runs on the
  routine's `tool_call` model when its window fits (machine work; main model is the fallback) and its
  spend is folded into the run's usage. Falls back to the deterministic one-line digest
  (`history.maybe_compact`) on any failure. The on-disk transcript keeps everything regardless.
- **Phase is live state**: a run's write to `state/phase.json` (write_file or edit_file) is mirrored
  into `ctx.phase` → status.json (every turn) → the run SSE `state` event, which also fires on phase
  change. `statemap.py` parses the routine's own main.md (`## Run flow` bold leads — the TASK-SPECIFIC state names decompose emits; stages/ filenames as fallback)
  into the UI's state-graph diagram (`/stategraph` endpoints, `static/components/stategraph.js` —
  rendered in the run view's rail and the conversation artifact rail, current phase highlighted live).
  Every `assistant_action` transcript event is stamped with the ACTIVE phase, so the rail is also an
  instrument panel: `statemap.phase_stats` (served at `/api/runs/<id>/phases`) derives per-phase
  turns / tokens / cost / wall-clock from the transcript — dispatch time lands on the acting phase,
  completion time on the phase that produced the next action.
- **A run resumes where it left off** (`run_routine(resume_from=…)`, `EngineLoop(resume=True)`): the
  transcript is replayed into the message list (`history.replay_messages`) with a fresh budget window
  (`budget_base_turn`); usage REPORTING stays cumulative across legs (`history.prior_usage` →
  `ctx.usage_base`; budgets ignore it). The **model can be switched mid-run** — a `control.json`
  `switch_model` signal applied at the turn boundary (`for_model` re-resolves every turn).

## Core contracts — extend, never repurpose

- **Actions** (`engine/actions.py` — flat schema on purpose; weak models and Ollama grammars handle flat
  far better than `oneOf`): `util, write_util, read_file, view_image, write_file, edit_file, memory_read,
  memory_write, llm, spawn, subtask, detach, subruns, kill, wait, ask_user, finish`. Every action carries `say` (ONE
  terse sentence of narration) + `kind`. `read_file` batches related reads via `paths` (one turn, one
  observation section per file); `edit_file` anchor-replaces in place so revisions cost the diff, not
  the document. `subtask` runs a child sub-workflow SEQUENTIALLY and blocks (the parallel `spawn`'s
  sibling — one child-task executor, `engine/childrun.py`); a `subtask` with `workflow: "generate"`
  drafts a new pattern when the `workflows: generate` capability is held (see docs/subtasks.md).
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
  / ollama-native; degrades gracefully (retries without `response_format`/`reasoning` on a 400, and without
  `response_format` on a 503 that hides a schema-incapable backend). Caching
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

The **model catalog** (`config.ModelConfig`, `ServerConfig.models`) binds a provider model id to
an endpoint and owns the PER-MODEL attributes — `multimodal`, `context_chars`, `effort`,
`temperature` (each None inherits the endpoint kind default / the endpoint's own value).
Endpoints hold only transport + auth + those DEFAULTS; `multimodal` is NOT on the endpoint (one
endpoint serves many models with different windows and vision support). Each **routine
references models BY NAME** (`routine.yaml` `models:` maps a role → catalog name): `main` (the
loop), `subroutine` (a spawned child's main), `tool_call` (the `llm` action), optional
`uncensored`. A role left unset falls back to the server's single `system_model` (also a catalog
name) — the ONE model for pre-routine machine work (the clarify wizard + workflow
generation/suggestion). `EndpointRegistry.resolve(name)` /
`.for_model(kind, routine.models)` / `.for_system()` produce a RESOLVED `ModelRef` (endpoint,
model id, effort + the filled-in multimodal/context_chars/temperature) — the runtime handle, no
longer parsed from yaml. `supports_media(mime, *, multimodal)` and compaction (`ref.context_chars`)
take the resolved model's values; `complete()` gains a `temperature` kwarg. Editing a catalog
model updates every routine that names it.

## Routines on disk

A routine dir (`~/routines/<slug>`) owns its recipe — the workflow library is NEVER read at run time:
- `routine.yaml` — `description` (one-line UI summary, always present), schedule (cron + tz + catchup),
  `workflow: {library_slug, library_commit}` (provenance only), `models:` (role → catalog model NAME:
  main / subroutine / tool_call / uncensored),
  `permissions:` (held CONDUCT docs) + `capabilities:` (the engine-enforced surface: {actions, utils,
  confirm, runs, workflows} — both user-changeable only, side by side on the routine page with cascades between
  them; `workflows: catalog|generate` gates in-run pattern drafting for subtasks),
  `budgets:` (max_turns / wall_clock_min / total_tokens (-1 = unlimited, the default) / subruns /
  subrun_depth / ask_timeout_min — all editable in the UI, wizard + routine page), `fs_read_roots` / `fs_write_roots`, retention —
  budgets/fs-roots/schedules are resources, never capabilities; `improve: false` opts the routine
  out of the routine-improver's passes (default: included).
- `main.md` — the workflow **decomposed and materialized into this routine** (an entry state-machine that
  routes to `stages/<name>.md` modules, read on demand, and ends with a Standing practices tail
  referencing `traits/`). The clarified instruction is only a transient compile SEED — decomposed into
  the stages at creation and NOT persisted (a routine carries no `instruction.md`); the stages are the
  sole source of truth. `traits/*.md` — the routine's OWN practice modules, ADAPTED from library traits
  at creation (self-refined afterwards; no post-creation toggle).
- `state/`, `LEDGER.md`, `inbox/` (daemon/web drop messages + answers here), `questions/pending/`
  (the ONE decision-record shape: {mode, type, default, expires} — asks and util approvals alike),
  `runs/<ts>/` (transcripts + status.json incl. usage/turns/elapsed_s — the dashboard's sortable
  per-routine stats; gitignored, keep-last-N with gzip). The engine commits the working dir
  automatically — routines never run git themselves.

## Child tasks (subtasks + subruns), questions, injection

- **A subtask and a subroutine are the same thing** — a child task materialized from a workflow
  pattern and run recursively (`engine/childrun.py` `build_child`, tree on disk under
  `runs/<ts>/sub/<n>/`), differing only in SCHEDULING and budget. Both are NON-BLOCKING background
  threads — the turn loop never monopolizes on a child, so the conversation stays responsive.
  **spawn** = PARALLEL (≤4 parallel; you keep working). **subtask** = SEQUENTIAL (start it, then
  `wait n=N` before the next so you can fold its result in; `turns` pins its budget, else half the
  parent's remainder). A child's completion is delivered by the turn-boundary hook
  (`announce_finished_subruns` — `SUBTASK FINISHED` / `SUB-WORKFLOW FINISHED`); `wait` is RESPONSIVE
  (it yields the moment a user message is pending — `inbox.has_pending_messages` — so the loop drains
  it and the parent replies, then waits again). Children are threads, so they die with the process
  (DELIBERATE — the subprocess alternative was evaluated and rejected, docs/subtasks.md § Process
  model): a resume marks any still-running child aborted and notes it (`history.orphaned_children`),
  and a subtask does NOT survive a conversation reply-finish — a job that must outlive a reply is
  the separate **`detach`** capability below, not a subtask.
  Decomposition is recursive (a child hits its own decompose gate; depth ≤ `max_subrun_depth`) and
  the `general-task` seed workflow carries a standardized `decompose_decision()` gate
  (inline | sequential | parallel); `converse` handles decomposition as inline prose.
  Children are killed at parent finish (never outlive it); exits fold usage into the parent. The
  recursive tree is visualized live in the run/conversation rail (`web/tasktree.py` read-model →
  `static/components/tasktree.js`). `subrun_start`/`subrun_end` events carry `mode` (sequential/parallel)
  + the child's allotted budget — payload EXTENSIONS, not new event types. A run interrupted mid-block
  on a subtask resumes with a synthesized "did not complete" observation (`history.dangling_subtask`).
  Pattern per child: pick a library slug, or `workflow: "generate"` to draft one (gated — see below).
- **A detached background task (`detach`) is the CROSS-REPLY counterpart** — for a long fire-and-forget
  job (a 20-min scrape) that must OUTLIVE a conversation reply. Unlike a subtask/spawn thread (dies with
  the reply's process), it runs as its OWN daemon-managed `engine-run` under a NEW `background_home`
  (config peer to routines/conversations), `routine.yaml` carrying `owner: {slug, dir}`. The engine
  handler is tiny — reject unless a root conversation (depth 0, under `conversations_home`), else drop an
  intent in `background_home/.requests/`; the daemon's **`DetachedManager`** (`daemon/detached.py`, single
  writer of `background_home`, ticked from `scheduler.run_forever` after the cron loop + a boot reconcile)
  owns the lifecycle — materialize (`childrun.materialize_to_disk`) + `runner.fire` on a third
  `BACKGROUND_SLOTS` pool → poll `status.json` (the `EventBus` is lossy) → on terminal, DELIVER
  (idempotent via `delivered.json` + a deterministic msg filename): copy `artifacts/` → owner, write a
  durable `<owner>/inbox/` message, then WAKE (`runner.resume` if idle, else the live reply drains it) +
  optional Discord ping (`communication`) → rebuild `<owner>/state/background.json` → gc past a grace
  window. Detached runs are excluded from the restart drain gate (the child survives SIGTERM via
  `start_new_session`; disk-poll delivers post-restart) and use deferred asks only. Gated by the
  `background-tasks` permission (default-ON for conversations); action = `detach` (never call it
  "background" — that means the within-reply subtask). Monitor/cancel via `web/api_background.py`
  (`GET/POST …/background`, `…/background/{id}/cancel`); the rail renders the tasks. See
  docs/background-tasks.md.
- **ask_user** is `blocking` (poll `inbox/answer-<qid>.json` up to `ask_timeout_min`, then the run
  CONTINUES on the action's stated `default` and the record stays open as deferred) or `deferred`
  (filed to `questions/pending/`, surfaced in a later run's state digest). Blocking asks are durable
  records too, and — when the routine holds the `communication` permission — are mirrored to Discord by
  the ENGINE (`engine/decisions.py`): a reply on either surface resolves everywhere and the other side
  is notified. All implicit outbound sends (the mirror + the detached-delivery ping) go through
  the ONE notification seam `rsched/notify.py` — see docs/notifications.md. The web layer posts
  answers into `inbox/`. Decisions-page LIFECYCLE (fields on the one record shape, never a new
  type): a blocking ask can be **deferred to the next run** (a `{defer: true}` inbox marker —
  the engine unblocks on the stated default, the record stays open; stale markers are swept at
  boot), a non-blocking one **snoozed** (`snoozed_until` on the record → `snoozed: true` derived
  on read; hidden from the inbox + badge, still in the run's digest), and a routine with >5
  unanswered deferred asks gets a `decision_backlog` flag on its dashboard card. Every finished
  (sub)run appends to
  `~/routines/.control/workflow-usage.jsonl` — the workflow-curator routine's evidence stream
  AND the durable spend series (tokens + cost + uncensored-referral count per finished run; run
  dirs fall to retention, this stream survives): `stats.monthly_spend` aggregates it per routine ×
  month — the Stats tab's "Monthly spend" table and the dashboard cards' compact month line
  (bg-task slugs attributed to their owner conversation; depth-0 entries only, a parent already
  folds its children in). The referral AUDIT (`ctx.referrals`: turns + llm calls the uncensored
  model answered — both paths increment it, children fold into the parent, status.json carries it
  per run) surfaces on the routine page's Models section.

## Conversations (interactive sessions)

A **conversation** is a routine-shaped dir under its OWN home (`conversations_home`, default
`~/conversations`): schedule-less, `kind: conversation`, and — unlike routines — **never
git-versioned** (no `.git`, so the engine autocommit no-ops; delete means gone). The user's first
message IS `instruction.md` (or, when a **playbook** is picked at creation, the playbook's brief
seeds it and the first message specializes it — see Libraries & seeds → Playbooks); the `converse`
library workflow is materialized in verbatim at creation (no LLM in the path — `conversations.py`;
title + editable tags arrive off-path via the system model). **Finish-per-reply**: every reply ends in an authored finish whose summary IS the
chat message; the next user message resumes the SAME run in place (fresh budget window —
`max_turns: 10` per reply; the engine's 85% warning cues a wrap-up-and-offer-continue).
- Runner: conversation replies draw from a **reserved interactive slot pool** (`INTERACTIVE_SLOTS`,
  3) — cron can't queue a chat reply and vice versa; `engine_cmd` targets `cfg.dir` (a path),
  which `_routine_dir` accepts. Run resolution in `api_runs`/`api_questions` is home-aware.
- Web: `web/api_conversations.py` (create/message are multipart — **attachments** land in
  `<conv>/attachments/` and ride the message text as an `[attached files]` block; vision util for
  images). **Artifacts**: deliverables the model `write_file`s into `<conv>/artifacts/` are
  listed/served here and rendered in the chat's side panel (html sandboxed, md/img/pdf/csv/json
  inline); routines get the SAME panel on the run view (`api_routines` `/artifacts` + `/artifact`,
  `components/artifacts.js` with `base: "routines"`), with the state-graph card on top.
  UI: `static/views/conversations.js` + `components/chat.js` (work folded per reply,
  `[new-topic]` first-line marker → warn + one-click fork) + `components/artifacts.js`.
- **Playbooks** (see Libraries & seeds → Playbooks): the new-conversation form has a playbook
  picker (`GET /api/playbooks`); the composer carries **Save as playbook** (`POST …/playbook` →
  distil a new one) and, when the conversation was seeded from a playbook, **Update playbook**
  (`PUT …/playbook` → revise that one) — both distil from the transcript via the `system_model`.
- Defaults: routine default permissions+capabilities PLUS **`background-tasks`** (the `detach` action —
  conversation-shaped, since a finished task reports back into the chat), shell OFF (one-click grant;
  run-history + the previous-runs depth greyed — routine-only); traits = ask-policy/global-utils/web-research/ledger-discipline/**git-checkpoint**
  (checkpoint commits in external project repos — the conversation dir itself is unversioned).
  Conversations feed workflow-usage + health events; they are EXCLUDED from the dashboard,
  scheduler, and instance-export. `bootstrap.sync_seed_library_docs` (every boot) lands new seed
  workflows/traits/permissions + playbooks (subfolder-aware) — how `converse`/`git-checkpoint` and
  seed playbooks reach existing instances.

## Libraries & seeds

ONE git-backed library repo (`libraries_home`, default `~/.local/share/routine-scheduler-libraries`),
seedable from the repo and syncable to a remote, holding **workflows/** (control-flow patterns),
**traits/** (reusable practice prose, adapted per routine at creation), **permissions/** (conduct
docs whose `requires:` frontmatter names the capabilities they presume), **playbooks/** (reusable
one-shot conversation briefs — the save/use-instruction analog), and **utils/** (the ONLY way
routines run code, with the `gu` dispatcher at the root). Repo seeds: `library-seed/` (workflows +
traits + permissions + playbooks),
`util-seed/` (utils), `routine-seed/` (bundled meta routines `self-audit`, `routine-improver`,
`workflow-curator`, `token-lab` — installed **disabled**; the dashboard shows a notice until
enabled; a seed added after first boot reaches existing instances via
`bootstrap.adopt_seed_routine` at daemon boot, which respects an archived copy). `token-lab` is
the token-efficiency R&D loop: measures real usage, tests methods via llm subcalls ONLY (never
integrates), publishes `artifacts/report.html`. `library-sync`
syncs the WHOLE instance into that one repo: `instance-export` copies each routine's working tree
(minus `runs/`, `.git`, transient inbox/question state) into `routines/<slug>/` and the server config —
token and api_key values redacted — into `config/`, then `git-sync` pushes. `bootstrap.py` seeds on
first boot; `deploy/install.sh` for host installs. Everything in the library is user-EDITABLE from
the Library tab, and DELETABLE except permission docs (the capability layer's conduct surface) and
the `clarify-instruction` workflow (the new-routine wizard runs it) — both guards are server-side.
A deleted seed workflow/trait returns at the next daemon boot (sync_seed_library_docs); a deleted
util stays deleted (git-recoverable — seed utils only land at repo creation).
- **Workflows** are self-contained **Python pattern files** (`.py`) that DEPICT a routine's control flow —
  never executed, parsed statically with `ast` (`workflows/pyworkflow.py`). Each has a `META = {...}` dict
  (`slug / name / description / when_to_use / version / tags / includes`, optional `tools:`
  allowlist), `PHASES` / `COMPLETION` literals, a top-level `main()` whose body is the per-run control flow,
  one function per step, and dummy parameter imports (`from routine.params import …`) naming the routine's
  parameters by type+meaning. The runtime is
  unchanged — routines are still the markdown `main.md`+`stages/` the orchestrator interprets: `adapt.decompose`
  turns a Python pattern into that markdown at scaffold; `materialize` renders it whole (sub-routines/fallback).
  A `tools:` list restricts action kinds (`finish` always allowed) — how `clarify-instruction` is held to
  ask/read/write/finish. `workflows/lint.py` gates every change; `suggest`/`generate` rank/draft via the
  `system_model`. The **new-routine wizard** runs `clarify-instruction`, which now SUGGESTS a pattern (or
  asks to generate one) and MARRIES the task to it — asking questions that overlay the task on the pattern's
  control flow + parameters (candidates written to the session's `state/candidates.md`).
- **Traits** (`library-seed/traits/`, `# trait:` heading, NO requires — lint-enforced): reusable practice
  prose. Selected at creation (the wizard preselects via `suggest_traits_permissions` from the refined
  instruction + chosen pattern), ADAPTED to the task by `adapt.decompose` (schema carries a `traits`
  array), written to `<routine>/traits/`, referenced from main.md's Standing practices tail
  (`scaffold.with_practices_tail` guarantees it; `scaffold.copy_traits` is the one trait-copy
  path routines and conversations share) — the routine's own files from then on, never toggled.
  The routine defaults (`DEFAULT_TRAITS`): `ask-policy / global-utils / web-research / ledger-discipline`;
  plus `git-checkpoint` (external-repo undo points — a conversations default, wizard-preselected for
  repo-editing routines, NOT a routine default). The five **after-run improvement passes** (bugfix /
  research / features / UI / efficiency) are NOT traits — the **routine-improver** meta routine owns
  them and sweeps every routine (honoring `improve: false`). `DEFAULT_TRAITS` (config) is the no-LLM
  fallback selection.
- **Permissions** (`library-seed/permissions/`, `# permission:` heading + machine-read `requires:` —
  {actions, utils, runs, workflows}, no confirm): CONDUCT docs of the two-layer permission set. The routine's
  enforced surface is its own routine.yaml `capabilities:` ({actions, utils, confirm, runs, workflows} —
  grants.py builds the run policy from it alone, so a doc-without-capability config fails closed);
  a doc's `requires:` names what its instructions presume and drives the UI cascades (activating a
  doc switches its requirements on; switching a capability off deactivates the docs requiring it —
  the server re-applies the activation cascade on save). Both layers user-changeable ONLY; routines
  can't self-grant. The doc set: `util-authoring` (requires write_util — the approval level
  always/creations/never is a CAPABILITY setting, default), `memory` (memory_read/memory_write —
  indexed ≤100-line notes in `.memory/`; INDEX.md engine-maintained, surfaced in the state digest;
  default), `communication` (requires `discord`; the enabled capability also turns on engine-side
  Discord mirroring of blocking decisions), `run-history` (previous-run reads; depth last/all is the
  capability), `shell` (requires the `shell` util — the escape hatch), `workflow-generation`
  (requires `workflows: generate` — a subtask may DRAFT a new library pattern when none fits, folding
  the system-model spend into the run; off by default), `background-tasks` (requires the `detach`
  action — launch a long fire-and-forget task that outlives a reply and reports back; default-ON for
  conversations, opt-in for routines). Reservable utils =
  the union of all docs' `requires.utils` (library-defined); gateable kinds = GATED_KINDS
  (engine-defined); `runs`/`workflows` are level capabilities. Permission bodies are SHORT (≤14 lines reach the prompt's CAPABILITIES section
  when held); the Library tab's permission editor has a prefilled, authoritative `requires:` panel.
  Any future permission-ish lever becomes a capability + a `requires:` entry, not a new yaml key.
  See docs/traits-permissions.md. `DEFAULT_PERMISSIONS`/`DEFAULT_CAPABILITIES` (config) are the
  source of truth; defaults added after routines exist reach them once via
  `bootstrap.adopt_permissions` at daemon boot. Historical data migrations are NOT kept:
  each runs once on the production instance and is deleted after convergence — a pre-0.8
  backup converts by booting the matching older tag first. MACHINE-CHECKED: migration code
  must carry a `MIGRATION(expires=YYYY-MM-DD)` marker comment; `tests/test_policy.py` fails
  once the date passes (and on migration-shaped code without a marker).
- **Playbooks** (`library-seed/playbooks/<slug>/`, `MAIN.md` + optional on-demand detail files):
  reusable, generalized **conversation briefs** — the in-app analog of the save-instruction /
  use-instruction pattern. A playbook is NOT a workflow (the `converse` workflow stays the harness);
  it only varies the *instruction*. `MAIN.md` front matter is `slug/title/when/tags/axis/updated`
  (`when` = the one-line catalog entry; `axis` = the generalization axis — what varies vs. stays
  fixed); the body is `## Parameters` (with `{{named}}` placeholders) + `## Instructions` + optional
  `## Detailed references` / `## Notes`. Storage + the live catalog are `playbooks.py` (a dedicated
  subfolder reader — NOT single-file `library_docs.py`/`DOC_RE`); git is the library ROOT
  (`workflows.library.git_commit`). A conversation is SEEDED from a picked playbook (its brief
  becomes `instruction.md`, the first message specializes it, `playbook: {slug, commit}` in
  `routine.yaml` records the binding → `cfg.playbook_slug`). The conversation's **Save as playbook**
  distils a NEW one and **Update playbook** revises the bound one, both from the transcript via the
  `system_model` (`playbook_distill.py` — `PLAYBOOK_SCHEMA`, mirroring `adapt.decompose` and
  the same refuse-to-degrade discipline). `workflows/lint.py` `lint_playbook_text` gates edits; the Library
  tab has a Playbooks section (`web/api_playbooks.py`). Reaches existing instances at boot via
  `bootstrap.sync_seed_library_docs` (subfolder-aware). See docs/playbooks.md.
- **Utils** are self-contained PEP 723 scripts: a docstring header (`<name> — summary`, `usage:`,
  `calls:`, `tags:`, `secrets: NAME,…` — the docstring is the ONLY machine-read surface; comment-form
  declarations above it are invisible), and a `--selftest` the engine runs before saving. `write_util`
  is gated twice: `utils_lib.header_problems` rejects a missing `tags:` line or a credential env var
  the code reads but `secrets:` doesn't declare (the Settings page can only prompt for declared
  secrets), then the selftest; approval rides the routine's write_util `confirm:` capability level.
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
  every rescan — no database, no cache files; parsing is memoized per file behind a stat()
  fingerprint (inode+mtime+size, so atomic rewrites always miss), pruned for deleted dirs,
  copies returned — the disk stays the source of truth on every lookup.
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
- `ruff check` (select ALL — every pyproject ignore names its house-style reason) and `mypy`
  are green in every commit; pre-commit enforces both. New ignores need the same one-line
  justification the existing ones carry.
- ONE outbound notification seam: any engine/daemon-implicit "reach the user" send goes through
  `rsched/notify.py` (see docs/notifications.md); new channels become a permission + a notify
  transport, never an inline util call.

## Versioning

`src/rsched/__init__.py` `__version__` is the single source (pyproject reads it via hatch's
version hook) — bump the minor on every user-facing revision. `/api/status` pairs it with the
running checkout's git commit stamp; the header's brand shows `v<version>` (tooltip = commit).
A bump MUST land with a matching `## [x.y.z]` CHANGELOG.md header in the same commit —
`tests/test_policy.py` (also a pre-commit hook) fails on a mismatch.

## Deploy

`deploy/install.sh` (idempotent host install: venv, config + token, seeds, systemd user service + linger)
or Docker (`docker compose up -d` — a disposable engine-only image; source, config, `~/.credentials`,
`~/routines`, and the library repo are all bind-mounted, so the whole system migrates as a tarball of
those dirs). Server config: `~/.config/routine-scheduler/config.yaml` (generated with a random token on
first boot by `bootstrap.ensure_config`, so a fresh deploy is never an open API). Web UI on `:8321`,
bearer-token auth; `RSCHED_BIND` / `RSCHED_PORT` override for containers. First launch redirects to
Settings until setup (secrets, endpoints + system model, GitHub device-flow, the library) is finished.
