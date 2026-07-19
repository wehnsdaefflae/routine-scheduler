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

## [0.72.0] — 2026-07-19

### Added
- **Schedule-once UI card (D28) — the frontend for the 0.71.0 one-shot backend.** The routine
  page now has a **Schedule once** card beside Triggers: a local-time datetime picker + reason
  field arms a one-shot (`POST /api/routines/<slug>/schedule-once`, the naive local time is
  converted to an absolute UTC instant client-side), the armed one-shots list with a Cancel
  button (`DELETE …/<id>`), and the daemon fire ledger (`fired N× · last …`).
  `static/components/schedule-once.js`, wired into `static/views/routine.js`.
- **Armed one-shots on the dashboard week strip.** `GET /api/schedule/week` now returns a
  `one_shots` list per routine (armed schedule-once fires inside the window) alongside the
  recurring cron `fires`, and includes a routine that has *only* a one-shot armed (no cron).
  The week grid renders one-shots as distinct **hollow** dots. `web/api_schedule.py`,
  `static/components/weekgrid.js`, `static/views/dashboard.js`.

### Tests
- `tests/ui/test_schedule_once.py` (Playwright): a seeded one-shot renders, Cancel clears the
  spool request, and arming from the UI writes a new request. `test_schedule_once.py` gains a
  week-strip API test (armed one-shot surfaces in `one_shots`; a far-out one does not).

## [0.71.0] — 2026-07-19

### Added
- **`schedule_run` action + `scheduling` permission — one-shot future runs (D27).** A routine
  holding the new `scheduling` capability can arm a routine to run ONCE at a future instant,
  then never again — the missing case between cron (repeats forever) and a manual run (now).
  `schedule_run` takes `target` (routine slug; **self-target always allowed**, another routine
  is the cross-routine case the permission authorizes), `fire_at` (an absolute ISO-8601 UTC
  instant or a relative offset like `+3d` / `+2h` / `+30m`), and `reason` (injected into the
  target's inbox just before it fires); `cancel: true` (+ optional `id`) calls it off.
- **Daemon-owned request spool + `OneShotManager`.** Armed one-shots live in
  `<routines_home>/.control/schedule-once/<slug>/req-*.json` (NOT `routine.yaml` — config
  stays the user's; the engine writes the spool un-sandboxed like `write_util`). A new
  `OneShotManager`, ticked beside `TriggerManager` after the cron loop, fires each due request
  ONCE (same draining/one-run-per-routine gates as cron/trigger fires) then **deletes** it —
  consumption is the non-repeating guarantee (no self-disabling cron, no config rewrite). A
  missed one-shot make-up-fires on the next daemon start; `expires_at` bounds staleness.
- **API:** `POST` / `GET` / `DELETE /api/routines/<slug>/schedule-once` — arm, list armed +
  fire ledger, and cancel from the routine page (the user path beside the routine's own arming).
- Full design + rationale: `docs/schedule-once.md`.

## [0.70.1] — 2026-07-19

### Fixed
- **New-routine draft field no longer refills with the last routine's task (F110).** The
  `#/new-routine` task textarea is form-persisted (a half-typed task survives a refresh —
  desired), but `static/views/new-routine.js` never forgot the draft once a clarification
  started, so the next visit restored the previously-created routine's text. It now calls
  `forgetField(ta)` on a successful start (the documented submit-then-forget pattern), so the
  field is empty on the next visit while still surviving plain navigation. Covered by
  `tests/ui/test_flows.py::test_new_routine_draft_is_forgotten_after_start`.

## [0.70.0] — 2026-07-19

### Added
- **`remove_util` action — routine-executable util curation (D25).** The engine gains a
  `remove_util` action mirroring `write_util`: a routine holding the **util-authoring**
  capability can now DELETE a global util, not just create/revise one. Like `write_util`,
  the removal runs un-sandboxed engine-side (`utils_lib.remove_util_file`, committed so it is
  recoverable from git history) — the counterpart the library previously lacked, which left
  removal only to the web UI or a host shell (F108: the util sandbox jails the library dir for
  every routine, even `shell`). The action **refuses** while any other util still declares the
  target on its `calls:` line (`utils_lib.referenced_by`, mirroring the `gu remove` no-callers
  guard), asks for approval unless the routine's write_util policy is `never`, and is declined
  for sub-workflows. Gated as a new `GATED_KIND` sourced from `util-authoring` (the permission
  doc's `requires.actions` now lists `write_util, remove_util`); stripped from detached tasks
  like `write_util`. Covered by `tests/test_remove_util.py` (helper, validation, capability
  gate, and the remove / refuse-callers / missing / subrun-decline handler paths).

## [0.69.1] — 2026-07-18

### Fixed
- **Audit page now renders the report's own markdown (F105).** `static/views/audit.js`
  never imported `md.js`, so a finding/decision `detail`, the top summary, and changelog
  entries showed their block markdown (lists, `code`, tables) as literal pre-wrapped text —
  the same gap F104 fixed on the Decisions page. Those four prose surfaces now render via
  `md()` (the sanctioned HTML-escaped innerHTML path); `F/D` ref-links still linkify through
  the rendered output. Covered by `tests/ui/test_flows.py::test_audit_detail_renders_markdown`.

## [0.69.0] — 2026-07-18

### Added
- **New "Summary" tab — each routine's latest finish message in one glance surface.** A
  sibling to the Decisions inbox (which collects what the routines need *from* you); Summary
  collects what they last *said* — the most recent run's finish summary per routine, newest
  first, with the finish markdown rendered (`md()`), a jump-to-run link, and a per-item
  mark-read control. Dismissing an item persists under `routines_home/.control/
  summary-read.json`; a newer run of that routine automatically resurfaces it. New route
  `#/summary` + `static/views/summary.js` + nav/breadcrumb, backed by a new read-only
  `GET /api/summary` and `POST /api/summary/{slug}/read` (registry read-model). The
  Decisions/`#/questions` inbox is unchanged. (Reviewer decision D21, option A.)

## [0.68.3] — 2026-07-18

### Fixed
- **util-stats snapshot failure was silent — the ACTUAL F97 root cause is a filesystem
  permission, not a `util_stats()` raise.** Proven this run by running the daemon's own venv
  (`/opt/rsched-venv/bin/python`, v0.68.2): `/home/mark/.local` is owned `root:root` (mode
  755), so the daemon (uid 1000 `mark`) cannot `mkdir ~/.local/state`; the snapshot write
  raises `PermissionError` — which **is an `OSError`** and was swallowed by
  `write_util_stats_snapshot`'s `except OSError: pass` with no log. Every util_stats-internal
  fix across 0.68.0–0.68.2 was treating the wrong layer. The real fix is operational (`chown
  mark:mark ~/.local`); code-side, the writer now leaves a `log.warning` breadcrumb naming
  the unwritable path so this class of misconfiguration is never silent again.
- **Markdown in Decisions-page items now renders.** `static/views/questions.js` rendered an
  OPEN question's text as raw `textContent` (and an answered one inline-only), so a meta
  (self-audit) decision's rich `detail` — lists, GFM tables, `code` — showed literal markup.
  Meta decisions now use the block renderer (`md()`); ordinary short prompts keep the
  inline-only subset (`mdInline()`). Reviewer-reported 2026-07-18.

## [0.68.2] — 2026-07-18

### Fixed
- **util-stats snapshot STILL never materialized — the real F97 root cause.** 0.68.1 only
  guarded a corrupt *transcript*, but the snapshot dir (`~/.local/state/routine-scheduler/`)
  never existed at all on the deployment even after two qualifying root-run finishes under
  0.68.1. Cause: `write_util_stats_snapshot` evaluates `util_stats(server)` *before* its I/O
  guard, and `util_stats()` still raised on a home it could not enumerate — `_backfill`
  iterates BOTH `routines_home` and `conversations_home`, and its per-home directory walk
  (`iterdir`/`stat`) was unguarded (a routines_home-only repro never exercised it). Two
  fixes: (1) `_backfill` now wraps each home's enumeration in `try/except` (skip+log a home
  it cannot read, keep the other home's counts); (2) `write_util_stats_snapshot` wraps the
  `util_stats()` call so any compute failure still writes a degraded, `error`-marked
  snapshot — the file (and its parent dir) is ALWAYS created, making the residual observable
  next run instead of a silent absent file. Tests:
  `test_backfill_tolerates_unreadable_home`, `test_write_snapshot_degrades_when_util_stats_raises`.

## [0.68.1] — 2026-07-17

### Fixed
- **util-stats snapshot no longer silently disappears when one transcript is corrupt
  (F97).** The run-finish hook (`engine/runtime.py`) that refreshes
  `util-stats.json` swallows every exception so telemetry can never break a run — but
  `util_stats()` computed the whole snapshot *outside* the write's own guard, so a single
  unreadable/corrupt transcript raised straight through the hook and produced **no snapshot
  at all** (the file stayed missing after several qualifying root-run finishes). `_backfill`
  now wraps each `_scan_transcript` in try/except: a bad transcript is skipped and logged,
  every other source still counts. The swallowed-exception `pass` in the runtime hook is now
  a `log.warning(..., exc_info=True)` so a future failure leaves a breadcrumb instead of
  vanishing silently.

### Changed
- **Default `ask_timeout_min` raised 5 → 480 (8h), the deployment norm (F102).** The old
  5-minute default seeded a blocking-ask timeout trap into every newly-created routine — a
  blocking question would auto-continue on its stated default after only 5 minutes. It
  recurred twice (`scheduler-improvement-research`, `global-utils-review`), each hand-fixed
  by the user, who approved raising it deployment-wide (config-optimizer
  `q-20260717-191914-24`). All mature routines already run 480; this fixes the root cause for
  future routines. Existing `routine.yaml` files are engine-sealed to runs and unchanged.

## [0.68.0] — 2026-07-17

### Added
- **Persisted util-stats snapshot — one source of truth for the Stats tab and routines
  (F97).** The per-util execution stats the Stats tab shows (`util_stats()`: library git
  dates + the durable workflow-usage stream + transcript backfill) are now written to
  `$XDG_STATE_HOME/routine-scheduler/util-stats.json` (default
  `~/.local/state/routine-scheduler/util-stats.json`) on every root-run finish, via the new
  `util_stats.write_util_stats_snapshot(server)` (atomic, best-effort — a stats write never
  breaks a run). The XDG state location is deliberate: a Landlock-jailed util subprocess can
  read `~/.local/state` but not the daemon's `routines_home/.control` area, so this is the
  one place a routine's util can reach the same numbers the web page computes. Unblocks the
  `global-utils-review` (util-improver) routine, whose first run stalled with "stats source
  UNRESOLVED" because the figures were reachable only through the token-gated `/api/stats`.
- **`util-stats` global util** (library) reads that snapshot and emits it (`--json` for a
  routine to consume, a table for humans, `--name` to filter one util) — the review
  routine's stats source.

## [0.67.4] — 2026-07-17

### Fixed
- **Run-page question form now updates when a run re-asks within the same phase (F93).**
  The run SSE tail (`web/sse.py`) emitted a `state` event only on a `(state, phase)` change,
  so a NEW pending question with unchanged state+phase never reached an open run page — the
  question form (which re-renders only on a `state` event) could keep showing a stale/absent
  form, forcing answers onto the Decisions page. The dedup key now also includes the pending
  question's `qid`, so a changed (or cleared) question always rides its own event.

### Added
- **`.ui-traces` diagnostics for the new-routine clarify run page (F93).** The setup panel
  records which stage it renders (`setup-stage`, with run state + `has_result`) and the run
  view records real transitions of the shown question id (`run-question`) — so a clarify run
  reported stuck on the chat frame (no create form) or missing its question form leaves a
  diagnosable trail for the self-audit's improve-ui lens.

## [0.67.3] — 2026-07-17

### Fixed
- **Settings → LLM endpoints: the system-model description now states its role-fallback
  behaviour.** The blurb described the system model only as the fallback for "setup-time
  work that isn't a routine yet" (the clarify wizard + workflow generation), omitting that
  it is ALSO the fallback for any routine role (`main`/`subroutine`/`tool_call`) left unset
  — which `config.py`, `EndpointRegistry.for_model`, and `docs/endpoints.md` all document.
  It now says so, and points at the separate per-model `fallbacks` failover chain, so the
  two fallback mechanisms aren't confused. UI-text accuracy only; no behaviour change.

## [0.67.2] — 2026-07-17

### Fixed
- **A conversation now sees its own task in the system prompt.** `build_system_prompt`
  appended the `# INSTRUCTION` section only at `depth > 0`, and the depth-0 ownership prose
  declared the WORKFLOW the "single source of truth for what to do". But a **conversation**
  runs at depth 0 while its task IS its first message (`instruction.md`) and the `converse`
  workflow only defines HOW to work a reply — so the agent was handed the converse pattern
  with its actual task dropped from the prompt, and on the first turn had to go hunting for
  `instruction.md` to understand what it was even asked to do. The composer now detects a
  conversation by HOME (its dir under `conversations_home`, matching `daemon.runner`, since
  the yaml `kind: conversation` is dropped by pydantic), carries the `# INSTRUCTION` section
  for it, and gives it conversation-specific ownership prose that names `instruction.md` as
  the task, frames later user messages as refinements of it, and preserves multi-turn /
  sub-work replies. Scheduled routines are unchanged (their task stays compiled into the
  recipe). `docs/prompt-anatomy.md` updated to match. Reported via the audit feedback channel.

## [0.67.1] — 2026-07-17

### Fixed
- **Dashboard routine card no longer counts snoozed questions as open.** The card's
  "N open questions" count (`web/api_routines.py`) ignored `snoozed_until`, so a question
  snoozed into the future showed as an open question on the card while the Decisions tab
  badge and the Decisions page — which hide snoozed items by design — showed nothing; the
  two surfaces disagreed. The card now derives both `open_questions` and `decision_backlog`
  through the same snooze-aware filter (`_awaiting_questions`, reusing `_snooze_active`), so
  a snoozed decision stays quiet everywhere and the card count can never contradict the
  badge. Reported via the audit feedback channel.

## [0.67.0] — 2026-07-17

### Changed
- **The `meta` tag is now a plain tag — no special-casing.** Previously `meta`-tagged
  workflows were hidden from the spawn/subtask capability catalog and from wizard
  suggestions, meta routines were hidden on the dashboard by default, and the `meta` tag was
  sorted first and styled specially. Now: meta workflows appear in the spawn catalog
  (`engine/capabilities.py`), in the wizard clarifier's candidate patterns
  (`web/wizard_store.py`) and in `suggest()` (`workflows/suggest.py`, the `INTERNAL_TAG`
  filter is gone); the dashboard no longer hides meta routines by default and sorts/styles
  the tag like any other (`static/views/dashboard.js`, `library.js`, `util.js`). Bundled meta
  routines still install **disabled** on a fresh instance (a seed-install safety default, not a
  tag behaviour — enable each on its routine page). Self-audit decision D15.

## [0.66.1] — 2026-07-17

### Fixed
- **`rsched lint` works under the util sandbox.** The 0.63.0 Landlock sandbox deliberately
  hides `~/.config/routine-scheduler/` (secrets live there), so `rsched lint` — which called
  `load_server_config()` only to find `libraries_home` — crashed with `PermissionError` when
  invoked from a sandboxed util (e.g. the `gu rsched-lint` helper self-audit uses). `lint`
  now accepts `--libraries-home DIR` to lint a library directly, skipping the server-config
  read; the library dir itself is already visible to utils (it is `utils_home`). Self-audit
  decision D16.
- **Restored the green test gate**: 0.66.0's new per-util telemetry (`ctx.count_util`) had
  broken a `tests/test_utils.py` fixture whose fake context lacked the method.

## [0.66.0] — 2026-07-17

### Added
- **Outcome-gated self-improvement: recipe-version health + one-click roll-back.** Every
  run is stamped with the recipe VERSION that produced it — the last commit touching
  main.md / stages/ / traits/ / tuning.yaml (`rsched/recipes.py`), never the state-noise
  HEAD; uncommitted recipe edits (the routine-improver's) are snapshotted into a
  recipe-only `recipe: pre-run snapshot` commit at run start, so every version is a real,
  revertable commit. The stamp lands in status.json (`recipe_commit`) and the durable
  workflow-usage record, so health history outlives run retention. The routine page's new
  **Recipe health** section (`GET /api/routines/{slug}/health`, `rsched/run_health.py`)
  buckets runs by version — outcomes, fail rate, median turns/tokens, deferred-question
  churn (`asks_deferred`, engine-counted) — with pre-stamp history date-attributed and
  marked `date-mapped`. A deterministic regression heuristic (no stats libraries; every
  constant justified in the module: 5-run windows, ≥3 runs to judge, fail-rate +0.4,
  1.5× median growth with +5-turn / +20k-token floors) flags the newest recipe change
  when its runs are clearly worse. **Flag-first**: the roll-back is the user's click
  (`POST /api/routines/{slug}/recipe/revert`) — it restores ONLY the recipe files as a
  new commit (never routine.yaml or state), 409-guarded while a run is active; the
  routine-improver never auto-reverts.
- **Per-util execution stats on the Stats tab.** Every util call is counted by outcome in
  the engine (`RunContext.util_stats`): ok / error / usage_error (exit 2 — argparse's
  bad-arguments convention) / missing / denied / rejected. Denials are counted at the
  validation seam (`engine/actions.util_rejection_outcome`) — a denied call is corrected
  inside the schema-retry cycle and never becomes a turn, so the executor alone would
  never see it; user slash commands count identically; `list`/`show` discovery never
  counts. The per-run breakdown rides status.json and the workflow-usage record (`utils`
  payload extension — always present on new records, marking the run as counted).
  `rsched/util_stats.py` joins that stream with the library's git history (created / last
  revised per util, one memoized `git log` walk) and a stat-fingerprint-memoized
  transcript backfill for pre-stream runs. The new **Global utils** table answers, per
  util: exists since when, last revision, how often executed / successful / mis-called /
  permission-blocked, first & last execution — honest about unknowns (never-executed
  utils, pre-stream rejection history).

### Docs
- New Help guide `docs/run-analytics.md`; CLAUDE.md (routines-on-disk + workflow-usage
  paragraphs) and README updated.

## [0.65.0] — 2026-07-17

### Added
- **Per-model output `max_tokens` in the catalog.** `ModelConfig.max_tokens` (and an
  `EndpointConfig.max_tokens` default it inherits) resolves into `ModelRef.max_tokens`,
  with a generous engine fallback (`DEFAULT_MODEL_MAX_TOKENS` = 16,384). Every engine call
  site — turns, the `llm` action, compaction archival, refusal referral — now sends the
  resolved per-model value instead of a hard-coded 16,384; `claude-cli` maps it to
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. Settings surfaces an audit flag (`max_tokens_warning`) on
  any model whose limit is unset (riding the generic default), implausibly low (< 4,096), or
  larger than the model's context window — so "every model has its max tokens set correctly"
  is auditable at a glance, mirroring how unset secrets are flagged.
- **Ordered model failover chains with provider cooldowns.** A catalog model may declare
  `fallbacks:` — an ordered list of catalog model names (non-transitive) the engine fails
  over to when the model fails hard (its transport retries are exhausted, or the error was
  never retryable). `routine.yaml` still maps each role to ONE catalog name — editing a
  catalog model's chain updates every routine that references it, so no config-shape
  migration. Two cooperating levels (`endpoints/failover.py`): a hard `EndpointError` marks
  the `(endpoint, provider model id)` *cooling* for 5 minutes (centrally, in
  `InstrumentedEndpoint` — the one seam every LLM call crosses), and every role resolution
  (`for_model` / `for_uncensored` / `for_system`) picks the first not-cooling chain member;
  the turn-completion seam (`engine/completion.py`) additionally advances down the chain
  MID-TURN on a hard failure. The switch is logged visibly as a transcript `error` event
  carrying a `failover` payload (`from` / `to` / `cooldown_s`) — a payload extension, not a
  new event type — and each turn's `usage.model` records the model that actually served it,
  so spend attribution and `status.json`'s live model stay truthful. Chain exhausted → the
  run fails exactly as before; models without `fallbacks` behave exactly as before.
- **Settings credential-source indicator.** Each endpoint card now shows which rung of the
  credential ladder is live — inline key / secret `<VAR>` / env file / none — and warns
  loudly when an inline key **shadows** a set secret (the inline key wins, so editing the
  secret changes nothing until it's removed). Computed by label-only mirrors
  (`api_key_source` / `token_source`) sitting beside the resolvers they track; key values
  are never returned through the API. The documented precedence (inline → secret → env file)
  is unchanged.

## [0.64.0] — 2026-07-17

### Added
- **Instance-wide full-text search.** One box in the app header (`/` or Ctrl-K) over
  everything the instance ever wrote — run transcripts (say/note narration, finish
  summaries, questions + answers, user messages; gzipped archives and subrun trees
  included), result.md reports, compaction `history/` archives, LEDGER.md, `.memory/`
  notes, durable decision records, and recipe files — across routines AND conversations.
  Hits rank by BM25 (porter stemming, so `playbook` finds `playbooks`), group by
  routine → run with snippet-highlighted matches, and deep-link into the run /
  conversation / decisions / routine views. Backend: an SQLite FTS5 index (stdlib
  `sqlite3`) at `<routines_home>/.control/search.sqlite3` — a pure cache of the flat
  files (delete it, it rebuilds), kept fresh behind per-file stat fingerprints (newest
  runs first, budget-bounded passes with a per-pass progress guarantee) by a daemon
  maintainer task plus a ~2s query-time top-up; rows for retention-pruned runs are
  pruned. Raw FTS5 syntax passes through when it parses; anything else falls back to
  escaped phrase terms — a malformed query is a 400, never a 500. New: `search/`
  package, `web/api_search.py` (`GET /api/search?q=`), the header
  `components/searchbox.js` (compact icon at rest, expands over the nav on focus),
  docs/search.md.

## [0.63.0] — 2026-07-17

### Added
- **Util-subprocess sandbox (Landlock).** Every util now runs inside a Landlock jail
  (`rsched/landlock.py` — a stdlib-ctypes binding + strict child wrapper; `rsched/sandbox.py`
  — the policy layer) whose visible filesystem is derived from the run's permissions: the
  routine dir + its `fs_read_roots`/`fs_write_roots` read/write, plus the toolchain a util
  needs to execute (interpreter, uv + its caches, the util library, system trees). The
  daemon-user HOME — `~/.config/routine-scheduler` (the secrets store), `~/.credentials`,
  `~/.ssh` — is invisible, closing the `gu page-fetch file:///…/secrets.env` read-and-exfil
  bypass. Verified working inside the production Docker container (Landlock ABI 4, filesystem
  + TCP, default seccomp). New server config `sandbox: strict | permissive | off` (default
  **permissive**: jail when the kernel supports it, warn + run bare when it doesn't; strict
  refuses to run utils unsandboxed). See docs/sandboxing.md.
- **Network as a declared util capability.** The util docstring header gains a required
  `net: outbound | none` line (undeclared = none — no TCP); the sandbox denies all TCP
  (Landlock ABI ≥ 4) to a util that declares none. Sibling calls declared on `calls:` resolve
  network + secret needs transitively.
- **Scoped secrets injection.** A util subprocess now receives ONLY the store secrets it (or
  a `calls:` sibling) declares on `secrets:`; every other store key is scrubbed even out of
  the inherited daemon environment (applies in every sandbox mode, no kernel needed). Secret
  detection now also resolves the `VAR = "NAME"` + `os.environ[VAR]` indirection.
- **Never recreate a user-deleted util.** `write_util` for a slug with a deletion in the util
  library's git history is rejected inside the schema-retry cycle (never a turn); the model
  must `ask_user` first and an explicit yes that run unblocks it (`interact.recreate_denial`).
  The boot seed-sync likewise never resurrects a user-deleted seed util.

### Changed
- `utils_lib.run_util` / `selftest` take a `SandboxPolicy`; `header_problems` requires the
  `net:` line; the util-authoring permission doc + prompt CAPABILITIES note the new rules.
- One-shot boot migration (`MIGRATION(expires=2026-08-17)`) stamps pre-sandbox library util
  headers with `net: outbound` (behavior-preserving) + any missing `calls:`/`secrets:`.

## [0.62.0] — 2026-07-17

### Added
- **Event-driven routine triggers (webhook path).** A routine can now fire on an external
  event alongside cron, via a new canonical `triggers:` list in routine.yaml (one shape from
  day one: `{id, type, cooldown_s, …}` — `webhook` implemented, `imap`/`watch_path` reserved
  so the mail/file-drop watchers slot in later without reshaping config). The webhook path:
  `POST /api/hooks/<slug>/<token>` (`web/api_hooks.py`) is the one deliberately
  unauthenticated API route — the per-trigger, server-generated URL token IS the auth
  (constant-time compare, generic 404 with no existence oracle, 64 KiB streaming size cap,
  per-slug rate limit + durable spool cap, payload never echoed, rejections logged). The
  handler only RECORDS events durably in the `.control/triggers/<slug>/` spool; the
  scheduler-ticked `TriggerManager` (`daemon/triggers.py`) turns them into fires, so
  one-run-per-routine, `max_concurrent_runs`, and the restart drain stay the daemon's job.
  **Coalescing**: N events while a run is active/queued/cooling → ONE fire, each event still
  landing as its own inbox message (deterministic filenames → exactly-once across crashes);
  `cooldown_s` (default 60) bounds trigger-fire frequency so a leaked URL can't burn budget.
  A **Triggers card** on the routine page (`static/components/triggers.js`) creates/deletes
  webhooks, copies the hook URL, and shows the per-trigger fire ledger. The library-sync
  export now redacts webhook `token` values in routine.yaml. See `docs/triggers.md`.

## [0.61.0] — 2026-07-17

### Added
- **Run-history heartbeat strip on the dashboard.** Every routine card AND list-view row
  now carries a compact SVG strip of the last 15 runs (`static/components/heartbeat.js` —
  the symmetric PAST view to the week grid's future fires): green ok / amber partial /
  red failed / grey aborted / teal still-running bars, oldest left, newest at the right
  edge, bar height tracking the run's token spend (sqrt-scaled per strip). Hover shows
  ts · outcome · turns · tokens · cost · duration; click opens that run. A routine that
  failed 4 of its last 10 runs no longer looks identical to one green for a month.
  Data path: cards gain an additive `recent_runs` field (`web/api_routines.py`
  `HEARTBEAT_RUNS_N` — a slice of what the registry already parses, no new scanning), and
  status.json gains the additive **`outcome`** field (ok|partial|failed|aborted, stamped
  at run end by the engine) because `state` folds a partial finish into "finished" — the
  strip is where partial becomes visible again.
- **GFM pipe tables + blockquotes in model-authored prose.** `static/md.js` (the one
  sanctioned innerHTML pathway) now renders pipe tables — header row + `|---|` separator
  → `table.list` in a `.tablewrap`, `:---:`/`---:` alignment honored, `\|` escapes, a
  malformed table stays literal text — and `>` blockquotes (grouped, nested via re-parse,
  recursion depth-capped) on BLOCK surfaces: finish summaries, llm replies, artifacts.
  The escape-first security structure is unchanged (everything HTML-escaped before any
  transform; no live HTML); `mdInline` (say narration, questions) stays inline-only.
  The models are TOLD: the composer's finish gloss and the ACTION_SCHEMA `summary`
  description now state that pipe tables and blockquotes render — so tabular results
  arrive as real tables, not ASCII art (`docs/prompt-anatomy.md` and its pin test move
  in the same commit).

### Fixed
- `tests/ui` `test_routine_page_saves`: the tag-removal disk assert waited a fixed 200ms
  — now an explicit poll on the yaml state (`_wait_until`), per the standing
  fix-flakes-with-render-waits rule.

## [0.60.0] — 2026-07-17

### Added
- **⚙ capabilities & budgets on the new-conversation composer.** The same panel the
  conversation header offers now exists BEFORE create — necessary because the first reply
  fires on create, so a permission (e.g. shell), per-reply budget (minutes/tokens), or
  deliberation level toggled post-hoc would miss reply #1. Fed by the new
  `GET /api/conversations/defaults`; the collected `{active, capabilities}` payload rides
  the create request through the same resolve + cascade + floor as the header save, and
  `deliberation` lands in tuning.yaml. The old "⚙ options: project dir, shell" block (and
  the `shell` create form field) is retired — shell is now just one toggle in the panel.
  `permissionsPanel` returns `{node, value}` so it can collect without saving.
- **Audit references are hyperlinks.** Every `F63`/`D14` mention in the audit report's
  prose (summary, findings, decisions) and in the Decisions page's meta items links to the
  card it names: `#/audit?focus=<id>` lands on, scrolls to, and flashes that card
  (`static/components/reflinks.js`; decisions now render read-only cards on the Audit page
  so D-references have a landing target).

## [0.59.0] — 2026-07-16

### Changed
- **The run page is the whole new-routine setup surface (D11 UI half, completing the
  wizard unification).** The bespoke wizard views (`static/views/wizard.js`,
  `static/views/wizard-create.js`, the `#/wizard` route) are retired. A clarify session —
  a real run of the protected `clarification` routine since 0.58.0 — now renders at
  `#/run/clarification:<ts>` like any other run, with a new setup panel
  (`static/components/setuppanel.js`) mounted on top: a slim chat frame (cancel setup)
  while the clarify run is live, then the suggest → create → build stages as run-page
  panels once it finishes. `#/new-routine` (`static/views/new-routine.js`) keeps only the
  draft form plus the in-flight-session resume list; the setup banner, the Decisions
  page's wizard items, and the resume links all point at the run page. `/api/wizard/start`
  and session snapshots return the session's `clarify_run_id` for that navigation.

### Fixed
- **Decision answers for a live clarify run now reach the session** (the missing sibling
  of 0.58.1's inject/converse fix). Answering a clarify ask through
  `POST /api/questions/{qid}/answer` (run page, Decisions page) — and deferring it — wrote
  to `clarification/inbox`, which the live session never polls; both now route to the
  `.wizard-<ts>` workspace inbox via `api_questions._record_dir`, and the answered-state
  derivation reads the same dir.
- **A clarify ask no longer lists twice on the Decisions page.** Since 0.58.0 the same
  blocking question surfaced once via the clarification routine's active run and once via
  the workspace's durable pending record; the wizard scan now dedupes against the real
  run (and stamps items with the clarify `run_id`, badged `wizard`, linking the run page).

## [0.58.1] — 2026-07-16

### Fixed
- **Run-page messages to a live clarify session now reach it** (self-audit D13=B follow-up).
  A clarify session (0.58.0) is a real run whose artifacts live at
  `clarification/runs/<ts>`, but the engine executes it in the hidden throwaway workspace
  `.wizard-<ts>` and polls THAT dir's inbox. `POST /api/runs/clarification:<ts>/inject`
  and `/converse` derived the inbox as `run_dir.parent.parent/inbox` =
  `clarification/inbox`, which the live session never polls, so a run-page message was
  silently dropped. New resolver `wizard_store.session_inbox_dir` redirects a clarify
  run's message to the `.wizard-<ts>` workspace inbox when that workspace exists; ordinary
  routines and legacy session-local clarify runs fall through to `routine_dir/inbox`
  unchanged. (`answer` already routed correctly — the wizard question carries the
  workspace dir name.)

## [0.58.0] — 2026-07-16

### Changed
- **Clarify sessions are now REAL runs of the `clarification` routine** (self-audit D13=B,
  first slice). `wizard_store.create_session` lands the run at
  `routines_home/clarification/runs/<ts>` — a valid `clarification:<ts>` run id with no
  dotfile bridge — and stamps the session's `routine.yaml` with the clarification slug so
  the engine composes that id in status/transcript/usage. `engine-run` gained a `--run-dir`
  override (artifact dir decoupled from the throwaway session workspace, which stays
  hidden as before); `_clarify_run_dir`, cancel/abort, the LLM-sidecar tailer and
  finalize's provenance copy all resolve through the new `wizard_store.clarify_run_dir`.
  Standard run surfaces now apply to clarify chats: the run page (`#/run/clarification:<ts>`),
  SSE tail, transcript paging, registry/dashboard listing, and orphan recovery. Legacy
  sessions and deploys without the template keep the old session-local layout (fallback).
  Remaining slices: run-page panels replacing wizard.js/wizard-create.js, and routing
  run-page *inject* to the session workspace inbox.

## [0.57.2] — 2026-07-16

### Fixed
- **Decision-card option buttons no longer overflow right on narrow screens** (self-audit
  F80). A full-sentence option (e.g. the wizard-unification decision's option B) rendered
  as a single `.btn` with `white-space: nowrap`, so a long label ran off the viewport even
  though the `.row` container already wraps between buttons. New rule
  `.answer-opts .btn { white-space: normal; max-width: 100%; text-align: left }` lets the
  label wrap inside the button and cap at the container width. The shared `answerForm`
  options row is tagged `.answer-opts`. Guarded by a 400px-viewport UI test asserting the
  option button's right edge stays within the question card.

## [0.57.1] — 2026-07-16

### Changed
- **Test suite: 3× faster, +12 behavior tests, coverage 84.8% → 88%** (user order). Speed
  came from diagnosis, not skipping: (1) the app lifespan's pdoc docs build is a to_thread
  task shutdown can only AWAIT — every TestClient/uvicorn test paid ~3s teardown and one
  test a 19s rebuild; `RSCHED_SKIP_DOCS_BUILD` (set suite-wide in conftest, cleared by
  test_docs_build) removes it. (2) `with_retries`' 1s/2s backoff clock is now
  `RSCHED_RETRY_BASE_DELAY`-tunable at call time — dead-endpoint tests exercise the retry
  logic without sleeping (test_with_retries_backoff pins the production delays). (3)
  pytest-xdist `-n auto` is the default (`-n0` for serial); the suite is hermetic per test.
  Wall clock: 224s → ~70s (110s with coverage). New meaningful tests: the CLI command
  surface (validate/abort/lint/suggest/scaffold/run-once exit codes, printed diagnostics,
  disk effects — cli.py 37%→~90%), the executor's real `uv run` util seam incl. the
  grants-aware failure/repair-hint contract, and the playbook edit/detail/delete routes
  (lint-gated PUT, honest 404s). Coverage ratchet raised: fail_under 84 → 87.

## [0.57.0] — 2026-07-16

### Added
- **The note channel** (user order): any action may carry an optional `note` — 1-3
  SELF-CONTAINED lines worth keeping beyond the context window (a confirmed finding, a
  dead end, a fallback plan, an unresolved doubt). The engine (`engine/notes.py`) appends
  it to `state/notes.md` at **no turn cost**, stamped `[run · turn · phase · action]` —
  the stamp is an address into the transcript/history archive where the note's full
  context permanently lives; the contract demands self-containment (the same boundary
  discipline as subrun briefs and finish summaries). Rationale: the one-action-per-turn
  contract priced every dedicated write at a full turn, so insights died with the window
  (bookkeeping deferred under budget pressure, end-of-run writes as reconstructions);
  this is the capture tier under the existing curation tier — `memory_write` keeps its
  turn price as the memory INDEX's quality gate. The state digest carries the file's
  tail into the next run (the full file stays on-demand); notes.md remains ordinary
  prunable state (the improver's hygiene lens treats an un-understandable note as
  broken). `think-on-paper`'s standing paragraph now rides this channel, so the top
  deliberation stop no longer costs an extra turn per decision. The transcript renderer
  shows captured notes as 📌 lines in the turn box.

## [0.56.1] — 2026-07-16

Self-audit (first slice of the D11 wizard→run-page unification: backend structure).

### Changed
- **`api_wizard.py` split into a three-module wizard package (F63 budget).** The 355-line
  route file (over the ~350-line one-responsibility budget) is now three files sharing one
  `APIRouter`: `wizard_common.py` (the router + the helpers both halves use —
  `_wizard_pid`/`_center`/`_wizard_recorder`/`_stop_tailer`/`_wizard_dir`/`_clarify_run_dir`),
  `wizard_sessions.py` (session lifecycle + the clarify-chat stream: list/detail/cancel/start/
  events/transcript/answer), and a slimmed `api_wizard.py` (the build half: suggest/
  generate-workflow/finalize + `_build_routine`). `app.py`'s `api_wizard.router` include is
  unchanged (the router is re-exported); `scaffold`/`suggest_tags`/`FinalizeBody`/
  `_build_routine` stay importable off `api_wizard` for the tests. Pure structure — no route,
  payload, or behaviour change; full suite green 840/3. This is slice 0 of the wizard→run-page
  unification (audit D11): the session/clarify half is now cleanly separated from the build
  half, the seam the frontend unification lands along.

## [0.56.0] — 2026-07-16

### Changed
- **`tuning.yaml` — the deliberation carve-out redesigned away** (user order, same-day
  design review of 0.55.0): `deliberation` was behavior mis-filed in the authority file.
  It now lives in `tuning.yaml`, a new per-routine document for machine-tunable BEHAVIOR
  parameters, classed with the RECIPE — writable under the existing `recipe_unlocked` rule
  (the improver's fs_write_root), so the FILE boundary is the permission boundary again.
  Deleted: `GrantPolicy.config_tunable` and the executor's yaml semantic-diff gate; the
  "routine.yaml is NEVER writable by any run" invariant is absolute once more (denials now
  point knob changes at tuning.yaml). `config.load_tuning`/`write_tuning` are the one
  reader/writer pair; scaffold and conversation creation always write the file; the
  clarify-template copy reads it; the registry memo fingerprints both files so a
  tuning-only edit is never served stale. Production data migrated in the same session
  (routine.yaml `deliberation` keys moved into tuning.yaml; a leftover config key is
  reported as a problem and ignored — never read).

## [0.55.0] — 2026-07-16

### Added
- **The deliberation slider** (user order): a per-routine/per-conversation knob over how
  much of the model's thinking lands ON PAPER — the persistent prose channel that, unlike
  ephemeral thinking tokens, survives between turns. Four named stops
  (`terse | standard | deliberate | think-on-paper`), each a qualitatively distinct say
  contract (`engine/deliberation.py` owns the wording; the top two license knowledge
  BEYOND the run — domain conventions, base rates, prior art — and the top stop adds a
  notes-file discipline before direction-shaping actions). Conversations default to
  `deliberate`, routines to `standard`; children inherit the parent's live level.
  Surfaces: routine page (Models panel), new-routine wizard (suggested per task by
  `suggest_traits_permissions`, editable), conversation header panel (saves config +
  re-levels a live reply), and the run view (mid-run, control.json `set_deliberation` —
  applied at the turn boundary as an engine note carrying the new contract, exactly like
  a model switch). Status/SSE/API carry the live level.
- **The improver can optimize it.** `deliberation` is now the ONE routine.yaml key a run
  may edit — only under a user-granted fs_write_root (the routine-improver's grant), and
  the executor parses the proposed yaml and rejects any change beyond that single key
  (`grants.py config_tunable` + `executor._deliberation_only_change`). The improver's
  seed teaches the rubric: raise a stop when judgment-heavy transcripts show restatement
  says, lower when mechanical work carries contextualizing ceremony; one stop at a time,
  evidence logged. Every other config key stays sealed exactly as before.

## [0.54.1] — 2026-07-16

### Fixed
- **Flaky `test_dialog_reply_*` decisions tests (recurring F71).** The driver thread's
  wall-clock deadline (30s) could expire before the run's total ask budget elapsed
  (`ask_timeout_min: 1` × two blocking asks = up to 120s) under full-suite CPU load, so the
  re-ask answer was never posted and `answers[1]` raised `IndexError`. Raised both driver
  deadlines to 180s so the driver always outlives the run's whole ask budget. Test-only
  change; no runtime behaviour affected.

## [0.54.0] — 2026-07-16

### Added
- **"Refer to" on every message (the messenger reply analog).** Every transcript message
  (turns, injections, questions, answers, finish banners) and every chat message (yours,
  the agent's replies, single work steps inside a fold) carries a hover ↩ that primes the
  composer with a reference chip; sending prepends ONE leading quoted line
  (`> re <label>: <snippet>`) to the message text — plain markdown the model reads
  naturally, no new event field. The sent message renders the line as a compact quote chip,
  ✕ drops a primed reference, and a slash command never takes one (its `/<kind>` head must
  lead). Run view (all three modes) and conversations alike.
- **Transcript story rendering.** The run transcript groups the say stream by acting stage:
  a phase change draws a labeled divider (from the `phase` stamp assistant_action events
  already carry), so a run reads as chapters of its own stages. Applies wherever the shared
  renderer runs — run view, subrun unfolds, and chat work folds.

### Fixed
- **Conversation messages no longer carry `\r`.** Multipart form encoding turns every
  newline into CRLF; the conversations API now canonicalizes to `\n` on receipt (create +
  message), so multi-line chat messages stop leaking carriage returns into instruction.md,
  the inbox, and the model's context. Surfaced by the refer-to tests' exact-match asserts.

### Changed
- **Finding-first `say` contract.** The harness contract and the action schema now demand
  the say LEAD with what the last observation taught, then why this action — a few words
  for routine steps, 2-3 sentences on decisions, direction changes, and surprises (was:
  "one short sentence, what/why"). Mid-run narration becomes an actual story instead of a
  restatement of the action beside it; prompt-anatomy doc + pin test track the wording.

## [0.53.0] — 2026-07-16

### Added
- **Clarification template routine (audit decision D10).** The "+ New routine" wizard's
  clarify sessions now copy their budgets, models, and practice modules (`traits/`) from a
  visible, protected `clarification` routine instead of hardcoded values. Seeded via
  `routine-seed/clarification` and adopted once at boot on existing deployments; the API
  refuses run/archive for it (403), every card/detail payload carries `protected`, and the
  routine page swaps the run/archive buttons for a "protected template" chip. Editing that
  routine's budgets/models/traits tunes every future clarification session.

## [0.52.0] — 2026-07-16

Self-audit (wizard hardening after the 2026-07-16 routine-creation incidents).

### Fixed
- **A self-restart no longer kills an in-flight routine clarification.** Clarify runs live in
  dot-hidden `.wizard-*` dirs the registry skips, so the restart drain never saw them: a drain
  fired mid-clarification and orphaned the user's setup conversation at turn 0. New
  `restart.clarify_states()` folds live clarify runs into the drain gate — `waiting_user`
  defers the restart, `running`/fresh `starting` drain it; dead pids and stale orphans never
  block. `/api/wizard/start` also returns 503 while draining (mirrors finalize's gate).
- **The clarify run can no longer be silently decomposed into the drafted routine itself.**
  Observed: applied to a draft that described a research routine, the decompose step built THAT
  routine — it ran the task, posted its output to Decisions, never wrote
  `state/wizard_result.json`, and creation dead-ended with "The clarification run ended without
  a result." Patterns may now PIN deliverable paths (`META["pin"]`, clarify-instruction v8 pins
  `state/wizard_result.json`); the decompose prompt demands them and a result that drops one
  falls back to the verbatim pattern.
- **Clarify questions no longer show twice on the Decisions page** — a live blocking question
  also has a durable pending record; `_wizard_questions` now dedups by qid like `_all_questions`
  always did.

### Added
- The clarify error screen offers **"retry with the same draft"** (the error-stage wizard
  snapshot carries `draft_full`) instead of only a draft-losing "start over".
- The setup banner names the session it refers to (draft preview), so a leftover abandoned
  session no longer reads as if the routine just created were still "in progress".

## [0.51.0] — 2026-07-16

### Added
- **Nano-GPT endpoint cards show the account balance** like OpenRouter ones (user order):
  the credits route now sniffs the provider from `base_url` — OpenRouter keeps
  `GET {base}/credits`, Nano-GPT uses `POST /api/check-balance` on the origin with
  `x-api-key` auth (string `usd_balance`, verified live) — and returns a per-provider
  `manage_url` the card links instead of a hardcoded OpenRouter URL.

### Fixed
- **The conversations rails persist at every desktop width** (user order: the conversation
  list stays LEFT, state/artifacts stay RIGHT): at 1200–1559px the view now escapes the
  1180px column and becomes a three-column grid with sticky rails beside the chat —
  previously both rails collapsed into stacked blocks above the chat below 1560px. DOM
  order is now list · chat · artifacts, so on narrow/stacked screens the artifacts drop
  below the chat instead of pushing it down. `tests/test_static_layout.py` pins the
  regime; new `tests/test_endpoint_credits.py` pins the credits provider sniff.

## [0.50.2] — 2026-07-16

### Fixed
- **`server_tz()` consults `/etc/timezone` before the `/etc/localtime` symlink**: Docker
  bind-mounts through the image's symlink (stale NAME over correct zone DATA), so in a
  container the symlink route answered `Etc/UTC` even with the host's zone mounted.

## [0.50.1] — 2026-07-16

### Fixed
- **Conversations and detached background runs now survive container recreation**: the
  compose file was missing bind mounts for `~/conversations` and `~/background`, so both
  homes lived in the container's writable layer — any `docker compose up -d` after a
  compose/image change would have silently destroyed them (plain restarts reuse the
  container, which is why nothing was lost). Both are now bound like `~/routines`.
- **`server_tz()` works inside a container**: it now honors a `TZ` env var and falls back
  to `/etc/timezone` (bind-mounted from the host along with `/etc/localtime`, read-only) —
  previously only the `/etc/localtime` symlink trick worked, which a bind mount defeats,
  so a containerized daemon always reported `Etc/UTC` and stamped UTC into every schedule
  the UI wrote.

## [0.50.0] — 2026-07-16

### Added
- **write_file overwrites must be grounded** (the Claude-Code-style read-before-write rule,
  scoped to where it matters): overwriting an existing file OUTSIDE the routine's own dir —
  a project file under an `fs_write_root` — is rejected unless the run has read, viewed, or
  written that file this run (`ctx.seen_paths`, rebuilt from the transcript on resume so a
  leg-one read grounds a leg-two rewrite). The routine's own dir is exempt (state/report
  rewrites are its normal mode), `append` adds without destroying, new files need no
  grounding, and `edit_file` stays ungated — its verbatim anchor is self-grounding. The
  rejection is a teaching observation naming the fix; the composed prompt's file-actions
  line states the rule up front.

## [0.49.1] — 2026-07-16

### Changed
- **`steps/` → `stages/` everywhere — one module-dir convention.** All seven production
  routines were migrated in place (`git mv steps stages` + a reference rewrite across
  main.md / stage modules / traits / state files, committed per routine repo; `runs/`
  and LEDGER history untouched), and the engine's transitional `steps/` acceptance from
  0.49.0 was removed (`statemap.STAGES_DIR`). Per the migration policy, the data
  migration ran once on the production instance and no migration code is kept.

## [0.49.0] — 2026-07-16

### Changed
- **The stage modules ARE the state graph — nothing inferred from prose.** `statemap.py` no
  longer parses main.md's `## Run flow` for bold state names; the diagram's nodes are the
  routine's own `stages/*.md` modules (older recipes' `steps/` accepted too), ordered by
  where main.md first mentions each one, with the module's leading heading as the tooltip.
  "no parseable run flow" can no longer happen — every routine has stage modules with
  task-specific names (this fixes the config-optimizer's empty rail).
- **The live phase is derived from stage-module reads, not phase.json.** Reading
  `stages/<name>.md` IS the state transition: the executor stamps it into `ctx.phase` →
  status.json → the SSE `state` event; a resumed run rehydrates the phase from its replayed
  transcript. `state/phase.json` stays recipe-private state (the digest still shows it) but
  no longer drives the diagram, and decompose no longer asks recipes to bookkeep it per
  stage. The routine `/stategraph` endpoint's `current` now comes from the latest run's
  status.json.

## [0.48.1] — 2026-07-16

### Fixed
- **Full-repo `ruff check` is green again**: the seed trees are now excluded from lint
  (`extend-exclude = ["library-seed", "util-seed"]` with the reasons documented in
  `pyproject.toml`). Workflow pattern files are never-executed control-flow depictions
  parsed with `ast` (pseudo-imports are the format; `workflows/lint.py` is their gate), and
  seed utils are PEP 723 single-file scripts with script conventions (print CLI,
  assert-based `--selftest`; header checks + the selftest run are their gate). Previously
  ~226 findings in those trees never surfaced because the pre-commit hook only lints
  changed files — the "ruff green in every commit" invariant now holds for the whole repo,
  and pre-commit's `--force-exclude` keeps the exclusion effective for explicitly-passed
  paths too.

## [0.48.0] — 2026-07-16

### Added
- **File-activity rail card** (user order): the run view and the conversation view now show
  which files a run read / wrote / edited — per-path counts derived server-side from the
  transcript's observation events (`GET /api/runs/{id}/files`, `rsched/fileactivity.py`),
  so subruns and user slash commands count too. Rows are first-touched order, long paths
  truncate on the left, failed touches are flagged; the card live-refreshes off the SSE
  tail (bursts coalesced into one refetch).

### Changed
- **State graph marks skipped phases**: a state the run's `phase.json` jumped over (no turn
  ever recorded under it) now renders `» skipped` instead of a ✓ — previously the checkmark
  was purely positional, claiming work that never happened. Detection requires the run to
  stamp phases at all, so a conversation's synthetic reply-cycle diagram is unaffected.

## [0.47.0] — 2026-07-16

### Changed
- **Conversations view adopts the run page's layout** (user order): the chat owns the full
  1180px main column; the conversation list parks in a LEFT margin rail and
  state/tasks/artifacts in the RIGHT margin rail on wide screens (`.run-rail` /
  `.run-rail.left`), ordinary collapsible blocks above the chat otherwise. The old
  three-pane grid (drag handles, fold rails, persisted pane widths) is removed —
  `views/conversations.js` −78 lines, plus the matching CSS. New
  `tests/test_static_layout.py` pins the rail adoption and checks every mounted
  `conv-*`/`pane-*` class is styled.

## [0.46.1] — 2026-07-16

### Fixed
- **Conversations view: `mdInline` was used but never imported.** `static/views/conversations.js`
  called `mdInline(q.question)` when rendering a deferred question (`showQuestion`) without
  importing it from `/static/md.js`, so the deferred-question box crashed the render with
  `ReferenceError: mdInline is not defined` (observed twice in `.ui-traces` on 2026-07-15).
  Added the missing import. A new static-analysis test (`tests/test_static_imports.py`) now
  asserts every `static/**/*.js` that calls `md()`/`mdInline()` imports it from `/static/md.js`,
  so the console's no-build ES modules can't ship this ReferenceError class again.

## [0.46.0] — 2026-07-16

### Changed
- **A slash command keeps the speaking turn with the user — it never hands the turn to the
  model.** When the model has given the turn back (an authored finish) and the resuming
  message only runs commands, the engine executes them and returns to idle with **no model
  turn and no reply** (the loop's command-only gate: `loop.leg_after_authored` + all
  commands, no prose → `_exit_commands_only`, no finish event, `result.md` untouched). You
  can run any number of commands in a row and the assistant stays quiet; it replies only
  when you send a plain message — and then it sees every command's result (replayed from the
  transcript). The rule is uniform across conversations and routines: it fires wherever the
  turn is yours (a conversation reply, or a resumed finished run), and does NOT fire for a
  routine's own scheduled execution (its workflow always runs; an injected command there is
  context). A command still grounds the run, so a following model finish is not treated as
  fabricated. The command composer's send toast now reads "command running — you keep the
  turn".

## [0.45.1] — 2026-07-16

### Fixed
- **Command autocomplete was unreadable**: the dropdown referenced a CSS token that
  doesn't exist (`--panel`), rendering transparent over the chat. It now uses the theme's
  raised surface (help panel likewise), the harness pins an opaque computed background so
  an undefined token can't slip through again, and a sweep confirmed every `var(--…)` in
  both stylesheets resolves.

## [0.45.0] — 2026-07-16

### Added
- **Chat slash commands — the user can run the same actions and utils as the assistant.**
  Type `/` in the conversation composer for autocomplete (kinds first, util names after
  `/util `); the **/ commands** button beside the input opens the full reference — the
  effect actions the conversation's capabilities allow plus every global util with its
  usage line (`GET /api/conversations/{slug}/commands`). A sent command executes through
  the engine's normal action path (`engine/commands.py` parse → the model action's exact
  schema + `validate_action` gates → `executor.dispatch`) at the next turn boundary —
  costing **no model turn**. The result renders in the chat as a command block, and the
  assistant sees exactly what the user ran and what came back; malformed or disallowed
  commands answer with their usage line. Grammar:
  `/util <name> [arg …]`, `/read_file <path> [path …]`, `/write_file <path> <content…>`,
  `/edit_file <path> anchor="…" replacement="…"`, `/view_image <path> [prompt…]`,
  `/llm <prompt…>`, `/memory_read <name>`, `/memory_write <name> about="…" <content…>`.
  Loop-control actions (`spawn`, `subtask`, `wait`, `ask_user`, `finish`, …) are
  deliberately not commands — they steer the assistant's run.

## [0.44.0] — 2026-07-16

### Added
- **Library items are deletable, not just editable**: traits and global utils gain a
  delete button in their editors (themed confirm, committed to the library repo) beside
  the existing workflow and playbook deletes. Two protections, enforced server-side and
  reflected in the UI: **permission docs cannot be deleted** (they are the capability
  layer's conduct surface — edit them instead) and the **`clarify-instruction` workflow
  cannot be deleted** (the new-routine wizard runs it to create every routine; its editor
  simply has no delete button). A deleted seed workflow/trait returns at the next daemon
  boot; a deleted util stays deleted but is git-recoverable. After a delete the page
  reloads onto the bare Library list instead of the dead item's deep link.

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

