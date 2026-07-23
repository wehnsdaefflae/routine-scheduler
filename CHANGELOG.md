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

## [0.87.4] — 2026-07-23

Last small closers from the findings ledger.

### Fixed
- A broken connection/machine **binding no longer fails silently**: the resolver warnings
  the executor used to discard ("key X unset", "machine Y not in catalog") now land in the
  engine log on every util call that resolves bindings.
- **Workflow lint validates `tools:`**: a META allowlist naming an unknown action kind
  (or not being a list) is a lint problem — a typo'd entry used to pass lint and silently
  allow nothing at run time. Vocabulary = `engine/actions.KINDS`.
- Playbook writes (`MAIN.md` + detail files) are atomic (`paths.atomic_write`) — they are
  read cross-process by the web layer and library sync.

## [0.87.3] — 2026-07-23

Test-suite consolidation + the findings ledger's coverage list.

### Changed
- **One test double / helper where five-to-seven copies were**: `FakeRunner` (scheduler,
  triggers, schedule-once, hooks; the detached suite subclasses it for its status-writing
  fire + guarded resume), `git_in` (the pinned-identity subprocess-git helper behind every
  per-file `_git`), `mk_run` (the run-dir/status.json factory), and `make_test_server`
  (the hermetic config.yaml builder behind `api_client` and every hand-rolled TestClient
  block) — all in `tests/conftest.py`.
- UI harness: the free-port probe is gone — the bound socket is handed straight to
  uvicorn (`run(sockets=[...])`), closing the close-then-rebind race under xdist; the
  StubRunner's unread resume recording dropped; stale "inert until installed" comment
  fixed.
- `test_loop` wait wall-clock margins widened to 20s (the failure mode they distinguish
  is the 30s timeout; 10s flaked under load); the search limit-cap test now asserts the
  clamp (it was a tautology); a `/api/audit` test subsumed by an earlier one removed.

### Added
- Coverage for the ledger's untested list: the `subruns` status action; the wait-timeout
  branch (SubrunManager-level); the compaction gate's cached-0.8 vs uncached-0.6
  thresholds; trigger exactly-once redelivery across a crash replay; the sshfs mount
  success path (key 0600, pinned known_hosts, keydir removed on unmount); `/api/fs`
  401 + truncation; `ensure_docs` skip-env short-circuit; and a new UI file covering
  the Help and Log views plus the transcript renderer's question / answer / error /
  compaction rows.

## [0.87.2] — 2026-07-23

### Changed
- **Mega-view splits** (the ≤~350-line rule, applied to the frontend): `settings.js`
  874 → 99 lines (eight `settings-*.js` section modules — github, connections, machines,
  secrets, library+sync, source, server, notifications — plus `settings-common.js` for the
  shared remote tester; the pre-existing `settings-endpoints.js` convention, applied to
  every section); `conversations.js` 754 → 442 (`conversations-new.js` composer,
  `conversations-head.js` header, `components/filepicker.js`); `routine.js` 623 → 147
  (`routine-config.js` — every config panel, Name…Origin — `routine-health.js`,
  `routine-recipe.js`). Pure refactor: same DOM order, same behavior, all 56 UI tests
  green unchanged.

## [0.87.1] — 2026-07-23

Frontend polish sweep (the findings ledger's deferred UI batch).

### Added
- **Shared components**: `components/referchip.js` (the refer-to chip the run view and chat
  composer both mount — one convention, one implementation) and `questionPanel` in
  `components/answerform.js` (the blocking-question panel; the conversation now also shows
  the util-approval tag and the timeout/Decisions line — same record shape, same chrome).
- Run detail API (`GET /api/runs/{id}`) carries `home` (routine | conversation |
  background) — a payload extension.

### Changed
- **SPA remount replaces `location.reload()`** everywhere (run resume/converse/revise
  reattach, library delete): `router.remount()` re-renders the current view in place —
  no full page reload, no flash. Library **save** now also refreshes in place (list +
  tags), reopening the editor via its deep link.
- **Run view is home-aware**: a conversation-home run's breadcrumb links
  `#/conversations/<slug>` (it used to 404 onto the routine page), its rail uses the
  conversation stategraph/artifacts routes; a background task labels itself and skips
  the routes it doesn't have.
- Stats: routine/conversation names in "By routine" and "Monthly spend" link to their
  pages; the two stat-tile CSS systems (`.stat` / `.stat-card`) collapsed into one.
- Week grid colors are slug-stable (hash, not row index) — a routine keeps its color
  as the list reorders.

### Fixed
- **Accessibility**: dialogs trap Tab focus and restore it to the opener on close
  (`role=dialog aria-modal`); the search box is a real combobox/listbox
  (`aria-expanded`, `aria-activedescendant`, option roles); the conversation state dot
  carries `title` + `aria-label` (it was color-only).
- **Error-vs-empty honesty**: a failed fetch no longer renders as "empty" — the state
  graph, help tab, artifacts, file-activity, and subtask-tree rails each say the load
  failed (first load only; later transient errors keep the last good render).

## [0.87.0] — 2026-07-23

### Changed — the oversized modules split along their seams (overhaul batch 8)
Every file the audit flagged over the ~350-line standard now has one responsibility
(public surfaces unchanged — same imports, same routes):
- `engine/executor.py` (595) → executor (dispatch, util runner, llm subcall) +
  **`engine/fileops.py`** (read/view/write/edit, memory actions, read_trait, the shared
  path gates).
- `config.py` (666) → the **`config/` package**: `base` (vocabulary + lenient
  validation), `modelconf` (endpoints/models/machines), `server`, `routine` —
  `from rsched.config import X` is unchanged for every consumer.
- `web/api_routines.py` (630) → read surfaces + **`web/api_routine_edit.py`** (traits,
  permissions, PATCH, run-now, archive) + **`web/routines_common.py`** (the guards and
  lookups four sibling routers used to reach into api_routines for).
- `web/api_conversations.py` (506) → main + **`web/conversations_common.py`** (lookups,
  streamed attachment saving) + **`web/api_conversation_playbooks.py`** (save/update
  playbook).
- `endpoints/claude_cli.py` (478) → the adapter (sessions, retries, media latch) +
  **`endpoints/claude_cli_wire.py`** (command construction, env scrubbing, token
  resolution, envelope parsing).
- `engine/actions.py` stays whole on purpose — it is the documented single home of the
  action contract.

### Fixed/Changed — remaining deferred hygiene (batch 8, same commit series)
- ONE home each for: the lenient frontmatter parse (`library_docs.parse_lenient`), the
  neutral git identity (`libgit.GIT_USER/IDENTITY_*`), the Standing-practices tail
  (`scaffold.render_practices_tail` — the two copies' lead wording had drifted), the
  cross-home probe (`registry.all_homes`), and the abort-with-pid-fallback sequence
  (`api_runs.abort_with_fallback`, reused by background cancel — which now 409s
  honestly instead of `ok:true cancelled:false`).
- Bootstrap's five add+commit pairs go through `libgit.commit` (per-repo lock, scoped
  stage); `registry.next_fire` takes a real `Schedulable` protocol (the library-sync
  duck-typing `type:ignore`s are gone); `RunInfo` carries `model` so the Stats
  aggregate stops re-reading every status.json per request; `_run_ref` drops its two
  retired-shape tolerances; scaffold filters unknown budget keys and applies the same
  raise-then-floor capability discipline as the save path; GitHub device flows are
  pruned; the `library_home`/`utils_home` twin properties collapse into
  `libraries_home`; expired GitHub device flows are pruned on each start.

## [0.86.2] — 2026-07-23

### Changed — hygiene and dedupe sweep (overhaul batch 7)
- **Dead code removed** (each verified caller-less): `grants._PERMISSIVENESS` +
  `grants.unsatisfied_requires`, `lint_materialized_text` + `PLACEHOLDER_RE`,
  `bug_reports.read_bug_reports`, `scaffold._git_init`, `library_docs._git`,
  `schema_guard.parse_reply`'s never-passed `semantic` hook, `read_workflow`'s
  redundant 3-tuple (body==raw), the openai adapter's unreachable
  `structured_outputs` hint, and the OAuth provider registry's consumer-less
  `device_url` scaffold field.
- **One home per idiom**: `libgit.git_log` replaces the two byte-identical copies
  (library_docs / workflows.library); every mid-run control.json signal writes through
  ONE `merge_control` helper (api_runs, reused by the trait live-add) instead of six
  hand-rolled read-modify-writes; the runner reads `registry.ACTIVE_STATES` instead of
  two drifted inline tuples; the CLI's turn renderer falls back to `BRIEF_FIELD` for
  any kind without a rich label (six kinds rendered blank).
- **The server's zone, not a hardcoded city**: conversations, wizard sessions, detached
  tasks, and `rsched scaffold` now default tz to `server_tz()` instead of
  Europe/Berlin.
- `runtime`'s dead "fatal problem" substring classification is gone (no load problem
  ever matched it); `suggest_workflows` degrades gracefully like its sibling
  suggesters instead of 500ing the wizard on an endpoint failure; the api_audit
  legacy-shape recovery and the registry's pre-`elapsed_s` fallback carry MIGRATION
  markers; `atomic_write` documents its deliberate no-fsync durability posture and
  `FileSink.__del__` its real contract.

## [0.86.1] — 2026-07-23

### Fixed — frontend sweep (overhaul batch 6)
- **Badge refreshes are coalesced.** Every bus event — including sub-second `llm_task`
  storms during a busy run — fired its own `GET /api/questions`; llm_task events now
  skip the badge entirely and everything else refreshes at most once per 3s window with
  a trailing refresh.
- The routine page is live: its own run start/finish events refresh the header chip,
  recipe health, and the runs table (it was a static snapshot — a stale hub).
- Question filters live in the URL (`#/questions?filter=…&routine=…`); a routine page's
  "answer" button deep-links straight to that routine's open decisions.
- Search hits into a NESTED subrun (`?sub=2/1`) land on the child's top-level subtree
  instead of NaN-ing back to the main transcript; the never-produced `?offset=` deep
  link is gone.
- Library rows carry REAL section deep-links (`#/library/<kind>/<slug>`) — middle-click
  and open-in-new-tab work; a failed conversations-list load shows an error + retry
  instead of a silent blank rail; the Summary tab's filter/read states are actually
  styled and the list refreshes when a run finishes; the one-shot cancel dialog prints
  the fire time instead of `[object HTMLSpanElement]`; the models panel uses a CSS token
  that exists.
- a11y: toasts announce via `role="status"` + `aria-live`; the refer (↩) buttons are
  visible on keyboard focus.
- Dedupe/dead-code: ONE authed-fetch loop behind `api()`/`apiUpload()`; ONE
  `BUDGET_FIELDS` vocabulary (`components/budgetfields.js` — the two copies had drifted
  labels, and the wizard panel now shows the help lines too); dead `.tag.meta` CSS
  dropped. Server-side dead routes removed: `GET /api/workflows` + `POST
  /api/workflows/lint` (uncalled), the three shadowed `/library/utils/{name}`
  registrations (the `{kind}/{slug}` routes dispatch), `POST /settings/machines` (PUT
  upserts), and the pre-D11 wizard `events`/`transcript`/`answer` shims.

## [0.86.0] — 2026-07-23

### Changed — the read-model architecture gets an honest home (overhaul batch 5)
The audit's architecture verdict: the registry was "a universal read-model at a daemon/
address" (21 importers, 10 of them web), its siblings were scattered across four homes,
and every rail poll re-derived its view from raw transcripts.

- **`rsched/registry.py`** — the catalog/run-index read-model moves OUT of `daemon/`
  (every importer rewritten; nothing re-exported). The daemon OWNS processes; it does
  not own the shared view of the disk.
- **`rsched/readmodels/`** — the derived-view home: `stats`, `run_health`, `util_stats`,
  `statemap`, `fileactivity`, and `tasktree` (formerly `web/tasktree.py`) move in, with
  two shared primitives:
  - `memo` — stat-fingerprint caching (inode+mtime+size, the registry's idiom): a
    derived view recomputes only when an input file actually changed, and cached values
    return as deep copies. `statemap.phase_stats`, `fileactivity.file_activity`, and
    `tasktree.build_tree` — all polled every few seconds by the run rail — now hit this
    instead of re-parsing whole transcripts per tick.
  - `usage_stream` — the ONE parser of `workflow-usage.jsonl` (stats' monthly spend,
    run_health's buckets, and util_stats' table each had their own), memoized on the
    stream's fingerprint.
- **Search**: queries run on their own WAL read connection — a long refresh pass no
  longer makes the search box hang behind the writer's lock; the refresh budget now
  covers the stat-walk too (it was spent before the first budget check at scale).
- `USAGE_ERROR_EXIT` moves to `utils_lib` (the util contract) — the Stats read-model no
  longer reaches into `engine.executor` for a constant.

## [0.85.4] — 2026-07-23

### Fixed — transports, daemon, and web API sweep (overhaul batch 4)
- **Remote machines**: `known_hosts` parsing anchors on the key-TYPE token — a `.pub`
  paste used to pin `base64 comment` and every connection then refused (engine + the
  seed `remote` util); a failed mount attempt no longer leaks its PEM key dir; stale
  `.mounts/` key dirs are swept at daemon boot; `mnt/` is gitignored BEFORE any mount
  attempt so a crashed run's stale mount can't be autocommitted.
- **OAuth**: the refresh manager's slow token exchange now lands via compare-and-swap
  (`store.update_connection`) so it can never clobber a re-authorization that happened
  mid-exchange; a refresh response without `expires_in` gets an assumed 1h lifetime
  instead of hammering the provider every 5s tick; a non-JSON 200 no longer aborts the
  whole pass (per-connection isolation); the needs-reauth Discord ping is gated on a
  binding routine actually holding the `communication` permission; the provider `error`
  echoed on the public callback page is HTML-escaped.
- **Model calls outside the engine get real limits.** Decompose, suggest, generate,
  distill, and conversation autolabel now pass the resolved model's `max_tokens` (and
  effort) — they silently ran at anthropic's 8192 fallback; the adapter fallback itself
  now IS `config.DEFAULT_MODEL_MAX_TOKENS` (16 384), removing the two-constants
  conflict. The Settings credential label flags a configured-but-missing env file
  (`env_file_miss`) instead of reporting benign keylessness.
- **Daemon**: a queued run is abortable (the supervisor honors a cancel flag after its
  slot acquire); a PAUSED run releases its concurrency slot like waiting_user; a pid
  the daemon may not signal (EPERM) counts as ALIVE; retention gzips nested subrun
  transcripts (`rglob`); trigger cooldowns gate PER TRIGGER as documented (a cooling
  trigger's events wait; a sibling's fire); `ensure_config` verifies the token actually
  landed whatever the example says and writes atomically; `rsched abort` resolves
  conversations and background tasks and counts queued runs.
- **Web API**: inbox message filenames carry a uuid (same-second messages clobbered
  each other); the protected clarification template can no longer be resumed or given
  revise-recipe grants directly; the Decisions surface scans `background_home` (a
  detached task's deferred asks were invisible); attachment uploads STREAM to disk with
  the size cap enforced mid-flight (a big body used to buffer whole and OOM the 3.3GB
  box), capped per message, same-name collisions suffixed, and a 413 no longer strands
  a half-created conversation; web routine-dir commits take the shared repo lock;
  `config.yaml`/library-doc/workflow writes are atomic; the wizard answer endpoint
  validates `qid` (was a path segment); same-second wizard sessions no longer 500.
- **Config**: unknown top-level keys in `config.yaml`/`routine.yaml` are reported as
  problems — a misspelled `permisions:` used to silently reset the real field to
  defaults with zero trace.
- **Misc**: `stats.monthly_spend` skips malformed usage records instead of 500ing the
  dashboard; on-demand workflow generation counts up `-2, -3…` on slug collisions and
  rewrites the draft's META slug to match (no silent overwrite, no perma-lint flag);
  the decompose fallback logs WHY it degraded; the playbook-distill digest keeps the
  NEWEST exchanges when truncating (Update-playbook needs exactly those).

## [0.85.3] — 2026-07-23

### Fixed — engine resume/control correctness (overhaul batch 3)
- **A resumed parent gets its children's results back.** Child-exit announcements are
  live message appends with no 1:1 transcript event — a resume replayed everything BUT
  them, so a parent interrupted after a subtask finished lost the child's summary.
  `replay_messages` now reconstitutes announcements from `subrun_end` events (placed
  where the live message sat; children delivered via a `wait` observation are not
  re-announced), sharing one wording builder with the live announcer.
- **Blocking answers are no longer duplicated on resume.** The answer text lives inside
  the ask_user observation; replaying the `answer` event too injected it twice.
- **Mid-run switches fire once, not once per leg.** control.json is web-owned, so a
  consumed switch_model/set_deliberation/add_traits signal could never be cleared —
  every resume leg re-applied it (re-pinning models the user had changed back and
  re-injecting the same engine notes). The engine now keeps a per-run applied ledger
  (`control-applied.json`) that seeds the edge-triggers on every leg.
- **Fresh-boot inbox prose is transcripted.** Messages drained at kickoff rode only the
  composed prompt — invisible to the transcript renderer and lost on resume. They are
  `user_injection` events now (with a `boot` marker) and replay correctly.
- **`schedule_run` self-target works for conversations.** The schema always promised it;
  the handler only resolved routines_home. A conversation's one-shot lands in a
  namespaced spool (`conv--<slug>`, so a same-named routine can never be mis-fired) and
  the daemon wakes the conversation by RESUMING its run — the "remind me in 3 days"
  flow. Corrupt one-shot request files are dropped instead of rescanned every 5s; the
  dead `active` flag is gone from spool records.
- **Aborting a paused run credits the paused time**; killing a still-running child at
  parent exit snapshots its usage race-free; parallel `spawn` catalogs are listed for
  subtask/detach-only workflows too; a missing util's observation now names the
  available utils inline (no discovery turn); `orphaned_children` carries the workflow
  slug so boot's synthesized `subrun_end` matches the collector's shape.
- Hygiene: the inbox's raw-text fallback (no writer produces it) is gone — corrupt
  message files are consumed with a warning, transiently-unreadable ones still wait;
  `decisions._reply_texts` pins the discord util's ONE output shape; the dead
  `Budget.hard` knob is removed; tolerant `getattr` debris dropped across
  loop/subruns/interact/executor/capabilities/composer (test stubs now carry real
  fields); `executor` reuses `grants.is_recipe_path`.

## [0.85.2] — 2026-07-22

### Fixed — daemon + web security/robustness sweep (overhaul batch 2)
- **SSE tickets are SSE-only credentials.** A minted ticket used to satisfy `require_auth`
  on EVERY `/api` route — a 60-second URL-carriable full-API bearer, writes included. It
  now authenticates only the two EventSource surfaces (`/api/events`,
  `/api/runs/{id}/events`), GET only.
- **The fs picker refuses credential stores.** `/api/fs/list` browsed anything the daemon
  user can reach — including `~/.credentials`, the config dir (secrets.env, vapid keys,
  `.mounts/`), `~/.ssh`, `~/.claude`. Those roots (and descendants) now 403.
- **One bad scheduler tick no longer kills scheduling.** The cron tick body is guarded:
  an exception (a tz typo surfacing in next_fire, a disk blip) is logged + flagged as a
  health event and the loop keeps ticking — it used to unwind `run_forever` silently
  while the web UI kept serving. Lifespan background tasks (scheduler, push, search) get
  a died-silently observer; routine/library-sync `tz` values are validated at load/save.
- **Restart protection for clarify runs works again.** `restart.clarify_states` still
  scanned the pre-D13 `.wizard-*/runs` layout, where no run has lived since the wizard
  unification — the drain gate for in-flight setup conversations was silently inert. It
  now reads `clarification/runs/*` (the real layout); the tests pin the real layout too.
- **The scheduled library sync respects the commit serialization.** `git_sync` committed
  with an unscoped `git add -A` and no repo lock — sweeping any concurrent writer's
  uncommitted util into an "instance sync" commit and racing engine commits on git's
  index (bypassing the 0.83.1/0.83.2 discipline daily). It now stages only its own paths
  (`routines/ config/`) under the shared per-repo lock, pulls `--autostash`, and no
  longer pushes over a failed pull. Config-export redaction also scrubs URL-embedded
  credentials (`https://user:token@host` in remote URLs).
- **Multi-line secrets survive the store.** A pasted SSH private key (the
  remote-machines `key_var` flow) was silently corrupted by the line-based secrets store
  (docs tell the operator to paste one). Values containing newlines are now JSON-quoted
  onto one line; single-line values keep the historical format byte-identically.

### Fixed — engine + transport correctness sweep (overhaul batch 1)
Security/correctness-critical fixes from the external audit's findings ledger.

- **Empty completions engage failover and referral (the refusal-gap fix).** Adapters now
  surface the provider's stop reason on every `Completion` (`stop_reason`: anthropic
  `stop_reason`, openai `finish_reason` / ollama `done_reason`, the CLI envelope's
  `stop_reason`/`subtype`). An EMPTY completion with `stop_reason: refusal` — a classifier
  refusal, previously indistinguishable from a hiccup — is referred to the routine's
  `uncensored` model like a free-text refusal; the SECOND consecutive empty from one model
  engages the fallback chain exactly like a hard `EndpointError` (logged as the same
  `failover` payload) instead of blind same-model retries until the run died.
- **Cooldowns are a provider-health signal.** `InstrumentedEndpoint` now starts the 5-min
  failover cooldown only for retryable-class failures (outage, rate limit, network) — a
  bad key or a Settings probe with a wrong credential no longer poisons resolution for
  5 minutes. The engine still cools any model it abandons mid-turn (the judgment moved to
  `completion._switch_to_fallback`, which now marks the failed model itself).
- **claude-cli transport hardening**: calls now run under the shared `with_retries`
  backoff (one transient CLI failure no longer costs a cooldown + failover); garbled CLI
  stdout is retryable like an unparseable HTTP body; the stream-json image capability
  latch flips only after retries exhaust (not on one blip) and a fresh-session reseed
  carrying OLD in-context images degrades them to placeholders instead of hard-failing;
  per-run session cwds under `~/.cache/rsched/claude-cli/` are pruned after a week;
  `SSH_AUTH_SOCK`/`SSH_AGENT_PID` are scrubbed from the CLI child like every util child.
- **`write_util` traversal guard + selftest rollback.** A `write_util`/`remove_util` name
  must be a kebab-case slug (validated in the schema cycle, backstopped in
  `utils_lib.write_util_file`/`remove_util_file`) — a path-shaped name could write outside
  the library. A FAILED selftest now rolls the write back (a new util's dir removed, a
  revision restored to the previous working text) instead of leaving the broken script
  live for concurrent `gu` callers.
- **Util subprocess timeouts kill the whole process group.** `run_util` starts utils in
  their own session and `killpg`s on timeout — the `uv run` grandchild used to survive
  (holding the pipes open and blocking the engine turn past its timeout forever). Output
  capture is file-backed and capped at 1 MB per stream. The seeded `gu` dispatcher's
  `list` skips `__pycache__`/removal residue; `header_problems` now also rejects
  undeclared `["gu", "<sibling>"]` exec sites (same regex the boot migration uses —
  moved to `utils_lib.GU_CALL_RE`).
- **Child tasks keep their workflow's `tools:` allowlist.** `childrun.build_child` loaded
  the materialized pattern's allowlist and then dropped it — every spawn/subtask child ran
  unrestricted. The child number allocation is also lock-protected now (parallel spawns
  could collide), and child/recipe/result writes are atomic (`materialize_to_disk`,
  `_ensure_decomposed`, `result.md`).
- **Blocking-ask re-files keep `config_patch`.** The abort/defer/timeout re-file paths
  dropped a pending config proposal; only the dialog path kept it.
- **Spend from failed turns is booked.** Usage burned by schema-retry cycles that never
  produced an action (force-fail, abort preempt) now lands in the run's usage.
- Empty-completion retry backoff honors `RSCHED_RETRY_BASE_DELAY` (suite runs the logic,
  not the clock); `run_context` sheds its `__import__("os")` debris.

## [0.85.0] — 2026-07-22

### Added — Runtime duration bars on the dashboard "this week" strip
Each fire on the week strip is now a **duration bar** instead of a point: it starts at the fire
time and its width is the routine's **average runtime drawn true to scale** against a day's width
(a full day column = 24h), so a mark's length is an honest read of how long a run takes
(`components/weekgrid.js`).

- The average is computed in the browser from the `recent_runs` window each card already carries
  (`avgRuntime` over `elapsed_s`); no API change. A **2px minimum width** keeps even a short run
  visible, and the **exact runtime is in the hover tooltip** (bars for minutes-long runs sit near
  the floor — that's the honest scale on a week-wide axis).
- Routine **identity moved to a legend below the strip** (colour swatch + name + schedule), which
  frees the timeline to use the full width; the routine's average also shows on legend hover.

### Added — Sections side-TOC on the routine page
The routine detail page now grows the same sticky **"On this page"** rail Settings has, listing its
`<h2>` sections on wide viewports (`components/toc.js`). The page's recipe file tree is a
within-section nav and no longer suppresses the page-level TOC.

### Changed — Filesystem roots are picked, not typed
The routine page's read/write **filesystem roots** are now chosen with a real **server-side
directory browser** instead of a free-text "one path per line" textarea — browse the daemon's
filesystem and select an actual directory (`components/dirpicker.js` + `components/fsroots.js`,
backed by the new bearer-authed `GET /api/fs/list` — `web/api_fs.py`, names + is-dir only, never
file contents). Each root is a removable row; the save payload is unchanged.

## [0.84.0] — 2026-07-22

### Added — Remote machines: routines act on SSH hosts (GPU boxes, build servers)
A routine can now run commands and move files on remote machines over SSH — for work that needs
specific hardware (a GPU, a big build box) the daemon host doesn't have. Modeled on OAuth
connections: a **resource binding**, never a capability a run can grant itself.

- **Machine catalog** (`config.yaml` `machines:`, `ServerConfig.machines` → `MachineConfig`): an
  operator-only, instance-wide list of SSH hosts (host / user / port / `key_var` / pinned
  `host_key` / workdir / description / tags). Key MATERIAL never lives in config — `key_var` names
  a **Secrets-store** key holding the private key; the pinned `host_key` is the server's public
  key, verified STRICTLY at connect (no TOFU in a headless run). Settings → Machines does CRUD,
  a host-key **scan**, and a live **test** — the last two run the real `remote` util server-side,
  so what Settings proves is exactly what a run gets.
- **Binding** (`routine.yaml` `machines: [names]`): a routine names the catalog machines it may
  reach; the binding IS the grant. No run creates or changes one (`routine.yaml` stays sealed).
  Bound on the routine page, alongside models/connections.
- **The reserved `remote` util** (needs the new `remote-machines` permission): `list`, `exec`
  (short, blocking), `submit`/`status`/`logs`/`cancel` (DETACHED jobs for long GPU work — poll,
  or pass `--notify-webhook <the routine's own trigger URL>` and let the job ping the routine on
  completion, no polling), `push`/`pull` (SFTP), plus `scan-host`/`test`. Host keys pinned; a
  mismatch refuses to connect.
- **Injection**: the engine resolves a routine's bindings to `RSCHED_MACHINES` (non-secret
  connection metadata) + `RSCHED_MACHINE_KEYS` (private keys from the Secrets store), passed to
  the `remote` util under the same declared-var gate OAuth tokens use — a token/key reaches a util
  iff the routine binds the machine AND the util declares the var. Bound machines are named in the
  prompt's CAPABILITIES section, so the model knows its hardware without a discovery turn.
- **Filesystem shares** — compute crosses via `remote exec`, the FILESYSTEM via a mount. A machine
  catalog entry can set a `share` (a remote dir); when a routine binds that machine the engine
  mounts it over sshfs at `<routine>/mnt/<name>/` for the run, so ordinary filesystem utils (and
  `read_file`/`write_file`) act on remote files with **no transfer step**. The engine mounts it
  (not a sandboxed util), so the key never enters a util; the routine dir is already a sandbox write
  root and a Landlock rule on it covers the sshfs sub-mount, so utils operate under the same jail.
  `mnt/` is gitignored; mounting is best-effort (unreachable host / no `sshfs` → warn and proceed).
  Docker gains `sshfs` + `/dev/fuse` + `CAP_SYS_ADMIN` (inert unless a bound machine sets a share).
- **Hardening**: `SSH_AUTH_SOCK` / `SSH_AGENT_PID` are now scrubbed from every util subprocess
  (`STRIP_VARS`), so a forwarded agent can never route around the per-routine machine binding.

`~/.ssh` stays invisible to the sandbox exactly as before — remote-machine keys come from the
Secrets store, not from disk. See `docs/remote-machines.md`.

## [0.83.2] — 2026-07-22

### Fixed — Routine-dir commits queue instead of racing (finishes the 0.83.1 race work)
0.83.1 made the shared LIBRARY repo's commits lock-serialized. This does the same for a
ROUTINE's own git repo, closing the last meta-routine race: the **routine-improver commits a
target routine's dir itself** (via the `git-sync` util at its `record` stage), so when that
target is mid-run, two processes were committing one repo — the improver's `git-sync` and the
target's own autocommit / pre-run recipe snapshot — colliding on `index.lock`. Now every writer
of a routine dir takes the **same per-repo lock** (`<repo>/.git/rsched-commit.lock`) and they
queue instead of racing:
- **Engine** ([autocommit.py](src/rsched/engine/autocommit.py), [recipes.py](src/rsched/recipes.py)):
  the run-end autocommit, the pre-run recipe snapshot (`current_recipe_commit`), and the web
  recipe revert (`revert_recipe`) all commit under `paths.file_lock(repo_lock_path(dir))`.
- **`git-sync` util** (library repo): holds the same flock around its local `add`/`commit`/`rebase`
  (push stays outside — it's network-only and doesn't touch the index). Shipped to the library
  (daemon reads utils live — no restart).
- Tests: routine-dir lock coverage in [test_libgit.py](tests/test_libgit.py); the util's `--selftest`
  asserts the lock file is taken.

Still a *logical* (not corruption) gap, unchanged: a multi-file recipe edit isn't one transaction
and the target runs on its old in-memory recipe until its next run — acceptable, since the recipe
only takes effect at the next run anyway.

## [0.83.1] — 2026-07-22

### Fixed — Race conditions when the meta-routines run alongside other routines
Concurrent runs are separate processes with isolated per-routine dirs, so ordinary routines never
collide. But the meta-routines cross that boundary by design — **routine-improver** writes another
routine's recipe, **workflow-curator / util review** rewrite or delete utils another run may be
executing, and **self-audit** reads everything. Every engine write on those paths used a
non-atomic `path.write_text()` (truncate-then-write), and the shared library repo was committed with
an unlocked `git add -A`, so a concurrent reader/committer could see a torn file or sweep a
sibling's change into the wrong commit. Now:
- **Shared library repo commits are serialized and scoped** ([libgit.py](src/rsched/libgit.py)): the
  three duplicate `git_commit` helpers ([utils_lib](src/rsched/utils_lib.py),
  [library_docs](src/rsched/library_docs.py), [workflows/library](src/rsched/workflows/library.py))
  delegate to one primitive that holds a per-repo file lock (`paths.file_lock` /
  `paths.repo_lock_path`) and stages only the path it changed (`git add -A -- <path>`). Every
  writer — engine `write_util`/`remove_util`, the Library-tab web edits, on-demand workflow
  generation — passes its own pathspec, so no `git add` can sweep another writer's not-yet-committed
  file and two writers never collide on `index.lock`.
- **Engine writes are atomic** ([executor.py](src/rsched/engine/executor.py)): `write_file` (the
  overwrite branch), `edit_file`, and `memory_write` go through `paths.atomic_write` (tmp+rename,
  now mode-preserving so an existing file's bits — notably +x — survive an overwrite). The improver
  rewriting a live routine's recipe, that routine's own git autocommit / pre-run recipe snapshot,
  and self-audit reading any routine now see the old or new file whole, never a partial write.
- **Util create/delete are atomic** ([utils_lib.py](src/rsched/utils_lib.py)): `write_util_file`
  uses tmp+rename; `remove_util_file` renames the dir aside before deleting, so a routine executing
  `gu <name>` concurrently sees the util whole or gone, never a half-emptied tree.

Not addressed (out of scope, flagged for a follow-up decision): the improver editing a running
routine's recipe is still a *logical* race — atomicity stops torn files, but a multi-file recipe
edit is not a single transaction, and the target runs on its old in-memory recipe until its next
run. A run-active guard for recipe writes is the open question.

## [0.83.0] — 2026-07-22

### Added — Revise recipe (change a routine's recipe in natural language, from the run view)
A finished routine run's message box gains a **"revise this routine's recipe"** mode: type the
change ("make the report shorter", "stop checking X") and the run resumes with a **run-scoped
recipe self-write grant** and edits its OWN `main.md` / `stages/` / `traits/` / `tuning.yaml` using
its normal file tools — the warmest possible context (it just executed). No extra routine, no
persisted grant.
- **`engine/revise.py`** + the loop ([loop.py](src/rsched/engine/loop.py:102)): a marker the
  `/revise` endpoint drops in the run dir is read ONCE at loop init — it grants `recipe_unlocked`
  and widens `allowed_tools` with `read_file`/`write_file`/`edit_file` for that leg only, then
  clears itself. Ordinary runs stay recipe-sealed; `routine.yaml` (config) stays sealed even under
  revise.
- **`POST /runs/{id}/revise`** ([api_runs.py](src/rsched/web/api_runs.py)): routine-only,
  finished-runs-only; injects a framed directive (edit your recipe; route config asks to
  `ask_user`) and resumes. UI: the "revise" mode in [run.js](static/views/run.js) (hidden for the
  protected clarification template).
- **Config bridge (one-click apply):** a run can't edit `routine.yaml`, so a config-shaped request
  becomes an `ask_user` carrying an optional **`config_patch`** (the `PATCH /routines` body). The
  Decisions page renders the proposed change with an **"approve & apply"** button that PATCHes the
  routine and resolves the ask — reusing the config controls shipped in 0.82.0. `config_patch`
  threads through `actions.py` → `interact.py` → the decision record (`inbox.file_question`) →
  `questions.js`.

## [0.82.0] — 2026-07-22

### Fixed
- **Editing a claude-cli endpoint no longer wipes its `credentials_env` / `key_env_file`.** A
  full-replace `PUT /settings/endpoints/{name}` preserved only `temperature` / `extra_body` /
  `max_tokens`, so any edit (even re-saving the token) reset a custom subscription-token path back
  to the default `~/.credentials/claude-code-oauth.env` — silently breaking auth. Both fields are
  in the preserve list now (`web/settings/endpoints.py`).

### Added — config surfacing (every setting is now reachable in the UI)
An audit found several config fields that had no editable control and could only be changed by
hand-editing `routine.yaml` / `config.yaml`. All are now in the UI:
- **Routine page:** a **Name** rename; a **Retention** control (`keep_runs`); a **Filesystem roots**
  editor (`fs_read_roots` / `fs_write_roots` — a write root covering the routine's own dir is the
  lever that unlocks recipe self-editing, the routine-improver's mechanism); the schedule **catchup**
  policy (skip vs run-once) on the schedule editor; and the **`max_total_turns`** budget (was
  conversation-only). `PATCH /routines/{slug}` accepts them (`keep_runs`, `fs_read_roots`,
  `fs_write_roots`, `schedule.catchup`); the detail read surfaces them.
- **Settings → Server:** a new panel for the runtime knobs — util **sandbox** mode
  (strict/permissive/off), **max concurrent runs**, **registry rescan** interval, and the **GitHub
  OAuth client id** (`GET`/`PUT /settings/server`, `web/settings/server.py`). Sandbox and rescan
  apply live; concurrency needs a restart (the copy says so).
- **Settings → endpoint cards:** inputs for **`temperature`**, **`key_env_file`**, the claude-cli
  **`credentials_env`**, and the openai **`extra_body`** (JSON — OpenRouter provider routing);
  `EndpointBody` + `_endpoint_view` carry the latter two.
- **Triggers card:** a **cooldown** input on webhook-trigger creation (the backend already accepted
  `cooldown_s`; the form never sent it).

### Changed
- `patch_routine` grew a `_apply_resource_fields` helper for the nested/validated fields, and now
  handles the `deliberation` (tuning) write before any `routine.yaml` mutation — so a combined
  patch can no longer early-return and drop an accompanying config change.

## [0.81.0] — 2026-07-21

### Added
- **Practice modules are changeable after creation** — a *Practice modules* panel on the routine
  page and in the conversation header adds or drops traits on an existing routine/conversation
  (`POST /routines/{slug}/traits`, `POST /conversations/{slug}/traits` — one shared
  implementation). The `traits/` directory IS the state and main.md's Standing-practices tail is
  DERIVED from it, rebuilt on every change (`rsched/traits.py`), so add and remove need no
  special-casing and a hand-edited tail converges back. A later add copies the library text
  **verbatim** — only creation adapts, and an LLM round-trip between flipping a switch and the
  module taking effect isn't worth it for a set written to be generally applicable.
- **An addition reaches a run already in flight.** Deliberately not 409-guarded like other
  routine file edits: a run may never write its own `traits/`, so the web layer is the sole
  writer there and no race exists. Since the composed prompt is immutable under the
  prompt-caching contract, `control.json` `add_traits` makes the engine append the module's prose
  as an engine note at the next turn boundary (`control.apply_trait_additions`, the same
  edge-triggered discipline as the model and deliberation switches). Removal lands at the next
  run — prose already in a live context cannot be unsaid.
- **`read_trait` — a read-only practice consult for a run.** A run still never changes its own
  set, but may pull one library module into the CURRENT run when the work turns out to need a
  discipline its recipe lacks (`name: "list"` for the catalog, entries flagged when already held).
  Nothing is written, so the recipe invariant holds intact. Gated by the new `practice-library`
  permission — default-on for conversations, opt-in for routines.

### Changed
- `DEFAULT_TRAITS`' "not toggleable afterwards" note is retired: the user may now retune the set
  at any time. What stays fixed is the direction — practice is granted, never self-granted.

## [0.80.0] — 2026-07-21

### Added
- **A curated practice-trait set in the library** — eleven new opt-in traits distilled from
  Anthropic's prompt-engineering guidance, the Claude Code plugins (skills and prompt-snippet
  references as well as the output-style hooks), OpenAI's agent prompting guide, and the
  self-correction/verification literature:
  `evidence-discipline` (every reported claim traced to an observation; verified-or-not as a
  binary, never a confidence score), `decision-commitment` (choose and stop re-deciding),
  `error-recovery` (read a failed observation before reacting; two failures at one step means the
  approach is wrong), `change-restraint` (the smallest change that does the job),
  `independent-verification` (check from outside the context that produced the work — a mechanical
  check, else a `subtask` verifier briefed without your reasoning), `review-recall` (find first,
  filter second), `teaching-insights` (explain the reasoning where a human is reading),
  `interface-design` (build UI that looks chosen rather than generated — pin the subject, avoid
  the current default looks, plan and critique a token system before coding) and `interface-copy`
  (words as design material: name things by what the reader controls, errors that explain and
  direct, one job per element), `test-design` (a test earns its place by failing — name the
  regression first, assert behaviour not internals, watch it fail once) and `failure-visibility`
  (error handling written INTO code: never catch without a reaction, enumerate what a broad catch
  would swallow, stubs never ship).
  None is a default: each is picked per routine/conversation, **the trait itself is the on/off
  switch**, and a trait that is off contributes nothing to the prompt. They reach existing
  instances at the next daemon boot via `bootstrap.sync_seed_library_docs` and ride the normal
  `library-sync` to the library repo — no new mechanism, no always-on block.
- **`docs/curated-traits.md`** (new Help-tab guide) — per-trait provenance and *evidence strength*,
  the reasoning behind shipping these as selectable traits rather than one always-on prompt
  extension (the prompt's scarce resource is attention, not cached tokens), and the candidates
  evaluated and **rejected on evidence**: self-critique-before-finishing (measurably net-negative
  unaided), "don't be sycophantic" (the least effective mitigation tested), numeric confidence
  (systematically overconfident), plus the ones this harness already covers structurally.

### Changed
- `suggest_traits_permissions` now tells the wizard when each curated trait is worth taking — and
  explicitly not to take the set by default, since every trait costs prompt on every run it is on.

## [0.79.1] — 2026-07-21

### Fixed
- **A finish→reopen no longer loses the pre-finish leg's util histogram and counters in
  `status.json` (F140 — completes the F131/F132 fix).** The boot-time `prior_counters` reseed
  (0.76.1) rehydrates a resumed leg's cumulative telemetry from the prior leg's `status.json`,
  but `Runner.resume()` overwrote that file with a bare `{state:queued, turn:0, …}` dict — no
  `utils`, no `asks_deferred`/`schema_retries`/… — *before* the engine booted, so the reseed
  read an already-clobbered file and carried nothing forward. Observed: a reopened run with 9
  real util calls reported only 2 in `status.json` (and `utils:{}` on the double-finish path).
  The queued-status write is now a shared `_queued_status()` helper that, on resume, merges the
  prior leg's telemetry forward (transient run-state fields still reset); a fresh run is
  unchanged. The global util-stats snapshot was always correct (transcript-derived); this only
  repairs the per-run `status.json` + finish event. Regression-guarded by a round-trip test
  asserting the resume write is lossless w.r.t. `prior_counters`.

## [0.79.0] — 2026-07-20

### Added
- **Settings → Secrets: manage multi-entry (JSON-map) secrets one entry at a time.** A secret whose
  value is a JSON object (e.g. `FTP_SOURCES` holding several FTP servers) can be extended without
  re-typing the whole write-only blob: the card lists the entry NAMES (never the values) with a
  per-entry delete, and an "add / replace entry" form merges a single entry SERVER-side (the other
  entries' values are never returned). New endpoints `PUT /settings/secrets/{key}/entry` and
  `DELETE /settings/secrets/{key}/entry/{name}`; the listing gained a `maps` field. Plus a
  show/hide toggle on the secret-value field, since a JSON map is unreadable when masked.

## [0.78.4] — 2026-07-20

### Added
- **Settings → Secrets now shows each needed secret's FORMAT.** A "format / help" expander per
  entry reveals the declaring util's `usage:` + docstring, so a structured secret's shape (e.g.
  `FTP_SOURCES` is a JSON map `{name: {host, user, pass, port?, tls?, dir?}}`) is discoverable
  right where you set it — not only in the util source. `utils_lib.parse_header` now returns the
  docstring; the needed-secrets API carries the declaring util's usage + doc.

## [0.78.3] — 2026-07-20

### Fixed
- **Settings → Secrets no longer lists OAuth connection tokens (e.g. `NOTION_ACCESS_TOKEN`) as
  "needed" secrets.** A util declares them only so the sandbox lets the ENGINE-injected token
  through — the user never *sets* them (they come from binding a connection on a routine), so
  prompting for them was misleading. The needed-secrets list now excludes every provider's
  `<PROVIDER>_ACCESS_TOKEN` (`oauth.providers.connection_token_vars`, now also the single source
  for that injected-var name, used by `store.tokens_for_routine` too).

## [0.78.2] — 2026-07-20

### Fixed
- **The "declare the credential env vars you read" util gate had a blind spot** (`utils_lib.
  _secrets_read`): it caught direct literals (`os.environ["X_TOKEN"]`) and single-constant
  indirection (the `gu claude` `TOKEN_VAR = "…"` pattern) but **not a tuple/list of names looped
  over `os.environ`** — `KEYS = ("A_PASS", …); for k in KEYS: os.environ.get(k)`. So the `ftp`
  util shipped without declaring `FTP_PASS`, which the sandbox then silently scrubbed from a
  routine's util subprocess (its FTP creds never arrived). The gate now resolves grouped
  tuple/list constants; a full library sweep found `ftp` was the only offender (its `secrets:`
  line is fixed in the utils library). Credentials set in Settings → Secrets now reach the util.

## [0.78.1] — 2026-07-20

### Added
- **Settings → Connections: each provider row now links straight to where you create its OAuth
  app** ("create app ↗" → the provider's dev console: Notion my-integrations, Google Cloud
  credentials, Slack apps), via a new `console_url` on the provider registry. No more hunting for
  the right page.

## [0.78.0] — 2026-07-20

### Added
- **A sticky side table-of-contents on long pages** (`static/components/toc.js`): on wide viewports
  a fixed rail parks in the right margin (mirroring the run/conversation rails, same 1560px
  breakpoint), listing the view's `<h2>` sections with click-to-scroll and the in-view section
  highlighted. Mounted generically by the router for any view with ≥2 headings; skipped on views
  that already carry their own rail/nav. Hidden below 1560px.

### Changed
- **Settings → Connections: the Public URL field now pre-fills from the browser's origin** (when
  it's https and nothing is saved), so you rarely type it — the URL you reached the console at is
  the redirect base you want.

## [0.77.1] — 2026-07-20

### Fixed
- **Settings → Connections: the OAuth base-URL field was mislabeled "Redirect URL"**, which invited
  pasting the full `…/oauth/callback` (doubling the path). Renamed to **"Public URL"** with a
  "base, not a path" note, and the card now derives + shows the exact callback
  (`<public_url>/oauth/callback`) to register in the provider, with a copy button.

## [0.77.0] — 2026-07-20

### Added
- **OAuth connections** (docs/oauth-connections.md): connect an external service account (Notion
  first) via OAuth in the web UI, and a routine acts on its behalf headlessly. A connection is a
  RESOURCE binding (routine.yaml `connections:` provider→account, like `models:`), never a
  capability. Consent + refresh run in the daemon/web process; a run only READS a short-lived
  access token from disk (the engine↔daemon boundary is filesystem-only).
  - `oauth/providers.py` — provider registry (Notion implemented: auth-code + PKCE, long-lived
    token, no device flow; Google/Slack scaffolds; app creds in the Secrets store as
    `<PROVIDER>_OAUTH_CLIENT_ID`/`_OAUTH_CLIENT_SECRET`). `oauth/store.py` — the daemon-owned
    `connections.json` (0600, single writer + lock, metadata-only listing).
  - `web/settings/oauth.py` + a PUBLIC `GET /oauth/callback` (bearer-exempt like the webhook route;
    the per-flow `state` + PKCE-S256 are the guards) + a Settings → Connections card. New
    `ServerConfig.public_url` builds the redirect URI (e.g. a Tailscale Serve https URL).
  - `daemon/oauth_refresh.py` — `OAuthRefreshManager` refreshes expiring tokens on the scheduler
    tick, persists refresh-token rotation, flags `needs_reauth` + notifies on rejection (a no-op
    for non-expiring providers like Notion).
  - Engine injection: a routine's bound connections reach a util as `<PROVIDER>_ACCESS_TOKEN` via
    `run_util(extra_secrets=…)` / `_child_env`, but ONLY if the util declares the var — the
    declared-only sandbox invariant, extended to engine-provided tokens. The `notion` global util
    was revised to read `NOTION_ACCESS_TOKEN`.

## [0.76.3] — 2026-07-20

### Changed
- **`ruff check` and `mypy` now run inside pytest (`tests/test_quality.py`), so the one gate the
  engine actually enforces covers them.** CLAUDE.md requires both green on the FULL repo every
  commit and relies on pre-commit — but the daemon commits programmatically (git hooks bypassed),
  pre-commit is not installed on the deployment, and self-audit's only hard gate is `pytest-run`.
  The F97 external audit found the tree had been RED (11 ruff + 8 mypy errors from the Jul-19
  toolchain bump, ruff 0.15.21 / mypy 2.3.0) across 0.72–0.76 with every commit sailing over it,
  because a run only lints the files it changed. Running the two gates as tests means a red
  full-repo can never be committed silently again — a red suite reverts, and the checks also cover
  the live tree's pending edits. Skips cleanly in a minimal env without the dev tools; the commit
  gate always has them. (Companion to the same audit's 0.76.2 fixes.)

## [0.76.2] — 2026-07-20

### Fixed
- **F97, actually fixed — the util-stats snapshot dir was never writable in the container,
  and the four-release chase (0.68.0–0.68.3) diagnosed the wrong `~/.local`.** External audit
  on the host (reviewer-reserved for 2026-07-20) settled it: the snapshot file
  (`~/.local/state/routine-scheduler/util-stats.json`) has **never existed** — not on the host,
  not in the container. The daemon runs as uid 1000 in Docker, and the container's
  `/home/mark/.local` is `root:root`: the entrypoint (`deploy/docker-entrypoint.sh`, run as
  root) does `mkdir -p ~/.local/share/routine-scheduler-libraries` for that bind mount —
  creating `~/.local` + `~/.local/share` as root — then chowns only the *leaf* to `mark`, so
  `~/.local` and `~/.local/state` stay root-owned and the writer's `mkdir(~/.local/state/
  routine-scheduler)` raises `PermissionError` (reproduced: `docker exec -u 1000 rsched mkdir -p
  …/state/…` → *Permission denied*). The 0.68.3 fix chowned the **host's** `~/.local`, which is
  irrelevant — `~/.local/state` is not a bind mount, so the daemon writes into the container's
  own root-owned tree; and the routine's "stale mount-namespace" note was a misdiagnosis (the
  `util-stats` util's 404 was correct — the file genuinely was absent). Fix: add
  `~/.local/state` to the entrypoint's chown loop, so the uid-1000 daemon can create any XDG
  state subdir it needs (now and for future consumers). Takes effect on image rebuild +
  container recreate.
- **A cleanly-finishing engine subprocess's WARNING/ERROR logs no longer vanish — the reason
  F97 hid for four releases.** The daemon spawns each `engine-run` with `stdout=DEVNULL,
  stderr=PIPE` and only surfaced that stderr on a *crash* (`_reap`), so the 0.68.1/0.68.3
  snapshot-write breadcrumb — emitted by the engine on a successful finish — was silently
  dropped ("never silent again" was still silent). `_reap` now re-emits a tail of any
  WARNING/ERROR/CRITICAL/traceback lines (new pure `_notable_stderr` helper, tail-capped so a
  chatty run can't flood the log) into the daemon log (→ `docker logs`), so a persistent
  non-fatal failure is diagnosable from the outside. Unit + integration tested
  (`tests/test_scheduler.py`).

## [0.76.1] — 2026-07-20

### Fixed
- **A resumed run reset its per-run telemetry counters to the resumed leg's own tally
  (self-audit F131/F132; bug report from routine-improver 2026-07-20).** A finish→reopen (an
  operator message injected after an authored finish) starts a fresh `RunContext`, and `boot`
  rehydrated the token-spend base, grounding set, and turn base from the transcript — but NOT
  the cumulative counters mirrored to `status.json` and the finish event. So a reopened run's
  `status.json` showed `utils: {}` and `asks_deferred: 0` (plus `schema_retries` /
  `schema_forcefails` / `referrals`) despite the pre-finish leg's real activity — e.g.
  global-utils-review 2026-07-19 recorded four real util calls yet reported an empty util
  histogram, nearly tripping a false finish-claim-of-unperformed-work flag. Fix: on resume,
  `boot` reseeds these counters from the prior leg's `status.json` (the run dir is reused
  across legs) before the first `write_status` overwrites the file — the same
  cumulative-across-legs guarantee `usage_base` already gives token spend. The GLOBAL
  util-stats snapshot was always correct (it is transcript-derived); this repairs only the
  per-run `status.json` and finish event. New `history.prior_counters` helper (unit-tested) +
  a resume integration test.

## [0.76.0] — 2026-07-19

### Fixed
- **The `remove_util` action permission reverted to unchecked on every "Save permissions"
  (self-audit F130; operator bug report filed from global-utils-review 2026-07-19).** Enabling
  the `remove_util` toggle never persisted, so an operator-approved util removal
  (`pagedrop-publish`, unused and now failing its own selftest with 403) stayed un-executable
  for 5 consecutive runs. Root cause: `floor_capabilities` keeps a gated action only when a HELD
  permission doc's `requires.actions` names it, but `permissions/util-authoring.md` was seeded
  before `remove_util` existed and lists only `write_util` — so the floor stripped `remove_util`
  on every save. Fix: `floor_capabilities` now also keeps a gated kind whose canonical source
  permission (`_DEFAULT_KIND_SOURCE`, e.g. `remove_util → util-authoring`) is held, closing the
  "library predates the kind" gap generically. The RAISE (`capabilities_for`) is unchanged, so
  merely holding util-authoring does NOT auto-enable `remove_util` — it stays an explicit opt-in
  that now persists.

### Added
- **`write_file` observation reports the file's total `size` after the write (self-audit F129;
  bug report: an `append:true` appeared to overwrite a file's existing content).** The
  observation carried only `bytes` written, so an append that silently overwrote was
  indistinguishable from a genuine append. It now includes `size` (total on-disk bytes) and the
  append observation reads *"wrote N bytes … (appended; file now M bytes)"* — a true append shows
  `size == prior + bytes`, an overwrite shows `size == bytes`, making the class provable from the
  observation alone. (`do_write_file`'s append path itself is correct — `open("a")` — so no
  overwrite was reproducible in code; this is the diagnostic for a future occurrence.)

## [0.75.0] — 2026-07-19

### Added
- **Reject an ok-finish that CLAIMS a high-signal action the run never took (reviewer AUDIT
  decision D31=B; self-audit finding F127).** A routine wrote *"Filed report_bug to
  self-audit"* in its finish summary while taking no `report_bug` action — narrated unperformed
  work that the old fabrication guard (which only rejects an ok-finish taken as the very
  *first* action) let through. New `src/rsched/engine/finish_guard.py` `unbacked_action_claims()`
  scans a top-level ok-finish summary for the literal engine token of `report_bug` / `ask_user`
  / `schedule_run` bound to an affirmative completion verb; when that action was never taken
  this run the finish is rejected with an instruction to either take the action or drop the
  claim. Deliberately narrow (precision over recall on the shared run path): only literal action
  tokens (never natural-language paraphrases), negations are excluded, and **meta routines**
  (tag `meta` — self-audit, routine-improver, config-optimizer, token-lab, clarification) are
  EXEMPT because their job is to quote and analyse *other* runs' actions (a universal check would
  false-reject the auditor's own summaries). Covered by `tests/test_finish_guard.py` (incl. the
  real radar summary as the positive) and a `tests/test_loop.py` integration test.

## [0.74.1] — 2026-07-19

### Changed
- **Auto-rerun the flaky Playwright UI suite (reviewer AUDIT decision D30=A).** The browser
  UI tests are non-deterministic under `pytest-xdist` (browser/timing/shared-resource
  contention between parallel workers occasionally reds a genuinely-passing test on a
  full-suite run — F120), which corrodes the hard test-gate. `pytest-rerunfailures>=14.0` is
  now a dev dependency and a `pytest_collection_modifyitems` hook in `tests/ui/conftest.py`
  applies `flaky(reruns=2)` to every `tests/ui` test (scoped there so the rest of the suite
  keeps failing fast). Reruns fire ONLY on failure — an intermittent blip passes on retry, a
  real regression still fails all attempts. The `flaky` marker is registered in `pyproject.toml`
  so it is warning-clean under `filterwarnings=error` even while the plugin is absent.
  - **Note on activation:** declaring the dep in `pyproject.toml` does not install it into the
    project venv `/opt/rsched-venv`, which is read-only to routines; the reruns stay **inert**
    until the venv owner runs `uv sync` (a one-time out-of-band step). Until then the wiring is
    committed and the gate is unaffected. The earlier hard blocker — that merely adding the dep
    made `uv run` try to sync the read-only venv and crash the gate — is resolved out-of-band by
    the `pytest-run`/`rsched-lint` utils' `uv run --no-sync` fallback.

## [0.74.0] — 2026-07-19

### Added
- **`report_bug` — an ungated, default-on "report potential bugs" action for EVERY routine
  (reviewer AUDIT decision D29=A).** Any run — at any depth — may file a bug report about the
  scheduler itself (engine, a util's CLI, the web UI, a workflow) with a one-line `title` and
  optional `detail`. It appends a structured entry
  (`{ts, routine, run_id, title, detail}`) to `<routines_home>/.control/bug-reports.jsonl`
  (new `rsched.bug_reports` module, best-effort append modeled on the health-events log) and
  does not interrupt anyone or reach the user. `report_bug` joins `finish` in the new
  `ALWAYS_KINDS` set: it bypasses both the workflow `tools:` allowlist and the capability
  gate (it is not a `GATED_KIND`), so it is available to every routine with no capability to
  enable. The self-audit routine's gather-evidence reads this stream each run and turns
  unresolved entries into findings (recipe wiring tracked separately for the routine-improver).
  New action schema fields `title`/`detail`; handler `interact.handle_report_bug`; observation
  rendering; composer + `docs/prompt-anatomy.md` action-list entries. Tests in
  `tests/test_report_bug.py` (+ the `report_bug` case in the `test_actions.py` valid-actions
  matrix).

## [0.73.0] — 2026-07-19

### Fixed
- **Decision answers now sync to the Audit page too (reviewer AUDIT note: "responses to
  decisions are still not synced everywhere the decision surfaces").** The Audit tab
  reconstructed a decision's answered-state from the still-queued `pending_feedback` inbox
  messages ALONE, so the moment a self-audit run consumed a decision's feedback message the
  Audit page re-presented that answered decision as `open` — while the Decisions page kept it
  hidden via the durable `audit/decisions-answered.json` marker. `/api/audit` now also emits
  `answered_decisions` (the marker ids answered at-or-after the report's `generated`, the same
  rule `_audit_decisions` uses); `static/views/audit.js` reads it and shows those decisions as
  **answered** (not open), hiding their options. The two surfaces now agree.
  `web/api_audit.py`, `static/views/audit.js`.

### Changed
- **`schedule_run` unknown-target now teaches the caller which slugs are valid.** Arming a
  one-shot on an unknown routine returned a bare `unknown_target` — a scheduling routine
  guessing a sibling's slug (observed: `train-seat-finder-scheduler` burned turns guessing
  `bahnbonus-seat-finder`/`-position-finder` before the real `bahnbonus-seat-position`, even
  building a new util + asking the user). The `unknown_target` observation now carries
  `valid_targets` (every sibling routine slug) and `suggestions` (fuzzy close matches), and the
  formatted observation prints "Did you mean …? Valid target slugs: …".
  `engine/interact.py`, `engine/observations.py`.

## [0.72.1] — 2026-07-19

### Fixed
- **Conversation title/tags now use the conversation's OWN model, not the system model.**
  `conversations.autolabel` resolved the title+tags via `EndpointRegistry.for_system()`, so a
  conversation pinned to an uncensored model still had its title generated by the default
  system model — which could refuse (e.g. a title reading "denied request"). It now resolves
  `for_model("main", <the conversation's models>)` — the same model its replies use — with the
  system model kept as the fallback when the conversation pins none. (Reviewer AUDIT note.)

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

