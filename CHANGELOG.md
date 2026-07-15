# Changelog

All notable changes to **routine-scheduler** (`rsched`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Versioning conventions**
- The single source of truth is `__version__` in `src/rsched/__init__.py` (pyproject reads
  it via hatch). `/api/status` pairs it with the running checkout's git commit stamp so a
  deploy is always identifiable.
- **Bump the minor on every user-facing revision**; the patch for isolated bug/regression
  fixes. Each code-changing commit that ships a revision should bump `__version__`, tag the
  version in its commit subject `(x.y.z)`, and add an entry here.
- Dates are UTC. The project has a fast, single-author cadence (many commits per day), so
  entries group related work rather than list every commit.

## [Unreleased]

_Nothing yet._

## [0.43.0] — 2026-07-15

### Added
- **The state-graph rail is an instrument panel**: every `assistant_action` transcript
  event now carries the phase that was active while it was produced, and
  `statemap.phase_stats` (served at `GET /api/runs/{id}/phases`) derives per-phase
  turns · tokens · wall-clock · cost from the transcript — dispatch time attributed to
  the acting phase, completion time to the phase that produced the next action, the
  tail after the last action to the last phase. The run-view and conversation rails
  render the numbers on each visited node, refreshed on every phase transition; turns
  from before any `phase.json` write show as a "before any phase" foot line.

## [0.42.0] — 2026-07-15

### Security
- **The bearer token no longer rides SSE query strings** (where it leaked into access
  logs). EventSource connections mint a short-lived, unguessable ticket first
  (`POST /api/sse-ticket`, 60 s TTL, multi-use within it so browser reconnects keep
  working; expired tickets purged on mint) and send that instead; `?token=` is no longer
  accepted anywhere. Reconnects mint fresh tickets automatically via the `sse()` wrapper.

## [0.41.0] — 2026-07-15

### Changed
- **Decisions page is a grouped inbox**: the priority view renders sections — *Blocking
  (a run is waiting on you)* → *Deferred* → *Meta* → *Settled (answered, queued)* — with
  section headers + counts; a blocking ask within 30 minutes of its timeout carries a
  loud red "expiring" chip and sorts to the very top of its group. Keyboard navigation
  (↵ / ↑↓ / 1-9), every filter chip, the routine filter and the non-priority sorts (which
  render flat, as before) all survive unchanged.

## [0.40.0] — 2026-07-15

### Changed
- **Run view: one message input with an explicit mode selector** replacing the shifting
  two-button arrangement. Where a message goes is stated, not implied: a live run fixes
  the mode to "→ live run" (inject, picked up at the next turn boundary); a terminal run
  offers "→ continue this run" (rehydrate and converse, the default) or "→ queue for next
  run". Enter always sends in the visible mode.

## [0.39.0] — 2026-07-15

### Changed
- **Routine page saves in place — no full-page reload anywhere.** Schedule saves refresh
  the header chip + next-fire line from a fresh read; permissions saves re-render the
  panel from the server's post-cascade state; models saves just toast (the selects already
  hold the truth). Scroll position and unsaved edits elsewhere on the page survive a save.
- **One shared tag editor** (`components/tags.js`) for routines AND conversations: chips
  with ✕ remove plus an inline add field, every change saved immediately — the routine
  page's separate "save tags" button and the conversation's prompt-dialog "+" are gone.

## [0.38.0] — 2026-07-15

### Changed
- **One shared answer form** (`components/answerform.js`) replaces the six hand-rolled
  copies (Decisions page, run view, conversation panel, wizard, transcript inline, chat
  inline). The component owns the core — input/textarea, option buttons (numbered + digit
  keys where wanted), default line, ask-back, Enter-to-submit, draft persistence, error
  toast — while each host keeps its chrome (meta chips, expires/mirrored notes,
  snooze/defer lifecycle, settled states) via `{ node, input, submit, setSettled }`.
  Accidental drift fixed in passing: the chat inline form no longer swallows errors
  silently, option buttons focus the input everywhere, and the conversation question
  panel renders markdown like every other surface.

## [0.37.0] — 2026-07-15

### Changed
- **Every native `confirm()`/`prompt()` replaced with themed dialogs**
  (`components/dialog.js` — the token gate's overlay language, keyboard-first: Enter
  confirms, Esc/overlay-click cancels, promise-based call sites). Covers routine archive,
  run abort, conversation delete + add-tag, workflow/playbook delete, endpoint/model/secret
  delete. Destructive confirms carry an action-named red button ("delete", "abort",
  "archive") instead of a generic OK.

## [0.36.0] — 2026-07-15

### Added
- **Uncensored-referral audit**: every referral — a turn the main model refused that the
  `uncensored` model answered (turn loop), or an `llm` call the tool model refused
  (executor) — increments `ctx.referrals`; children fold theirs into the parent. The
  count rides each run's `status.json`, the durable workflow-usage stream (so it survives
  retention and aggregates per month), and surfaces on the routine page's Models section
  ("↪ uncensored referrals: N total · M this month").

## [0.35.0] — 2026-07-15

### Added
- **Monthly spend aggregation** — answers "what does this routine cost me and is it
  growing": the workflow-usage stream now records each finished (sub)run's `cost` and
  serves as the DURABLE spend series (run dirs fall to retention; the stream survives).
  `stats.monthly_spend` rolls it up per routine × calendar month (depth-0 entries only —
  a parent's usage already folds its children in; detached-task slugs attributed to their
  owner conversation). Surfaced as a **"Monthly spend by routine" table on the Stats tab**
  (last 6 months, tokens · cost per cell, growing/steady/shrinking trend chips) and a
  **compact month line on every dashboard card** ("Jul: 2.00M tok · $2.00 (Jun: …)", with
  an ↑ growing chip past +20%). Historical entries predate the cost field, so cost sums
  start now; token trends are complete.

## [0.34.0] — 2026-07-15

### Added
- **Decision lifecycle on the Decisions page** — fields on the ONE record shape, not a
  new record type:
  - **Defer to next run** (blocking questions): a `{defer: true}` inbox marker releases
    the engine's blocking wait immediately — the run continues on the action's stated
    default, exactly the timeout path but chosen by the user; the record stays open as
    deferred, Discord (when mirrored) is told, and a marker that outlives its run is
    swept silently at the next boot.
  - **Snooze** (deferred questions): `snoozed_until` on the record hides it from the
    inbox, the nav badge, and every non-Snoozed filter until the timestamp (1h/4h/1d/1w
    or unsnooze); runs still see the open question in their state digest — snooze is UI
    noise control, never an answer.
  - **Decision-backlog flag**: a routine with more than 5 unanswered deferred asks gets a
    loud `decision backlog` chip on its dashboard card — the "silently starving on my
    input" signal.

## [0.33.0] — 2026-07-15

### Added
- **Policy gates as tests** (`tests/test_policy.py`, wired into pre-commit): (1) the
  delete-after-convergence rule is machine-checked — one-shot migration code must carry a
  `MIGRATION(expires=YYYY-MM-DD)` marker and the suite fails once the date passes (or on
  migration-shaped code without a marker); (2) a `__version__` bump without a matching
  `## [x.y.z]` CHANGELOG header at the top fails the suite (0.27 shipped without notes once).
- **Seed contracts pinned** (`tests/test_seeds.py`): every `routine-seed/` loads clean via
  `load_routine` (permissions exist, capabilities normalize, Standing-practices tail +
  bundled traits present, all `stages/*.md` references resolve), every seed markdown's
  `state/phase.json` assignment uses the canonical `{"phase": ...}` shape and names only
  live action kinds, `library-seed/` workflows parse via pyworkflow with slug/tools checks
  and the whole tree lints clean, and `util-seed/` docstring headers pass the engine's own
  `write_util` gate. Seed drift is now a test failure in the commit that causes it.

## [0.32.0] — 2026-07-15

### Changed
- **`engine/loop.py` and `engine/composer.py` split under the ≤~350-line standard**,
  behavior-preserving (every prompt string byte-identical; `test_prompt_anatomy` pins them).
  New modules, each one responsibility: `engine/completion.py` (get ONE valid action —
  schema retries, repeat-streak shedding, refusal referral, media fallback, the compaction
  gate), `engine/boot.py` (kickoff / resume rehydration of the message list),
  `engine/observations.py` (observation → next user message + truncation),
  `engine/capabilities.py` (the CAPABILITIES prompt section). `loop.py` keeps only the
  turn cycle; `composer.py` the system-prompt assembly and state digest.

## [0.31.0] — 2026-07-15

### Added
- **Browser UI test harness** (`tests/ui/`): Playwright drives the REAL console — the
  FastAPI app + static frontend served by uvicorn on an ephemeral port over fixture homes
  and a stub runner (no scheduler, no engine subprocess, no LLM). Covers the four
  load-bearing flows: Decisions answering (options, default, Enter-to-submit, blocking
  from a live run), the conversation composer (create + follow-up message), routine-page
  saves (description, budgets), and Settings endpoints/models CRUD (create, edit, delete
  behind confirm dialogs). Every test also fails on any uncaught JS error, and asserts
  what landed **on disk**, not just what the toast claimed. One-time setup:
  `uv run playwright install chromium`.

## [0.30.0] — 2026-07-15

### Added
- **Child-task process-model decision record** (docs/subtasks.md § Process model): evaluated
  migrating `spawn`/`subtask` threads onto the detached-subprocess pattern (to delete the
  resume-orphan handling) and rejected it with reasons — start latency, live budget folding,
  the responsive wait being a feature not a workaround, and the replacement lifecycle
  dwarfing the ~60 lines it would remove. Threads stay; `detach` remains the cross-process
  escape hatch.

### Changed
- **Registry scans are memoized behind stat() fingerprints** (`daemon/registry.py`): each
  parsed `status.json`/`result.md`/`routine.yaml`/question set is reused only while its
  (inode, mtime, size) fingerprint matches — freshness is re-decided from the filesystem on
  every lookup, callers get copies, entries for deleted dirs are pruned. Warm scan on the
  production instance: 77 ms → 9 ms, with no database and no invalidation protocol.

## [0.29.0] — 2026-07-15

The whole-codebase overhaul: every subsystem audited (engine, endpoints, daemon, web,
UI, workflows/seeds, tests, docs), bugs fixed, dead code and every legacy shim removed,
duplication unified, strict quality tooling introduced. No backwards compatibility is
kept — converged one-shot migrations and tolerant readers for retired formats are gone.

### Added
- **One outbound notification seam (`rsched/notify.py`).** Every engine/daemon-implicit
  "reach the user" send — the blocking-decision Discord mirror and the background-task
  delivery ping — goes through one module; channels are user-selected (web always,
  Discord via the `communication` permission), and the durable record is always the
  Decisions page / the conversation. New guide: `docs/notifications.md`.
- **Strict tooling, enforced.** `ruff` with `select = ALL` (every ignore carries its
  house-style justification inline in pyproject.toml), `mypy` over `src/rsched`,
  branch-coverage config, and a `.pre-commit-config.yaml` wiring both gates into git.
- **`docs/authoring.md`** — the missing guide to writing utils (PEP 723 + docstring
  standard + selftest), workflow patterns (`META`/`PHASES`/`main()`), traits,
  permissions, and playbooks, each with a real example.

### Fixed
- **Token budgets now mean the same thing on every provider**: the OpenAI-compatible
  adapter counted cached prefix tokens inside `in`, so `total_tokens` budgets burned
  cached traffic at full weight on OpenRouter/Ollama but not on Anthropic; cached tokens
  are now kept OUT of `in` across all three adapters (the documented invariant).
- **A dialog ("ask back") reply no longer destroys the decision record.** Intermediate
  replies used to resolve the pending question and tell Discord "resolved" before the
  dialog was over — a finish without a re-ask silently dropped the decision. The record
  now stays open (deferred) through the dialog; the model's re-ask supersedes it, a real
  answer resolves it, and a finish leaves it live for the next run.
- **`routine.yaml` is written atomically everywhere** (conversation autolabel, patch,
  wizard finalize) — three raw `write_text` sites violated the cross-process
  atomic-write invariant and could tear a concurrent engine boot read.
- Conversation "reply ready" desktop notifications now honor the Settings opt-in;
  Stats empty-states render their glyph correctly; same-placeholder form fields no
  longer share one draft-persistence key.
- Meta-routine seeds: three seeds shipped the removed `ask_timeout_h` key; the improver
  read a nonexistent `instruction.md`; self-audit's main.md contradicted its own
  write-report stage on deferred asks; phase-file keys standardized on `{"phase": …}`;
  false workflow provenance (`self-audit-code`, `meta-workflows`) removed.

### Changed
- **Settings leads with Endpoints → Models → System model** (the first-run critical
  path) and loads its sections in parallel; dashboard bus reloads are debounced.
- Shared UI primitives extracted (`states.js`, `follow.js`, unified formatters in
  `util.js`); duplicated backend logic unified (artifacts listing/serving, permission
  detail blocks, active-run guards, terminal-state constants, terminal-resume, the
  engine's usage folding, injection message shape, phase parsing, api-key resolution and
  HTTP plumbing across the three endpoint adapters).
- The stale committed `audit/` artifact (a self-audit run pointed at the source tree)
  is removed and gitignored; CHANGELOG gains the missing 0.27 entry and a proper 0.18
  header; README/CLAUDE.md/DOCKER.md drift fixed (`improve: false`, `workflow-curator`,
  `main()` patterns, model-catalog era Docker notes).

### Removed
- **All converged one-shot migrations and legacy shims** (the delete-after-convergence
  policy, applied): `rsched migrate-model-catalog`, `rsched migrate-stages`, the
  `ask_timeout_h` config shim, the legacy `confirm` vocabulary (`true` /
  `revisions-only` / `false`), the `fragment:` library-doc reader and `fragments` config
  scrub, `parse_run_ts`'s dead tz parameter, the `timeout_h` observation fallback, the
  `status: stable` frontmatter in fallback child recipes, and the empty boot-time
  permission-adoption walk.
- Dead code throughout: the unused `/routines/{slug}/files` endpoint, unread response
  fields (`endpoints` lists, `finish_status`), `GrantPolicy.workflows_sources`,
  `BudgetLedger.get`, `read_trait`, the vestigial `strip_inactive_improve` pass, unused
  UI components/CSS/exports, and tautological or dead test code.

## [0.28.0] — 2026-07-15

### Changed
- **Step modules are now "stage modules" (`stages/`).** A routine's decomposed workflow modules were
  called *step modules* and lived in `steps/`; they are now **stage modules** in `stages/`, listed by
  the `stages:` key in `main.md`'s frontmatter (was `modules:`), and the wizard/decompose schema emits
  `stages` (was `steps`). How a run reads them is unchanged — `main.md` is still the entry state machine
  that routes to on-demand modules.
- **The live workflow diagram is labelled with the routine's own stage names.** `decompose` now emits
  task-specific bold `## Run flow` state names that match the stage filenames, so the state-graph card
  in the run and conversation rails shows the routine's actual stages instead of the generic library
  pattern's states.
- **The routine-improver edits a target's RECIPE directly and proposes config changes via a deferred
  ask.** It rewrites `main.md` / `stages/` / `traits/` in place (the recipe is the source of truth); for
  any `routine.yaml` CONFIG change — budgets, models, permissions, capabilities, fs-roots — it files a
  **deferred `ask_user`** to the Decisions page rather than writing the file. A run NEVER writes
  `routine.yaml`.

### Removed
- **The seed→recompile machinery is gone — stage modules are the sole source of truth.** There is no
  longer a persisted per-routine *Seed*, no recompile-from-instruction step, no seed↔stages drift
  detection, no provenance hashing (`seed_sha256` / `compiled_sha256`), no routine-page Seed editor, and
  no `RecompileDriftError`. The clarified instruction is only a **transient compile seed** consumed at
  creation; a real routine dir no longer contains `instruction.md` (only the wizard's throwaway clarify
  session still uses one internally). After creation you edit a routine by editing its `stages/` /
  `main.md` / `traits/` directly — the routine page gains a navigable **Recipe** file-tree for exactly
  that — and there is no recompile step to undo those edits.

## [0.27.0] — 2026-07-15

### Changed
- **Per-model attributes moved off endpoints into a named model catalog.** A new
  `models:` catalog in the server config (`ServerConfig.models`, Settings → Models) binds a
  provider model id to an endpoint and owns the PER-MODEL attributes — `multimodal`,
  `context_chars`, `effort`, `temperature` (each `None` inherits the endpoint-kind default
  or the endpoint's own value). Endpoints hold only transport + auth + those defaults;
  `multimodal` is no longer an endpoint property (one endpoint serves many models with
  different windows and vision support). Every routine/conversation references models **by
  catalog name** (`routine.yaml` `models:` maps role → name), as does the server's
  `system_model`; `EndpointRegistry.resolve()` / `.for_model()` / `.for_system()` return a
  fully resolved `ModelRef` (endpoint, model id, effort, multimodal, context_chars,
  temperature). Editing a catalog model updates every routine that names it.
- `supports_media()` and compaction take the resolved model's values; `complete()` gains a
  `temperature` kwarg honored by all three adapters.

### Added
- A one-shot `rsched migrate-model-catalog` converted a pre-0.27 endpoint-attribute config
  (deleted after production convergence, per the migration policy).

## [0.26.0] — 2026-07-15

### Added
- **Detached background tasks — long fire-and-forget in conversations (`detach`).** A conversation can
  now kick off a LONG job (a 20-minute scrape, a bulk conversion), keep chatting about other things, and
  be told when it lands. Unlike a within-reply `subtask`/`spawn` (a thread that dies when the reply's
  process exits), a detached task runs as its OWN daemon-managed `engine-run` process and survives across
  reply-finishes, reporting its result back into the conversation on completion. The new `detach` action
  (fields `prompt` / optional `workflow` + `label`) is deliberately tiny on the engine side — it drops an
  intent file in a new `background_home` (a config peer to `routines_home`/`conversations_home`) and
  returns, so the assistant `finish`es the reply ("started it — I'll report back") and the conversation
  continues normally. See `docs/background-tasks.md`.
- **The `DetachedManager` (`daemon/detached.py`) owns the whole lifecycle, all on disk (restart-safe).**
  Ticked from the scheduler after the cron-fire loop (+ a boot reconcile), it: materializes each task dir
  (`childrun.materialize_to_disk`, `routine.yaml` carrying `owner: {slug, dir}`, permissions/models/fs-
  roots copied from the owner but a background-sized budget of its own) and `runner.fire`s it on a third
  `BACKGROUND_SLOTS` pool; polls `status.json` for completion (the `EventBus` is lossy); on terminal
  DELIVERS (exactly-once via a `delivered.json` marker + a deterministic message filename) — copies the
  task's artifacts into `<owner>/artifacts/from-bg-<taskid>/` and writes a durable inbox message — then
  WAKES the conversation (`runner.resume` if idle, else the live reply drains it) with an optional Discord
  ping when the owner holds `communication`; rebuilds `<owner>/state/background.json` (inlined into the
  reply's state digest so the assistant can answer "how's the scrape going?"); and gc's delivered tasks.
- **Monitor + cancel.** `GET /api/conversations/{slug}/background` lists a conversation's tasks,
  `POST …/background` drops an intent (the human/test analog of the engine action), and
  `POST …/background/{id}/cancel` aborts one (`runner.abort` + a pid fallback for a task that outlived a
  restart). The conversation rail renders a **background** card (label · state · cancel);
  `web/api_runs.py`'s run resolution now searches `background_home`, so a detached run's transcript /
  task-tree resolve on the generic `/api/runs` endpoints for free. Deleting a conversation tears down its
  detached tasks.
- **New `background-tasks` permission** (`requires: {actions: [detach]}`) — default-ON for conversations,
  opt-in for routines; `detach` joined `GATED_KINDS`.

### Changed
- Detached runs are **excluded from the self-update drain gate** (`ActiveRun.background` →
  `Runner.active_states` skips them): the engine child survives the daemon's SIGTERM via
  `start_new_session`, so a long background job never blocks a deploy, and the manager's disk-poll delivers
  it after the restart. Detached tasks also use **deferred asks only** (coerced in `interact.handle_ask`)
  so one can never park in `waiting_user` and hold a restart. `RoutineConfig` gained an `owner` field.
- The `converse` seed workflow's decompose guidance learned a `detach` branch (long/independent →
  detach; short/interactive → inline or `subtask`).

## [0.25.0] — 2026-07-15

### Added
- **Sequential subtasks — recursive task decomposition as a first-class concept.** A run can now
  decompose its work into an ORDERED sequence of subtasks, each run to completion before the next —
  distinct from the existing PARALLEL subruns (`spawn`). The realization: a subtask and a subroutine
  are the SAME thing — a child task materialized from a workflow pattern and run recursively — so the
  new `subtask` action and `spawn` are two schedulers over one child-task executor (`engine/childrun.py`,
  generalized from `subruns.py`). `subtask` is NON-BLOCKING: it starts a sequential child in the
  background (its own thread + context + pattern) and the parent keeps sequential order by `wait`-ing
  for it before the next; the completion is delivered by the turn-boundary hook, and `wait` is
  RESPONSIVE — it yields to a waiting user message so the conversation stays live while children run.
  Fields: `prompt` (self-contained brief), optional `workflow` (a library pattern for the step's
  purpose), `label`, `turns` (its budget). Decomposition is recursive (a child hits its own gate; depth
  ≤ `max_subrun_depth`). See `docs/subtasks.md`.
- **The decompose-decision gate in the seed workflows.** Concrete subtasks are never known statically,
  so the `general-task` (v9) and `converse` (v2) patterns now carry a standardized `decompose_decision()`
  step that decides inline | sequential (subtasks) | parallel (subruns) — reaching existing routines on
  recompile, new ones at creation.
- **In-run workflow generation (gated).** A subtask with `workflow: "generate"` DRAFTS a new library
  pattern for its brief (`workflows/generate.py`, lint-gated, committed) when the routine holds the new
  `workflows: generate` capability — covered by the `workflow-generation` permission, off by default,
  skipped when the token budget is nearly spent. The generation call's system-model spend folds into the
  run's budget.
- **The recursive task-tree visualization.** The run and conversation rails carry a live task-tree card
  (`static/components/tasktree.js`, fed by the `web/tasktree.py` read-model over the on-disk `sub/`
  transcripts): sequential subtasks (→) and parallel subruns (⇉), each a node with a state icon, its
  workflow pattern, and a per-node turn-budget meter (amber ≥85%, red over), children nested. `run-once`
  prints the same tree.

### Changed
- **Budgets are now one unified primitive** (`engine/budget.py`): a `Budget` is a stop condition over a
  resource, a `BudgetLedger` is an ordered set of them, and `allocate()` slices a child's ledger from
  the parent's remainder. The run, a conversation reply window, a subtask, and a subrun all share it —
  `RunContext` holds the live meter, the ledger holds the limits (single-writer `status.json` preserved;
  wording and status shape unchanged). Per-subtask budgets are SOFT at the parent: a child that overruns
  its own turn cap force-finishes `partial` and the parent re-plans; only run-level budgets hard-stop.
- `subrun_start`/`subrun_end` transcript events gained a `mode` (sequential/parallel) and the child's
  allotted budget — payload EXTENSIONS, so every existing consumer keeps working. Children are threads
  that die with the process, so a resume marks any still-running child aborted and notes it
  (`history.orphaned_children`) rather than letting the parent `wait` forever. `wait` also became
  responsive to pending user messages (`inbox.has_pending_messages`).

## [0.23.0] — 2026-07-15

### Fixed
- **Recompile no longer silently reverts routine hand-edits (the "rematerialization" bug).**
  `recompile_routine` re-derives a routine's `steps/` from its instruction × workflow; it used to
  do so unconditionally, discarding any hand-edits (the routine-improver's or a person's) that the
  routine page's drift banner already reported but the action ignored. This is what kept reverting
  newsletter-digest's fixes back to the library pattern's design. Recompile now consults
  `provenance.drift()` first: when the steps have drifted from the compile baseline and the edits
  are not in the seed, it **refuses** (`RecompileDriftError`; surfaced as `state=error`,
  `reason=steps_drift`) so nothing is lost silently. Pass `?force=true` to overwrite — and even
  then the pre-recompile `main.md` + `steps/` are backed up to `state/recompile-backups/<ts>/`
  first. The refusal keys off `provenance.drift()`, which reports no steps-drift for a routine that
  has no compile baseline, so only a routine whose steps drifted from its baseline trips the guard.

## [0.22.0] — 2026-07-15

### Changed
- **The graceful self-restart now DRAINS in-flight new-routine wizard builds** instead of only
  cleaning up their fallout (complements 0.20.1's boot-time `recover_orphan_builds`). A wizard
  build (`api_wizard._build_routine`) is an unpersisted web-process background task; restarting
  mid-build stranded a half-scaffolded routine. Now the scheduler tracks in-flight builds
  (`Scheduler.wizard_builds`, registered by `finalize`, cleared when the build ends) and the
  restart state machine treats a build as finishable work: `restart_action` gained a
  `builds_active` count, so a pending restart stays in **drain** (fires nothing new) until both
  active runs **and** builds have finished before it exits. While draining, `finalize` refuses a
  new build with **503** so the drain converges. A build is never "parked", so it can only hold
  the restart in drain, never defer it. (AUDIT follow-up: "drain builds as well instead of just
  dealing with the fallout.")

## [0.21.0] — 2026-07-15

### Added
- **Refusal referral now covers the main orchestrator loop and subroutine loops** (extends the
  0.20.0 `llm`-tool-call referral; AUDIT decision **D8 → C**). In an agent loop a turn is a
  schema-constrained *action*, so a model refusal surfaces as a free-text reply that fails to
  parse as an action **and** reads as a decline (`executor._looks_like_refusal`). When that
  happens and the routine has an `uncensored` model configured, `EngineLoop._next_action`
  re-issues the SAME turn to it once; a schema-valid action from the uncensored model continues
  the run untouched and the `assistant_action` transcript event is tagged `referred: true`.
  Subroutines run the same loop, so both are covered by one code path. Strictly **opt-in and
  inert**: no `uncensored` role → no referral, unchanged behaviour. A malformed-but-not-refusing
  reply still takes the normal schema-retry path (the uncensored model is consulted only on a
  genuine decline, at most once per turn); referral usage is folded into the turn's usage. No
  new action kind or transcript `EVENT_TYPE` — `referred` is an additive field on the existing
  `assistant_action` event, mirroring 0.20.0's observation field. `docs/endpoints.md` scope note
  updated.

## [0.20.1] — 2026-07-15

### Fixed
- **Wizard builds orphaned by a server restart/crash no longer hang forever.** A new-routine
  build (`api_wizard._build_routine`) runs as a web-process background task with no
  persistence; if the process dies between `finalize.json` = `building` and the terminal
  `done`/`error` write — e.g. a self-restart, which drains engine **runs** but not in-flight
  **builds**, or a crash/SIGKILL — the setup was stranded: `finalize.json` stuck at
  `building`, a half-scaffolded routine dir with no `routine.yaml`, and nothing to complete
  it (`Runner.recover_orphans` reconciles engine runs only). The user saw a setup that "never
  finishes" with no LLM call in flight. Boot now runs `wizard_store.recover_orphan_builds`:
  any `building` state in a fresh process is by definition orphaned, so it is marked a
  recoverable `error` (retry/cancel from the wizard) and its half-built dir (no `routine.yaml`)
  is removed — mirroring `_build_routine`'s own exception handler. (AUDIT note.)

## [0.20.0] — 2026-07-15

### Added
- **Optional `uncensored` model role + refusal referral for the `llm` tool-call.** A routine
  can now assign a fourth model role — **`uncensored`** — alongside main/subroutine/tool_call
  (`MODEL_KINDS`, the per-routine model editor in `routine.js`, `docs/endpoints.md`). When the
  routine's `tool_call` model answers a **free-text** `llm` action with a content refusal
  ("I can't help with that…"), the engine re-issues the **same** prompt to the `uncensored`
  model and returns that answer with `referred: true` on the observation. Strictly **opt-in
  and inert by default**: the `uncensored` role has **no system-model fallback**, so any
  routine that leaves it unset behaves exactly as before. Only free-text replies are
  considered — a schema-constrained (`response_schema`) reply is an answer, never a refusal —
  and the refusal detector (`executor._looks_like_refusal`) matches a decline only at the
  head of the reply, trading recall for precision so genuine answers are not rerouted. Scope
  today is the `llm` tool-call only (the orchestrator/subroutine loops have no clean
  free-text refusal signal). `docs/endpoints.md` gains a turnkey **Nano-GPT** (`kind: openai`,
  `base_url: https://nano-gpt.com/api/v1`) endpoint example that serves abliterated models
  directly. (AUDIT note.)

## [0.19.0] — 2026-07-15

### Fixed
- **Run timestamps are now unambiguously UTC end-to-end — the ~2h clock skew is gone.**
  `ids.run_ts()` always emits UTC (was server-local: identical on a UTC host, but a bare
  `YYYYMMDD-HHMMSS` carries no offset, so a UTC server running Europe/Berlin routines skewed
  every run-ts-derived time). `registry.parse_run_ts()` now reads run-ts as UTC (was stamping
  the routine's tz, which could spuriously re-fire a `catchup: run_once` routine on a UTC
  host), and the web UI's `toDate()` parses run-ts as UTC and renders it in the **viewer's**
  local time — so run-ts and ISO timestamps finally agree. (AUDIT note; residual: the
  pre-`elapsed_s` fallback in `registry.read_run` still treats both stamps as naive — correct
  on a UTC host, a minor follow-up elsewhere.)

## [0.18.0] — 2026-07-15

### Added
- **Two conversation budgets, settable before the conversation starts.** The "New
  conversation" view now exposes **turns / reply** (`max_turns`, the per-reply window) and
  **whole conversation** (`max_total_turns`, a cumulative cap across every reply). The new
  `max_total_turns` budget (in `DEFAULT_BUDGETS`, `-1` = unlimited default) is enforced in
  `budget_violation`/`budget_warning` against the cumulative `ctx.turn` (restored across
  resume windows), so a conversation can be bounded as a whole while each reply keeps its own
  small window. `POST /api/conversations` accepts `max_turns`/`max_total_turns` form fields
  (AUDIT note).

## [0.17.0] — 2026-07-15

### Fixed
- **Conversation state diagram now lights the current state.** The Conversations tab's
  "state" rail parsed the converse workflow's single `conversation` phase, which is never
  written to `state/phase.json`, so no node ever highlighted (AUDIT note). The
  `/api/conversations/{slug}/stategraph` endpoint now returns a two-node reply-cycle graph
  (**working** ⇄ **waiting for you**) with the current node lit from the live run state, and
  the view re-lights it on every SSE state event.

## [0.16.0] — 2026-07-14

The changes that had accumulated since 0.15.0 without a version bump — collected here and
the version advanced (the gap this changelog was created to close). Three commits:
`4bf63bd5bd`, `56d620dbe3`, `c6ca03ffa8`.

### Added
- **Cost budget**: a `-1`-capable `max_cost` whole-dollar cap, enforced in
  `budget_violation`/`budget_warning`/`child_budgets`, reported in `status.json`, surfaced
  in the composer run-prompt and all three UI budget editors (`4bf63bd5bd`, user request).
- **Manual stop for conversation replies**: the conversation composer gains a live
  **“✕ stop”** (abort) button — the backstop that makes unlimited (`-1`) budgets safe
  (F41, `56d620dbe3`).

### Changed
- **Budgets honor `-1` = unlimited across the board.** Wall-clock time and the new cost cap
  join `max_total_tokens` (`4bf63bd5bd`); **turns** follow (`max_turns = -1`), guarded in
  `budget_violation`/`budget_warning`, inherited by children, reported as `turns_left=null`,
  and shown as “unlimited” in the run prompt (F42, `56d620dbe3`).
- **Conversation settings are editable during an active reply.** The permissions PUT no
  longer 409s while a reply runs — like budgets, it lands on the next reply (delete stays
  guarded) (F36, `56d620dbe3`).
- **Two permission layers are now bound** (D8): a gated action / reserved util / previous-run
  access survives only as the **means of a held conduct permission** — `grants.floor_capabilities`
  applies a raise-then-floor in `resolve_permission_layers`, so e.g. `write_util` can no longer
  be granted with `util-authoring` off. The `confirm` level and run-history depth remain user
  policy under it. The permissions panel gains the inverse cascade so it cannot express a
  contradiction. Enforcement still reads capabilities alone (fail-closed) (`c6ca03ffa8`).
- The live **state-graph diagram** now tracks recipes whose `state/phase.json` names the field
  `state` (not only the canonical `phase`) — executor, loop, and statemap accept either
  (F43, `56d620dbe3`).
- Inline decision/question **answer fields are multi-line** (`<textarea>`, Enter = submit,
  Shift+Enter = newline) across the run, conversation, chat and transcript surfaces
  (F38, `56d620dbe3`).

### Fixed
- **Answering a decision on a finished conversation now resumes it** so the answer is actually
  consumed. `POST /api/questions/{qid}/answer` is async and, after filing the answer, resumes a
  terminal conversation in place (`runner.resume(..., reason="converse")`, as `message()` does);
  the engine drains the answer at run start. Live replies and scheduled routines are untouched
  (F39, `c6ca03ffa8`).
- The Audit **“Note for the next run”** field resets after send — the draft is cleared before
  the view reloads, so form-persistence no longer refills it (F37, `56d620dbe3`).

## [0.15.0] — 2026-07-14

### Added
- **Playbooks**: a one-shot playbook library — the save/use-instruction analog for
  conversations (distil a conversation into a reusable, parameterized starting point)
  (`2b323c5`). Documented across CLAUDE.md, getting-started, and a new Help guide.

## [0.14.1] — 2026-07-14

### Fixed
- **claude-cli** pairs stream-json input with stream-json output for image turns (`3f89cf5`).

## [0.14.0] — 2026-07-14

### Added
- **Native multimodal input**: a `view_image` action, a per-endpoint capability flag, and a
  `vision`-util fallback for text-only main models — image/PDF input end to end (`551e3b6`).

## [0.13.1] — 2026-07-14

### Fixed
- **LLM task manager** orphans in-flight children when their process closes, instead of
  leaking them (`f202e2c`).

## [0.13.0 and earlier] — 2026-07-08 … 2026-07-14 — Initial development

> Versions were not tracked in commit subjects before 0.13.1, so this is a thematic
> reconstruction from git history (~170 commits over six days) rather than a per-release
> log. It records what was built, grouped by area.

### Engine & contracts
- Core engine: the action schema + schema guard, the turn loop, executor, composer,
  transcript, and inbox; a **fabrication guard** (a finish before any executed action is
  rejected).
- Direct endpoints only (openai / anthropic / claude-cli adapters) with guarded JSON parsing,
  reasoning-effort mapping, tenacity retries, and clean retries on empty completions.
- Weak-model robustness: constrained decoding / structured output on all endpoints
  (OpenRouter `json_schema`, Ollama native + `num_ctx`); tool-call envelope unwrapping; a
  repeat-streak/“provider grammar” rescue path with per-run schema-retry telemetry
  (`schema_retries` / `schema_forcefails`) in `status.json`.
- **Token efficiency**: prompt caching in all adapters, per-run claude-cli sessions, one-shot
  reminders, `edit_file` + batched reads, compaction on the tool-call model, honest usage
  accounting.
- **History compaction**: full context archived to a navigable, LLM-built set of markdown files.
- **Run control**: mid-flight model switch; resume an interrupted run where it left off;
  parallel sub-workflows (`spawn`/`subruns`/`kill`/`wait`) with lifecycle owned by the parent.
- **No-shell design**: a scheduler-managed global util library replaces a shell action; the
  catalog is discovered via `util list` and teaches parameters (a failed call teaches the
  correct one).

### Daemon & scheduling
- Registry (a filesystem-derived catalog, no database), cron scheduler, subprocess runner,
  systemd deploy; friendly scheduling UI (presets, auto timezone); boot-time missed-fire
  catch-up; a self-update restart sentinel (also human-droppable from Settings).

### Web console & UI
- Web backend (app / auth / SSE / APIs) and a mono-first, keyboard-first “signal-deck” console.
- Live transcript SSE with inject / pause / resume and a blocking-question flow.
- Hash-router URL state everywhere (log / library / run / routine / settings / wizard),
  per-navigation view containers, breadcrumb + setup banner.
- Dashboard overview with last-run cost/turns/tokens/duration per card (sortable, filterable,
  table view); a week strip of every scheduled routine’s fires; a Log tab; a Stats tab
  (usage/token/cost analytics with an API); an LLM task manager overlay.
- Global session-storage **form persistence** (inputs survive a refresh; per-qid draft keys).
- Mobile pass; browser notifications (tab-open Notification API + opt-in Web Push);
  syntax-highlighted Python editors; a source-generated Help/documentation tab.

### Workflow library, wizard & meta routines
- Library workflows as self-contained Python pattern files; an allowlisted `tools:` contract;
  one merged library repo (`libraries_home`) with a scheduled one-repo sync.
- Modular recipes: the routine is decomposed into `steps/` at generation while workflows stay
  single-file; a materialized main.md entry point with on-demand step modules.
- Wizard: background routine builds, resumable sessions (disk-persisted meta, list/detail/cancel),
  a clarifier that suggests and marries a workflow pattern to the task.
- Meta routines: **self-audit** (this routine), a **routine-improver** (five after-run
  improvement passes consolidated into a meta routine; targets the least-recently-run),
  **library-sync**, and **token-lab** R&D.
- Tagging system: editable tags (≥3) on routines/workflows/traits/utils with filter UI and
  reuse-first suggestions.

### Traits & permissions
- Split the old “fragments” into **traits** (practice prose, routine-owned) and **permissions**
  (enforced grants, user-owned).
- **Two-layer permissions**: conduct docs with a `requires:` mapping + per-routine capabilities
  (gated actions, reserved utils, write_util approval level, previous-run depth) with a
  cascading UI; enforcement reads capabilities alone (fail-closed).
- Self-modification is not a permission: a run never edits its own recipe/config unless a
  user-granted `fs_write_root` covers the routine dir (the improver’s case).

### Conversations
- An interactive, Claude-Code-like tab on the same engine harness: continuing a finished run is
  a follow-up (converse semantics), not crash recovery; paste images/files into the composer;
  header model line + budget editor; draggable/collapsible panes; an artifacts panel.

### Memory & decisions
- `.memory/` behind designated `memory_read`/`memory_write` actions, with an engine-maintained
  INDEX and default-on adoption at boot.
- One **Decisions** inbox for every required user feedback (plain asks, util approvals, audit
  decisions — meta-badged), timeout-continues-on-default, with a synchronized Discord surface;
  durable answered-markers stop answered decisions from re-surfacing.

### Budgets & telemetry
- Health-events JSONL logging for run failures, budget exhaustion, and orphaned runs.
- `max_total_tokens = -1` (unlimited) becomes the default for routines and conversations;
  ask-timeouts in minutes.

### Secrets, setup & deploy
- One central secrets store injected into utils/endpoints/claude at run time (utils declare
  what they need); paste API keys / Claude token in the UI; GitHub device-flow connect;
  first-boot bootstrap that secures a fresh deploy and provisions libraries.
- Docker image (runtime + bind-mounted state), `gh` wired at container boot, HTTPS via
  tailscale-serve documented; first-launch redirect to Settings until setup completes.

### Docs
- Full README and CLAUDE.md kept current with the engine loop, contracts, libraries, deploy,
  the traits/permissions world, prompt anatomy (drift-guarded), and worked Help examples.

