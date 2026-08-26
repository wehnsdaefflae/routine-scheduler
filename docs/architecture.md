# Architecture reference

How routine-scheduler is put together, subsystem by subsystem. This is the DETAIL tier:
`CLAUDE.md` carries the working conventions, the commands and the core contracts an agent
needs before touching anything; everything below is here to be looked up when the work
actually reaches a subsystem.

Deeper single-topic guides live beside this one: subtasks, background tasks, triggers,
schedule-once, conversations, playbooks, rules & permissions, sandboxing, OAuth
connections, remote machines, notifications, search, run analytics, prompt anatomy,
endpoints, authoring.

## How a run works (engine/)

The turn loop (`engine/loop.py`) is the heart; `engine/runtime.py` is the entry above it
(`run_routine`, workflow loading/decomposition), `engine/boot.py` the initial message list
(kickoff or resume rehydration), `engine/completion.py` the get-one-valid-action side (schema
retries, model failover incl. classifier refusals, refusal clarification, media fallback, the
compaction gate), `engine/control.py` the
between-turns control plane (abort, pause gate, `control.json` model switch, injection drain,
subrun announcements), and
`engine/interact.py` the user-conversing handlers (`ask_user`, grant-gated `write_util`). Each turn:
check budgets → pause gate → drain injected user messages (`inbox.py`) → announce finished subruns →
get ONE valid action from the model (3 attempts: up to 2 schema-retries) → dispatch → append the observation →
repeat until `finish`. A `finish` emitted while an undrained user message waits (the message landed
AFTER this turn's drain — the finish-window race, R108) is deferred, not executed: the loop rejects
it as an observation and delivers the message as the next turn, so no instruction needs a manual
"resume" to be seen; only the spent reserved-finish turn ends anyway, its summary naming the
still-queued message (delivered by the next leg's boot drain). **Budgets are one unified primitive** (`engine/budget.py`: a `Budget` = a stop
condition over a resource, a `BudgetLedger` over them, `allocate()` for child slices) shared by the run,
a conversation reply window, a subtask, and a subrun — `RunContext` holds the live meter, the ledger holds
the limits (single-writer status.json preserved).
- **One action per turn** is enforced: the model returns a single JSON object matching `ACTION_SCHEMA`;
  `normalize_action` + `validate_action` (`engine/actions.py`) repair grammar debris from weak/constrained
  models and return precise per-kind errors. `actions.py` is the single source of truth for what a turn
  may do — adapters, UI, and the CLI event renderer all key off it. A workflow's `tools:` allowlist AND
  the routine's **capabilities** (`grants.py`) are enforced there too: allowed kinds = workflow tools
  ∩ (base ∪ enabled capabilities), plus path gates (runs/ needs the previous-runs depth; a run NEVER
  writes its own recipe — main.md / stages/ / **tuning.yaml** — a fixed rule unlocked only
  by a user-granted fs_write_root covering the routine dir (the routine-improver's case) OR a
  per-leg **revise marker** (`engine/revise.py`: the run-view "revise recipe" mode drops one, the
  loop reads it ONCE and grants recipe self-write + the file-edit kinds for that leg only);
  `routine.yaml` is NEVER writable by any run, even under an fs_write_root or a revise — config is the user's,
  no exceptions (the block is by FILENAME anywhere the run can write, external repos included — a
  deliberate over-block: any file named routine.yaml is treated as config); the machine-tunable knobs live in tuning.yaml, where the FILE boundary is the
  permission boundary; executor.py backstops absolute paths and scopes `runs: last`).
  A disallowed/switched-off call is corrected inside the schema-retry cycle with an error naming the
  covering permission, and never becomes a turn.
- **The system prompt is composed once at boot** (`engine/composer.py`; the CAPABILITIES
  section in `engine/capabilities.py`, observation rendering in `engine/observations.py`):
  harness contract → action schema
  + example → workflow body (the routine's own `main.md`, ending in a `## Standing practices` tail that
  names the general rules it holds — their prose is NEVER inlined; a SUBRUN inserts its INSTRUCTION brief
  here, a top-level routine does NOT — its task is baked into the recipe) → **capabilities** (model +
  context window, the action kinds usable this run, enabled capabilities + held permissions' short
  conduct notes,
  spawnable workflow patterns, the util catalog at name+summary altitude — ONE util's usage on demand via
  `util name=list args=["<name>"]`) → **state digest** (phase, `state/`, stage modules, held rules, last result,
  LEDGER tail, open/answered questions, inbox messages). Effect actions (`util`/`read_file`/`write_file`/
  `edit_file`/`llm`) run through `engine/executor.py`. A default routine's composed prompt is ~25k chars;
  everything else is reachable on demand (read_file stages/history, read_rule, util name=list, memory_read).
- **The message list is a prompt-caching contract**: composed once, appended-to only, never mutated —
  so providers serve each turn's prefix from cache (~0.1x). Per-turn boilerplate is banned: the util
  reminder is ONE-SHOT on the kickoff/resume note, the history pointer re-appears only every 10th turn,
  and schema-retry debris is dropped from the live prompt once a retry succeeds (the transcript keeps
  the error events). Cache traffic reports as usage `cached_in`/`cache_write` (kept OUT of `in`, so
  token budgets keep their meaning); the loop hands every completion a stable `session` key (str(run_dir))
  that adapters may use as a cache hint. THREE sanctioned exceptions rewrite the list in place:
  compaction (below), schema-retry debris cleanup, and the media fallback (a failed image turn's
  tail message is rewritten text-only) — each invalidates the provider cache once, by design.
- **Compaction archives context to a navigable on-disk history** (`history.compact_to_history`): when
  the prompt exceeds ~60% of the resolved model's `context_chars` — ~80% once cache hits are observed
  (compaction rewrites the prefix and invalidates the cache, so carried context is cheaper than
  re-archiving) — the middle turns are reorganized into markdown files (~≤100 lines each) under
  `runs/<ts>/history/` + `INDEX.md`; the prompt keeps only a pointer. The archival call runs on the
  routine's `tool_call` model when its window fits (machine work; main model is the fallback) and its
  spend is folded into the run's usage. Falls back to the deterministic one-line digest
  (`history.maybe_compact`) on any failure. The on-disk transcript keeps everything regardless.
- **Phase is derived, never bookkept**: the stage modules ARE the states — `statemap.py` builds the
  UI's state-graph diagram from the routine's own `stages/*.md`,
  in main.md first-mention order (nothing parsed from prose, so every routine has a diagram), and
  the engine tracks the run's live position from its stage-module READS: a `read_file` of
  `stages/<name>.md` stamps `ctx.phase` (executor) → status.json (every turn) → the run SSE `state`
  event, which also fires on phase change (`/stategraph` endpoints,
  `static/components/stategraph.js` — rendered in the run view's rail and the conversation artifact
  rail, current phase highlighted live). The transcript renderer chapters the say stream with
  labeled phase dividers from the same per-event `phase` stamp, so a run reads as a story. `state/phase.json` stays recipe-private state (the digest
  shows it); it does NOT drive the diagram. Every `assistant_action` transcript event is stamped
  with the ACTIVE phase, so the rail is also an instrument panel: `statemap.phase_stats` (served at
  `/api/runs/<id>/phases`) derives per-phase turns / tokens / cost / wall-clock from the
  transcript — dispatch time lands on the acting phase, completion time on the phase that produced
  the next action; a stage the run jumped over (zero recorded turns) renders as `skipped`, never a
  positional ✓. The sibling read-model
  `fileactivity.py` (`/api/runs/<id>/files` → `components/fileactivity.js`) derives per-file
  read/write/edit counts from the same transcript's OBSERVATION events — so subruns and user slash
  commands count — feeding the rail's files card on the run view and the conversation.
- **A run resumes where it left off** (`run_routine(resume_from=…)`, `EngineLoop(resume=True)`): the
  transcript is replayed into the message list (`history.replay_messages`) with a fresh budget window
  (`budget_base_turn`); usage REPORTING stays cumulative across legs (`history.prior_usage` →
  `ctx.usage_base`; budgets ignore it). The **model can be switched mid-run** — a `control.json`
  `switch_model` signal applied at the turn boundary (`for_model` re-resolves every turn).

## Endpoints (endpoints/) — transports, not agents

Chat-completion adapters implementing one `ChatEndpoint.complete(...)` (`base.py` — tenacity retries on
retryable `EndpointError`s; a 200 with an unparseable body is one of them). All three honor
`ModelRef.effort` and report prompt-cache traffic as usage `cached_in`/`cache_write` (kept out of `in`).
`complete()` takes an optional `session` caching hint (a stable key per run) adapters may ignore.
The credential ladder (`resolve_api_key`: inline api_key → key_var in Secrets → key_env_file; claude-cli:
env → inline → Secrets → credentials_env) has label-only mirrors BESIDE the resolvers
(`api_key_source` / `token_source`) feeding the Settings card's "credential in use" line — which rung is
live, plus a shadow warning when an inline key hides a set secret; key values never leave the server.
Three kinds:
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
`temperature`, `max_tokens` (each None inherits the endpoint kind default / the endpoint's own
value; `max_tokens` — the model's real OUTPUT limit, sent on every engine call — falls back to
`DEFAULT_MODEL_MAX_TOKENS` 16_384, and Settings flags unset/implausible values so "set correctly"
is auditable), plus `fallbacks:` — the ordered FAILOVER chain (catalog names, NOT transitive).
Endpoints hold only transport + auth + those DEFAULTS; `multimodal` is NOT on the endpoint (one
endpoint serves many models with different windows and vision support). Each **routine
references models BY NAME** (`routine.yaml` `models:` maps a role → catalog name): `main` (the
loop — and every spawned child's default), `tool_call` (the `llm` action), optional
`uncensored`. A role left unset falls back to the server's single `system_model` (also a catalog
name) — the ONE model for pre-routine machine work (the clarify flow + workflow
generation/suggestion). A single `llm`/`spawn`/`subtask` call may override its model per call
(`model:` a role — main|tool_call|uncensored — or a CATALOG model name; D81, extended by
the 2026-08-22 order retiring the subroutine role. The `list_models` action shows the
catalog; an unconfigured `uncensored` or an unknown name is rejected with a teaching note). `EndpointRegistry.resolve(name)` /
`.for_model(kind, routine.models)` / `.for_system()` produce a RESOLVED `ModelRef` (endpoint,
model id, effort + the filled-in multimodal/context_chars/temperature/max_tokens) — the runtime
handle, no longer parsed from yaml. `supports_media(mime, *, multimodal)` and compaction
(`ref.context_chars`) take the resolved model's values; `complete()` gains a `temperature` kwarg.
Editing a catalog model updates every routine that names it. **Failover** (`endpoints/failover.py`)
is two-level: a hard EndpointError anywhere (marked centrally in `InstrumentedEndpoint`) puts that
(endpoint, model id) in a 5-min process-local COOLDOWN, and every role resolution
(`for_model`/`for_uncensored`/`for_system`) picks the first not-cooling chain member — while the
engine's turn completion (`completion.py`) additionally advances down the chain MID-TURN on a hard
failure, logging the switch as a transcript `error` event with a `failover` payload (never a new
event type) and stamping each turn's `usage.model` with the model that actually served it, so
status.json (`ctx.main_model`) and spend attribution stay truthful. Chain exhausted → the run
fails exactly as before; models without `fallbacks` behave exactly as before.

## OAuth connections (oauth/)

A **connection** lets a routine act against a third-party service (Notion first) on behalf of an
external account. It is a RESOURCE binding like models/fs_roots — the routine.yaml `connections:`
map (provider → account label) IS the grant; no run creates or changes one, and there is no
capability layer. **Consent + refresh live in the daemon/web process** (a headless sandboxed run
can do neither — consent needs a browser, refresh must WRITE the token store); a run only READS a
short-lived access token from disk (the engine↔daemon boundary is filesystem-only). Pieces:
`oauth/providers.py` — the provider registry (non-secret endpoints + flags; Notion implemented —
auth-code + PKCE, long-lived token, no device flow; Google/Slack scaffolds; OAuth app creds in the
Secrets store as `<PROVIDER>_OAUTH_CLIENT_ID`/`_OAUTH_CLIENT_SECRET`). A `scoped` provider
(Google/Slack) reads its consent scopes from `<PROVIDER>_OAUTH_SCOPES` (`authorize_scopes`);
`authorize-start` ERRORS if that secret is unset — no hardcoded scope fallback, so a connection can
never silently consent to a narrower set than configured (`scoped=False` Notion sends no scope param).
`oauth/store.py` — the
daemon-owned connection store (one `connections.json` beside `config.yaml`, keyed
`<provider>:<account>`, atomic 0600, metadata-only listing, single writer + a lock;
`tokens_for_routine` maps a routine's bindings → env vars). `web/settings/oauth.py` — the flow:
`authorize-start` (authed, mints PKCE + `state`) → the user consents → the PUBLIC `GET
/oauth/callback` (mounted WITHOUT the bearer dep, like `api_hooks.hooks_router` — the unguessable
per-flow `state` is the CSRF guard) exchanges the code and writes the connection; the new
`ServerConfig.public_url` (external https URL, e.g. Tailscale Serve) builds the redirect_uri.
`daemon/oauth_refresh.py` (`OAuthRefreshManager`, ticked by the scheduler like the trigger/detached
managers) refreshes EXPIRING tokens near expiry, persists rotation and flags `needs_reauth` on
rejection — which badges it in Settings → Connections, the only notification there is (0.230.0
deleted every implicit outbound send) — a no-op for non-expiring providers (Notion). **Engine injection**:
`executor.do_util` resolves the routine's bound connections to `{<PROVIDER>_ACCESS_TOKEN: token}`
and passes them to `utils_lib.run_util` as `extra_secrets`; `_child_env` injects each ONLY if the
util declares the var — so a token reaches a util iff the routine binds the connection AND the util
declares the var (the `notion` util declares `NOTION_ACCESS_TOKEN`). See docs/oauth-connections.md.

## Secrets — two scopes (secrets.py)

`secrets.env` beside config.yaml is the CENTRAL store: instance-wide names, so exposing one
to a routine is a decision (the four-state `secret:<NAME>` grant). `secrets.d/<slug>.env` is
a routine's OWN store (D103, operator decision 2026-08-26 — R497): `SFTP_USER` means
something different to every routine that has one, and one flat namespace forced them either
to collide or to be spelled by convention. A scoped secret is owned by its routine —
implicitly exposed to its runs, invisible to every other routine, and SHADOWING a central
value of the same name (it rides `executor._extra_secrets`, which wins the `_child_env`
merge). The exposure gate therefore subtracts a routine's own names before deciding anything
(`interact._own_secrets`), and CAPABILITIES lists the two sets apart so a run never spends a
turn requesting what it already holds. Both scopes stay under the declared-only rule. Write
surface: Settings → Secrets for the central store, the routine page's *Own secrets* section
(`web/api_routine_secrets.py`) for the scoped one — values write-only in both, names only on
the way out. Scoped stores live under the CONFIG dir, never in the routine dir, which is
`git add -A` autocommitted and auto-pushed; archiving a routine drops its store, so a
credential never outlives its owner or is inherited by a slug's reuse.

## Remote machines (machines.py)

A **machine** lets a routine run commands and move files on an SSH host (a GPU box, a build
server) — hardware the daemon box lacks. Like connections, it is a RESOURCE binding, not a
capability: the instance-wide `config.yaml` `machines:` catalog (`ServerConfig.machines` →
`config.MachineConfig`: host/user/port/`key_var`/`host_key`/workdir/description/tags) is
operator-only, the routine.yaml `machines: [names]` list IS the grant, and no run creates or
changes either. **No secret lives in the catalog**: `key_var` names a Secrets-store key holding
the private key (the one credential); `host_key` is the server's PUBLIC key, verified STRICTLY at
connect (paramiko `RejectPolicy` — no TOFU in a headless run). Pieces:
- **`machines.py`** — `machines_for_routine(names, catalog)` resolves a routine's bindings + the
  Secrets store into two env vars: `RSCHED_MACHINES` (non-secret connection metadata) and
  `RSCHED_MACHINE_KEYS` (`{name: PEM}`, a credential — its name ends in KEYS so the util-authoring
  gate forces its declaration). `machine_env_vars()` keeps both out of the Settings "needed
  secrets" list (like `connection_token_vars()`).
- **The reserved `remote` util** (`util-seed/utils/remote`, paramiko; needs the new
  `remote-machines` permission → `requires: utils: [remote]`, the same reserved-util mechanism as
  `shell`): `list` / `exec` (short blocking) / `submit`·`status`·`logs`·`cancel` (DETACHED jobs
  for long GPU work — a setsid process group, killable; `--notify-webhook <the routine's own
  trigger URL>` lets the job ping the routine on completion instead of polling) / `push`·`pull`
  (SFTP) / `scan-host` · `test`. Host keys pinned; a mismatch refuses.
- **Engine injection** mirrors OAuth: `executor._machine_env(ctx)` (merged with `_connection_env`
  in `_extra_secrets`) passes the two vars to `run_util` as `extra_secrets`, under the SAME
  declared-var gate — a key reaches a util iff the routine binds the machine AND the util declares
  the var. Bound machines are NAMED in the prompt's CAPABILITIES section (`capabilities_digest`),
  so the model knows its hardware without a discovery turn.
- **Filesystem shares** (`MachineConfig.share`): compute crosses via `remote exec`, the FILESYSTEM
  via an sshfs mount. A bound machine whose catalog entry sets `share` gets that remote dir mounted
  at `<routine>/mnt/<name>/` for the run — so ordinary filesystem utils act on remote files with no
  transfer (`machines.mount_routine_shares` / `unmount_routine_shares`, hooked in a try/finally in
  `runtime.run_routine`). The **engine** mounts (unsandboxed, like OAuth consent) so the key never
  enters a util; key + pinned `known_hosts` go to a daemon-private `<config>/.mounts/` dir the
  sandbox keeps invisible. The routine dir is already a sandbox write root and a Landlock rule on it
  COVERS the sshfs sub-mount (verified empirically) — no sandbox change, no `remote-machines`
  permission needed (that gates the compute util, not the filesystem). `mnt/` is gitignored so the
  autocommit never slurps the remote FS; mounting is best-effort (unreachable/no-sshfs → warn +
  proceed) and a crashed run's stale mount is cleared before the next remount. Docker: `sshfs` in
  the image + `/dev/fuse` + `CAP_SYS_ADMIN` + apparmor:unconfined in compose (inert without a share).
- **`web/settings/machines.py`** — Settings → Machines CRUD + `scan-host` + `test` (the last two
  run the real `remote` util server-side with `base_policy`, so what Settings proves is what a run
  gets); routine-page binding via `api_routines` PATCH `machines` (catalog-validated). `STRIP_VARS`
  now also scrubs `SSH_AUTH_SOCK`/`SSH_AGENT_PID` so a forwarded agent can't bypass the binding;
  `~/.ssh` stays sandbox-invisible (keys come from Secrets, not disk). The operator helper
  `deploy/setup-remote-agent-user.sh` provisions a hardened login on the remote host (no sudo,
  key-only, its OWN home co-owned by an admin, root-owned authorized_keys, video/render) so the
  util never logs in as an admin account. See docs/remote-machines.md.

## Routines on disk

A routine dir (`~/routines/<slug>`) owns its recipe — a run NEVER follows library prose directly
(the recipe is materialized in at creation; deliberate, narrow exceptions read the library AS DATA
mid-run: subtask/spawn materialization, gated in-run workflow generation, `read_rule` reads,
and the capabilities digest's catalog listing):
- `routine.yaml` — `description` (one-line UI summary, always present), schedule (cron + tz + catchup),
  `workflow: {library_slug, library_commit}` (provenance only), `models:` (role → catalog model NAME:
  main / tool_call / uncensored), `connections:` (provider → account label — OAuth
  connection bindings, a resource like models; see OAuth connections above),
  `permissions:` (held CONDUCT docs) + `capabilities:` (the engine-enforced surface: {actions, utils,
  confirm, runs, workflows} — both user-changeable only, side by side on the routine page with cascades between
  them; `workflows: catalog|generate` gates in-run pattern drafting for subtasks),
  `budgets:` (max_turns / max_total_turns (cumulative across resume windows) / wall_clock_min /
  total_tokens / max_cost (whole-$ ceiling) — the last four honor -1 = unlimited — / subruns /
  subrun_depth / ask_timeout_min — all editable in the UI, creation flow + routine page), `fs_read_roots` / `fs_write_roots`, retention —
  budgets/fs-roots/schedules are resources, never capabilities; `improve: false` opts the routine
  out of the routine-improver's passes (default: included); `triggers:` — event-driven fires
  alongside cron (docs/triggers.md): one canonical list of `{id, type, cooldown_s, …}` entries
  (`webhook` implemented — server-generated URL token IS the auth; `imap`/`watch_path` reserved
  in the same shape), validated in `rsched/triggers.py`, created/deleted on the routine page's
  Triggers card (never by a run; the library-sync routine's export REDACTS trigger tokens).
- `tuning.yaml` — the routine's machine-tunable BEHAVIOR parameters, classed with the RECIPE
  (improver-editable under its fs_write_root; config stays sealed — the file boundary IS the
  permission boundary). Today: `deliberation:` (terse|standard|deliberate|think-on-paper — how
  much thinking lands on paper: words the say contract, `engine/deliberation.py`; creation-suggested
  per task, slider on the routine page / conversation header, mid-run via control.json
  `set_deliberation` from the run view). Absent file = defaults; `config.load_tuning`/`write_tuning`
  are the one reader/writer pair; future machine-tunable knobs land here, never in routine.yaml.
- `main.md` — the workflow **decomposed and materialized into this routine** (an entry state-machine that
  routes to `stages/<name>.md` modules, read on demand, and ends with a Standing practices tail
  naming the general rules it holds). The clarified instruction is only a transient compile SEED —
  decomposed into the stages at creation and NOT persisted (a routine carries no `instruction.md`);
  the stages are the sole source of truth. The rules themselves are NOT here: they live once in the
  library and the run reads them with `read_rule`.
- `scripts/<name>.py` (`rsched/scripts.py`) — the routine's OWN persistent helper scripts:
  PEP 723 + the util docstring-header standard minus the catalog lines (no `calls:` graph;
  `gu` is NOT on PATH — a step needing a util's capability belongs in the recipe, and there
  is no model channel: a script is pure deterministic code). Deliberately TOOLING, not a
  co-equal interpreter: the recipe stays the single interpreter of the task and delegates
  judgment-free sub-steps (polling, parsing, calculations, fixed artifacts) so a repeating
  step is written once and re-run for free. Run via the `script` action (gated by the
  `script` capability; conduct: the `scripts` permission doc) in a persistent VENV inside
  the routine's workdir — `<routine>/.venv`, created on first use, the script's PEP 723
  deps installed into it (net-open build step, R40's prewarm rationale), gitignored against
  the `git add -A` autocommit. The jail is the run's fs roots (recipe and script read and
  write the SAME files); the env carries ONLY the granted secrets the script's header
  `secrets:` line declares (`NAME?` = optional, withheld when not granted; a declared,
  present, still-undecided secret files the util call's blocking exposure ask). Authored by
  the run itself with `write_file` (an own-dir write; the repo's autocommit versions it) or
  by the user; no approval dial — the blast radius is a subset of the routine's own
  sandboxed permissions. The capabilities digest lists the routine's scripts when the
  capability is held; children run with capabilities off, so a subrun never sees the kind.
- `.util_outputs/<run-ts>/t<turn>-<util>.out|.err` (`engine/outputs.py`) — util output too large for
  the observation that carried it, saved in full instead of destroyed. A util's stdout is captured up
  to 1 MB (`utils_lib.OUTPUT_CAP`) and then head+tail truncated to 8k for the observation, and the
  transcript records the TRUNCATED payload — so that band had no survivor, and re-running is not the
  same data for a fetch, a paid call, or a mailbox read. ONLY truncated output is kept: an output the
  observation carried whole is already in the transcript verbatim. The pointer rides the observation
  that lost the middle (so the store needs no index), earlier runs' spills reach the next run through
  the state digest, and reads are ordinary `read_file` — which pages by line window, making a big
  output cheaper on disk than it ever was in context. Engine-owned and read-only for the run (like
  `runs/`), gitignored on first use (the run-end autocommit is `git add -A` and util output can carry
  tokens), never search-indexed, pruned to the last `KEEP_RUNS` runs.
- `state/`, `LEDGER.md`, `inbox/` (daemon/web drop messages + answers here), `questions/pending/`
  (the ONE decision-record shape: {mode, type, default, expires, request?} — asks, util approvals
  and access requests alike; `routine.yaml` additionally carries the `grants:` decision rows:
  deny-forever tombstones for any entity + secret exposure, written only by the web),
  `runs/<ts>/` (transcripts + status.json incl. usage/turns/elapsed_s + the finish `outcome` —
  `state` folds a partial finish into "finished", the outcome field keeps it distinguishable —
  the dashboard's sortable per-routine stats AND its run-history **heartbeat strip**
  (`components/heartbeat.js`: last 15 runs per card/row via the cards' `recent_runs`, green ok /
  amber partial / red failed / grey aborted, height = tokens, click opens the run). status.json
  also carries `recipe_commit` (the recipe VERSION that produced the run: the last recipe-touching
  commit, stamped at run start by `recipes.current_recipe_commit`, which first snapshots any
  uncommitted recipe edits — the improver's — into a recipe-only commit; null for unversioned
  dirs), `utils` (per-util outcome counts) and `asks_deferred`; gitignored, keep-last-N with gzip).
  The engine commits the working dir
  automatically — routines never run git themselves. **Recipe health** (`run_health.py`, routine
  page + `GET /routines/{slug}/health`) buckets the durable usage records by recipe version and
  flags the newest recipe change when the runs after it are clearly worse than the runs before
  (deterministic thresholds, each constant justified in the module — see docs/run-analytics.md);
  `POST /routines/{slug}/recipe/revert` is the one-click rollback (recipe files only — never
  routine.yaml or state; 409 while a run is active). Flag-first: the improver never auto-reverts.

## Child tasks (subtasks + subruns), questions, injection

- **A subtask and a subroutine are the same thing** — a child task materialized from a workflow
  pattern and run recursively (`engine/childrun.py` `build_child`, tree on disk under
  `runs/<ts>/sub/<n>/`), differing only in SCHEDULING and budget. A child's fs roots EXTEND the
  parent's — the parent routine dir and every configured/granted root stay reachable from
  `sub/<n>` (F185; capabilities stay off, resources inherit). Both are NON-BLOCKING background
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
  recursive tree is visualized live in the run/conversation rail (`rsched/readmodels/tasktree.py` →
  `static/components/tasktree.js`). `subrun_start`/`subrun_end` events carry `mode` (sequential/parallel)
  + the child's allotted budget — payload EXTENSIONS, not new event types. Children running at an interruption are dead on resume — boot marks each aborted
  (`history.orphaned_children` → a synthesized `subrun_end`) and tells the model; finished-child
  results ARE replayed (announcements reconstituted from `subrun_end` events).
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
  durable `<owner>/inbox/` message, then WAKE (`runner.resume` if idle, else the live reply drains it)
  → rebuild `<owner>/state/background.json` → gc past a grace
  window. Detached runs are excluded from the restart drain gate (the child survives SIGTERM via
  `start_new_session`; disk-poll delivers post-restart) and use deferred asks only. Gated by the
  `background-tasks` permission (default-ON for conversations); action = `detach` (never call it
  "background" — that means the within-reply subtask). Monitor/cancel via `web/api_background.py`
  (`GET/POST …/background`, `…/background/{id}/cancel`); the rail renders the tasks. See
  docs/background-tasks.md.
- **ask_user** is `blocking` (poll `inbox/answer-<qid>.json` up to `ask_timeout_min`, then the run
  CONTINUES on the action's stated `default` and the record stays open as deferred) or `deferred`
  (filed to `questions/pending/`, surfaced in a later run's state digest). An ask may carry
  `request: "<entity-id>"` — a typed ACCESS REQUEST (entities.py; docs/rules-permissions.md's
  grant model): the record then settles ONLY on one of the typed allow/deny × now/forever
  decisions (plus *allow once* for turn-action classes, spent by the next dispatched matching
  action and then revoked — D65; the Decisions page's buttons; free text is held, D38),
  forever-decisions are applied
  to routine.yaml by the WEB at click time (`web/grants_apply.py` — the engine never writes
  config), and every decision seeds the run's in-memory overlay (`engine/requests.py`) at
  whichever seam consumes it: the blocking answer, boot (decided between runs), or the live
  turn boundary (a deferred ask answered mid-run — the running run's policy, schema and util
  sandbox pick the grant up at once). Blocking asks are durable records too, and the console is
  the ONLY surface that carries them: no channel mirrors a decision, and the engine and daemon
  make no implicit outbound send at all (0.230.0 — see docs/notifications.md). Browser push is
  the away-from-console tier, rendering the same open-decisions record. The web layer posts
  answers into `inbox/`. Decisions-page LIFECYCLE (fields on the one record shape, never a new
  type): a blocking ask can be **deferred to the next run** (a `{defer: true}` inbox marker —
  the engine unblocks on the stated default, the record stays open; stale markers are swept at
  boot), a non-blocking one **snoozed** (`snoozed_until` on the record → `snoozed: true` derived
  on read; hidden from the inbox + badge, still in the run's digest), and a routine with >5
  unanswered deferred asks gets a `decision_backlog` flag on its dashboard card.
  `~/routines/.control/reports.jsonl` is the append-only REPORT ledger every routine writes
  through the ungated `report` action (`R<n>` id, routine, title, detail, an optional `target`
  when the reporter can name the owner, plus event rows folded onto it: `delivered`, stamped
  when an addressed target's run drains the message, and `retracted`, appended when the user
  withdraws a not-yet-consumed delivery — the outbox's one write, docs/messages.md).
  Unaddressed rows are self-audit's triage queue; addressed
  ones are also delivered into the target's inbox for its next scheduled run — no run is
  started. The Messages page reads it, so a hand-off that carried is distinguishable from one that
  silently never arrived (docs/items.md). Every finished
  (sub)run appends to
  `~/routines/.control/workflow-usage.jsonl` — the routine-improver routine's evidence stream
  for the shared library it owns — its `library-pass` stage reads this stream each sweep —
  AND the durable spend series (tokens + cost + uncensored-referral count per finished run; run
  dirs fall to retention, this stream survives): `stats.monthly_spend` aggregates it per routine ×
  month — the Stats tab's "Monthly spend" table and the dashboard cards' compact month line
  (bg-task slugs attributed to their owner conversation; depth-0 entries only, a parent already
  folds its children in). Records carry payload EXTENSIONS (never a new shape): `recipe_commit`
  (health-by-recipe-version outlives retention), `utils` (per-util outcome counts — ok / error /
  usage_error (exit 2 = bad args) / missing / denied / rejected, counted in `RunContext.count_util`
  at the executor + validation seams; a denied call never becomes a turn, so it is counted where
  it is raised; subrun records carry their OWN counts, never folded into the parent), and
  `asks_deferred` (deferred-question churn). `util_stats.py` joins the stream with the library's
  git history (created/revised per util) and a stat-memoized transcript backfill for pre-stream
  runs — the Stats tab's "Global utils" table. The referral AUDIT (`ctx.referrals`: turns + llm
  calls the uncensored
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
chat message; the next user message resumes the SAME run in place (fresh budget window — turns,
wall clock, tokens and subruns all reset). The per-reply budget is a runaway BACKSTOP, not a pace:
what ends a reply is the work reaching a handover point (a finished plan step, a verified
deliverable, a decision for the user, a blocker). A conversation's spine is its own **working plan**
(`state/plan.md`, written and revised by the run, inlined at the top of every later reply by
`state_digest`) — the emergent counterpart to a routine's compiled `stages/` + `phase.json`.
- Runner: conversation replies draw from a **reserved interactive slot pool** (`INTERACTIVE_SLOTS`,
  3) — cron can't queue a chat reply and vice versa; `engine_cmd` targets `cfg.dir` (a path),
  which `_routine_dir` accepts. Run resolution in `api_runs`/`api_questions` is home-aware.
- **Slash commands**: the user can run the SAME effect actions/utils the model can, from the chat
  input (`/util …`, `/read_file …`, … — autocomplete + a reference panel fed by
  `GET …/commands`). A command-flagged inbox message EXECUTES at the turn boundary via
  `control.run_user_command` — parse (`engine/commands.py`) → the model action's exact
  validate_action gates → executor.dispatch — costing NO model turn; the observation lands in the
  transcript (`user_injection {command}` + `observation {user_command}` payload extensions) and in
  the model's context as one USER COMMAND message. **The speaker turn stays with the user**: when
  the model has handed the turn back (an authored finish) and the resuming message ONLY runs
  commands (`loop.leg_commands` and not `leg_prose`, boot sets `leg_after_authored`), the loop's
  command-only gate ends the leg after boot with NO model turn and NO reply
  (`loop._exit_commands_only` — no finish event, result.md untouched, status→finished); the next
  PROSE message hands the turn over and the model replies, seeing the command results replayed. A
  run with its OWN work (a scheduled routine fire, crash recovery mid-workflow) has no authored
  hand-back, so it always proceeds and a command there is injected context. Loop-control kinds are
  not commands.
- Web: `web/api_conversations.py` (create/message are multipart — **attachments** land in
  `<conv>/attachments/` and ride the message text as an `[attached files]` block; vision util for
  images). **Artifacts**: deliverables the model `write_file`s into a DELIVERABLE DIR —
  `artifacts/`, `reports/` or `output/` (`web/artifacts.py` `ARTIFACT_DIRS`, one list governing
  listing, serving and deletion; R339/F336 — scanning `artifacts/` alone left the panel empty for
  a run that committed a verified `reports/*.pdf`, with no way to register it) — are listed/served
  here and rendered in the chat's side panel (html sandboxed, md/img/pdf/csv/json
  inline); routines get the SAME panel on the run view (`api_routines` `/artifacts` + `/artifact`,
  `components/artifacts.js` with `base: "routines"`), with the state-graph card on top.
  The RAIL those cards sit in is one component too (`components/rail.js`, R341): the run view
  and the conversation view render the same collapsible sections, remembered per browser —
  they were divergent copies, and only the conversation one could collapse (F296/R340).
  UI: `static/views/conversations.js` + `components/chat.js` (work folded per reply,
  `[new-topic]` first-line marker → warn + one-click fork) + `components/artifacts.js`.
  **Refer-to** (messenger reply analog, run view + chat): every rendered message carries a
  hover ↩ that primes the composer; the send prepends ONE leading quoted line
  (`> re <label>: <snippet>` — `transcript.js` `splitRef`/`referButton` own the convention)
  to the message TEXT, so the model reads it as plain markdown — no new event field, and
  renderers show it as a quote chip. Slash commands never take a reference (the `/<kind>`
  head must lead).
- **Playbooks** (see Libraries & seeds → Playbooks): the new-conversation form has a playbook
  picker (`GET /api/playbooks`); the composer carries **Save as playbook** (`POST …/playbook` →
  distil a new one) and, when the conversation was seeded from a playbook, **Update playbook**
  (`PUT …/playbook` → revise that one) — both distil from the transcript via the `system_model`.
- Defaults: routine default permissions+capabilities PLUS **`background-tasks`** (the `detach` action —
  conversation-shaped, since a finished task reports back into the chat), tuning.yaml
  `deliberation: deliberate` (chat is judgment-heavy; slider in the header panel), shell OFF (one-click grant;
  run-history + the previous-runs depth greyed — routine-only); rules = ask-policy/web-research/decision-record/intent-inference/**git-checkpoint**
  (checkpoint commits in external project repos — the conversation dir itself is unversioned).
  Conversations feed workflow-usage + health events; they are EXCLUDED from the dashboard,
  scheduler, and instance-export. `bootstrap.sync_seed_library_docs` (every boot) lands new seed
  workflows/rules/permissions + playbooks (subfolder-aware) — how `converse`/`git-checkpoint` and
  seed playbooks reach existing instances.

## Libraries & seeds

ONE git-backed library repo (`libraries_home`, default `~/.local/share/routine-scheduler-libraries`),
seedable from the repo and syncable to a remote, holding **workflows/** (control-flow patterns),
**rules/** (the general rules — ONE copy each, held by slug), **permissions/** (conduct
docs whose `requires:` frontmatter names the capabilities they presume), **playbooks/** (reusable
one-shot conversation briefs — the save/use-instruction analog), and **utils/** (the ONLY way
routines run code, with the `gu` dispatcher at the root). Repo seeds: `library-seed/` (workflows +
rules + permissions + playbooks),
`util-seed/` (utils), `routine-seed/` (bundled meta routines `self-audit`, `routine-improver`,
`token-lab` — installed **disabled**; the dashboard shows a notice until
enabled; a seed added after first boot reaches existing instances via
`bootstrap.adopt_seed_routine` at daemon boot, which respects an archived copy). `self-audit`
works LOOKUP-FIRST from `.codemap/` — a compact derived map of this repo (module API surface,
routes + JS callers, contract literals, mechanical audit flags — orphan candidates
pre-verified by a whole-repo reference scan) the library `codemap` util regenerates at orient
(gitignored; delete-and-rebuild, never hand-edited; `--since` prints the symbol-level change
set for the since-anchor review). Its companions: `sym` (syntax-aware surgery — read one
complete symbol with a content hash, compare-and-swap replace with a whole-file re-parse
gate, `diff` as a scoped per-symbol old→new review with signature-impact notes, `check` as
a syntax pre-gate before pytest) and `run-digest` (one-observation triage of every new
routine run; raw transcripts opened only on anomaly). `token-lab` is
the token-efficiency R&D loop: measures real usage, tests methods via llm subcalls ONLY (never
integrates), publishes `artifacts/report.html`. The **library-sync** ROUTINE
(0.165.0; it was a daemon job from 0.29.0 and is a routine again — the two git operations were
never the work, noticing and REPORTING that a push stopped landing is, and a job whose only
outcome surface was a status file let 94 commits pile up unpushed)
syncs the WHOLE instance into that one repo: each routine's working tree (minus `runs/`, `.git`,
transient inbox/question state) into `routines/<slug>/` and the server config — token/api_key
values AND URL-embedded credentials redacted — into `config/`; then commit (scoped to
`routines/ config/`, under the shared repo lock) → pull --rebase --autostash → push (never over a
failed pull). `bootstrap.py` seeds on
first boot; `deploy/install.sh` for host installs. Everything in the library is user-EDITABLE from
the Library tab, and DELETABLE except permission docs (the capability layer's conduct surface) and
the `clarify-instruction` workflow (routine creation runs it) — both guards are server-side.
A deleted seed workflow/rule returns at the next daemon boot (sync_seed_library_docs); a deleted
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
  `system_model`. Routine creation is initiated from a CONVERSATION ONLY (D58/D59) — there is no standalone new-routine
  wizard page. The conversation agent clarifies the task WITH the user in normal chat (`clarify-instruction`
  SUGGESTS a pattern, or asks to generate one, and MARRIES the task to it), then emits the **`create_routine`**
  action (`engine/create_routine.py`) — valid ONLY from a root conversation — which materializes the routine
  SYNCHRONOUSLY through the SAME `workflows.scaffold` path the retired wizard build once called (decompose the chosen
  workflow into main.md + stages/, record its held rules, write routine.yaml, init the auto-push git repo; the
  daemon's `registry_rescan_s` timer picks the new dir up). The protected `clarification` template routine
  still backs the clarify flow and its questions/decisions surface through `/api/questions`; `wizard_store.py`
  now retains only the on-disk helpers for that template.
- **Rules** (`library-seed/rules/`, `# rule:` heading, NO requires — lint-enforced): GENERAL
  rules — principle prose a run applies to its own case. ONE copy each, in the library: a routine
  holds SLUGS (`routine.yaml` `rules:`), named in main.md's Standing practices tail
  (`rules.with_practices_tail` guarantees it) and read on demand with `read_rule`, so revising the
  library text reaches every holder at its next run with no migration and no fork to drift. Nothing
  is copied into a routine dir and no LLM adapts anything at creation — a rule is general by
  construction, so the decompose pipeline only receives the held slugs as an index.
  The SET is preselected at creation (`suggest_rules_permissions` — which also suggests the
  routine's `deliberation` level — from the refined instruction + chosen pattern) and changed
  afterwards ONLY by the user (`rules.py`, `POST /{routines,conversations}/{slug}/rules` — one
  shared impl): the config list is the state, the tail is DERIVED and rebuilt from it. Deliberately
  not 409-guarded — no run writes routine.yaml, so the web layer is the sole writer; a newly bound
  rule even reaches a LIVE run via control.json `add_rules` → `control.apply_rule_additions` (an
  engine note read from the library, since the prompt is immutable), while an unbind lands next run.
  The TEXT is a separate ownership: `read_rule` is UNGATED (a routine must be able to read what
  binds it, and library prose has no side effect; reading one it does not hold applies for that run
  only), while `write_rule` is gated by the **rule-authoring** permission under its own approval
  dial `rule_confirm` — a revision lands on every holder, which is not the decision write_util's
  `confirm` governs. There is deliberately no `remove_rule`: deleting a rule silently un-binds every
  holder with nothing to catch it, so a run reports it and the user deletes it.
  The routine defaults (`DEFAULT_RULES`): `ask-policy / web-research / decision-record /
  intent-inference`; plus `git-checkpoint` (external-repo undo points — a conversations default,
  scaffold-preselected for repo-editing routines, NOT a routine default). Beside them the **curated
  set** — `evidence-discipline / decision-commitment / error-recovery / change-restraint /
  root-cause-fix / problem-routing / independent-verification / review-recall /
  teaching-insights / interface-design / interface-copy / test-design / failure-visibility` —
  distilled from external
  prompt-engineering guidance and the self-correction literature; NONE is a default, each is opt-in
  per routine (holding it IS the on/off switch — an unheld rule contributes nothing), and
  docs/curated-rules.md records each one's provenance, its evidence strength, and the candidates
  REJECTED on evidence (self-critique, anti-sycophancy prose, numeric confidence) so the set grows
  on observed failures rather than folklore. The **rules-review** meta routine owns the layer: it
  reads how runs actually interpreted each rule and revises the shared text from that evidence.
  The five **after-run improvement passes** (bugfix / research / features / UI / efficiency) are NOT
  rules — the **routine-improver** meta routine owns them and sweeps every routine (honoring
  `improve: false`). `DEFAULT_RULES` (config) is the no-LLM fallback selection.
- **Permissions** (`library-seed/permissions/`, `# permission:` heading + machine-read `requires:` —
  {actions, utils, runs, workflows}, no confirm): CONDUCT docs of the two-layer permission set. The routine's
  enforced surface is its own routine.yaml `capabilities:` ({actions, utils, confirm, runs, workflows} —
  grants.py builds the run policy from it alone, so a doc-without-capability config fails closed);
  a doc's `requires:` names what its instructions presume and drives the UI cascades (activating a
  doc switches its requirements on; switching a capability off deactivates the docs requiring it —
  and the server runs the SAME raise-then-floor on every path that persists a mapping: save AND
  creation (scaffold, conversation create, the composer's ⚙ payload, the /defaults preview), so a
  mapping never expresses a capability its held docs don't require — from birth, not first edit).
  Both layers user-changeable ONLY; routines can't self-grant. The doc set: `util-authoring` (requires write_util — CREATING a new util; the approval
  level always/creations/never is a CAPABILITY setting, default), `util-revision`
  (requires the `revise_util` capability token — changing a util other routines already
  call; the model emits the same write_util action and the engine picks the half from
  whether the name exists), `util-removal` (requires remove_util — deletion takes a
  capability away from every caller, so it is its own decision), `memory` (memory_read/memory_write —
  indexed ≤100-line notes in `.memory/`; INDEX.md engine-maintained, surfaced in the state digest;
  default), `messaging-discord` (requires the reserved `discord` util — one of the per-channel
  `messaging-*` docs), `run-history` (previous-run reads; depth last/all is the
  capability), `shell` (requires the `shell` util — the escape hatch), `workflow-generation`
  (requires `workflows: generate` — a subtask may DRAFT a new library pattern when none fits, folding
  the system-model spend into the run; off by default), `background-tasks` (requires the `detach`
  action — launch a long fire-and-forget task that outlives a reply and reports back; default-ON for
  conversations, opt-in for routines), `global-utils` (requires NOTHING — `requires: {}`; the `util`
  action is a base kind, so this doc is pure conduct: discovery, composition, never silently routing
  around a broken util; default-ON), `rule-authoring` (requires `write_rule` — author or revise a
  general rule in the shared library, under its own `rule_confirm` approval dial since a revision
  reaches every holder; opt-in), `scheduling`
  (requires the `schedule_run` action — arm/cancel one-shot fires on any routine, self-target
  always allowed incl. conversations), and `remote-machines` (requires the reserved `remote`
  util — see Remote machines above). Reservable utils =
  the union of all docs' `requires.utils` (library-defined); gateable kinds = GATED_KINDS
  (engine-defined); `runs`/`workflows` are level capabilities. Permission bodies are SHORT (≤14 lines reach the prompt's CAPABILITIES section
  when held); the Library tab's permission editor has a prefilled, authoritative `requires:` panel.
  Any future permission-ish lever becomes a capability + a `requires:` entry, not a new yaml key.
  See docs/rules-permissions.md. `DEFAULT_PERMISSIONS`/`DEFAULT_CAPABILITIES` (config) are the
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
  `calls:` (sibling utils exec'd via `gu` — drives transitive secret/net resolution), `tags:`,
  `secrets: NAME,…`, `net: outbound|none` — the docstring is the ONLY machine-read surface;
  comment-form declarations above it are invisible), and a `--selftest` the engine runs before
  saving. `write_util` is gated twice: `utils_lib.header_problems` rejects a missing `tags:`/`net:`
  line or a credential env var the code reads but `secrets:` doesn't declare (the Settings page can
  only prompt for declared secrets), then the selftest; approval rides the routine's write_util
  `confirm:` capability level. A header rejection is reported AS one — its own observation head
  naming each violated line — never as a selftest failure (R93), and a failed selftest surfaces
  exit code + both streams head+tail so the traceback's end survives. EDIT MODE (`anchor`/
  `replacement` instead of `content`) patches an existing util's source engine-side — a surgical
  fix never re-emits the whole script (`util name=show --full` returns the complete source) — and
  rides the same approval + selftest + rollback gates. The selftest runner prewarms a net:outbound
  util's PEP 723 deps before the timed run (`run_util` prewarms net:none/undeclared ones itself,
  R40/R20), so a heavy dependency tree never spends the selftest timeout on toolchain install.
  A slug with a DELETION in the library's git history is a user
  decision: `write_util` on it is rejected inside the schema-retry cycle until the user allows the
  `recreate:<slug>` access request this run (`interact.recreate_denial` / `utils_lib.was_deleted`;
  no allow-forever — a fresh deletion outranks any old grant), and the boot
  seed-sync never resurrects a deleted seed util. Discover with the `util` action `name: list`.
- **Every util subprocess is SANDBOXED** (docs/sandboxing.md): `utils_lib.run_util` takes a
  `SandboxPolicy` and wraps the command in a Landlock jail (`rsched/sandbox.py` policy +
  `rsched/landlock.py` ctypes binding/child wrapper, verified working inside the production Docker
  container) whose visible filesystem derives from the run — routine dir + fs roots rw/ro, plus the
  toolchain (uv + caches — including a C compiler, F341: a util's PEP 723 deps are BUILT at call
  time, so a package shipped only as an sdist needs `build-essential` present in the image or the
  util fails at its first run — the library, system trees; the daemon-user HOME stays invisible: secrets
  store, ~/.credentials, ~/.ssh — with ONE deliberate rw carve-out: ~/.claude + ~/.claude.json, the
  claude CLI's session/credential store, so `gu claude` works under the jail). Network is the util's `net:` declaration (undeclared = no TCP;
  transitive over `calls:`). Server config `sandbox: strict|permissive|off` (default permissive:
  jail when the kernel can, warn and proceed bare when it can't; strict refuses instead; the child
  wrapper itself never degrades — it exits 97). A one-shot boot migration (`MIGRATION(expires=
  2026-08-17)` in bootstrap.py) stamps pre-sandbox utils `net: outbound` + missing secrets/calls.
- **Secrets** are one central, write-only KEY→VALUE store (one `KEY=VALUE` line each;
  a value with newlines — an SSH private key — is JSON-quoted onto its line, so PEMs round-trip); a util subprocess receives ONLY the vars
  it (or a `calls:` sibling) declares — undeclared store keys are scrubbed even from inherited
  daemon env, in every sandbox mode. Endpoints and the subscription read the store as before; the
  UI flags unset declared vars.

## Ownership, concurrency, restart (daemon/)

- The **engine subprocess** owns `runs/<ts>/*`, `status.json` (atomic, single writer), and git commits in
  its routine dir. The **daemon** writes `inbox/` plus the closeout of orphaned runs, retention
  (delete/gzip old run dirs), detached-task delivery (artifacts + `state/background.json` on the
  owner), and the `.control/` spools/ledgers. The **web layer** edits routine config only when no
  run is active (409 otherwise) — deliberate live-edit exceptions: conversation settings and rule
  bind/unbind (control.json `add_rules` tells the live run); web-side routine-dir commits take the
  engine's per-repo commit lock.
- The daemon (`scheduler.py` + `runner.py`) fires cron via croniter and spawns one `engine-run` subprocess
  per routine (never two of the same at once) under `max_concurrent_runs`; a run that blocks on a user
  question **releases its slot** (a PAUSED run too). **`rsched/registry.py`** (a shared read-model,
  NOT daemon-owned) derives the catalog and run-index live from the filesystem every rescan — no
  database, no cache files; parsing is memoized per file behind a stat() fingerprint
  (inode+mtime+size, so atomic rewrites always miss), pruned for deleted dirs, copies returned —
  the disk stays the source of truth on every lookup. Every other derived view lives in
  **`rsched/readmodels/`** (stats, run_health, util_stats, statemap, fileactivity, tasktree,
  items — the maintenance index of findings/decisions/bug reports, docs/items.md — and
  messages — a routine's four message folders, docs/messages.md) on the
  same discipline: `readmodels/memo` fingerprint-caches per input file, `readmodels/usage_stream`
  is the ONE parser of workflow-usage.jsonl — a read-model is a pure derivation, deletable state,
  never a writer.
- **One-shot time triggers (schedule-once, docs/schedule-once.md)**: a spool
  (`.control/schedule-once/<slug>/req-*.json`) armed from the routine page or by a run holding
  `scheduling` (the `schedule_run` action); the daemon's `OneShotManager` fires each due request
  ONCE then CONSUMES the file (auto-deactivate = deletion). A conversation's self-armed one-shot
  is namespaced `conv--<slug>` and wakes the conversation by RESUMING its run ("remind me in 3
  days"). Cooldowns/expiry per request; corrupt requests are dropped, not rescanned.
- **Routine groups (D53/D61/D67/D71/F292/D80)**: a group is an ORDERED list of member records —
  `{"slug", "split"}`, where `split` opts the member into the two-phase fire below — plus a
  mid-chain-failure policy, stored instance-level in `.control/groups.json` (`rsched/groups.py` —
  web-written CRUD via `web/api_groups.py` and the Routines page's group rows (D80: the former
  /groups subpage is retired — the group rows carry run-now/pause/edit, the toolbar above the list
  creates groups and holds the instance default, and the editors are overlays in
  `static/components/groupmanage.js`), or the `manage_group` action from a root conversation —
  including the group's cron schedule (R312), so group scheduling needs no operator round-trip; a
  group is never routine config).
  - **Shared routine config (D82)**: a group also carries `config:` — routine.yaml keys its
    members INHERIT. Related routines share a policy surface (the same permissions, capabilities,
    rules, machines, connections, secret grants, models, budgets, fs roots, tags), and keeping N
    copies of it in step is how they drift apart; the group holds one copy. The group is a
    **default, never an override**: `config/routine.apply_group_config` merges it into the
    member's RAW routine.yaml *before validation* — list keys UNION (the group is a floor a member
    adds to), mapping keys merge per key with the member's value winning, and `capabilities` does
    both (its lists union, its dials take the member's when set). Merging pre-validation is what
    makes "the member set it" mean *the key is in its file* rather than *the model has a default*
    — every field here has a non-empty default, so a post-validation merge could never tell the
    two apart. `groups.CONFIG_KEYS` also fixes what may NOT be shared: slug/name/description/
    enabled/schedule/workflow/retention/triggers/improve say which routine this is and when it
    runs. Nothing is written back to routine.yaml, so leaving a group returns the routine to
    exactly what its own file says; `load_routine` records `inherited`/`inherited_from` so the
    routine page marks an inherited value instead of letting it read as one set there.
  "Run group now" — or the group's OWN cron (below) — ARMS a
  sequential chain (`rsched/group_runs.py`, one in-flight chain per group, snapshot of member
  records + resolved policy at arm time); the scheduler-ticked **`GroupRunManager`**
  (`daemon/group_runs.py`) advances it one transition per tick: fire the member at the cursor, wait
  for a terminal state, then per the outcome and `on_failure` (`stop` aborts the rest — any
  non-`ok` outcome counts as failure, a missing/disabled/crashed member too; `continue` fires on)
  fire the next.
  - **The two-phase fire (F292, operator standing order R214.3b)**: a chain runs in TWO PASSES —
    the INGEST pass fires every member in group order; the OUTBOUND pass then fires the members
    flagged `split` again, same order — so every member's ingestion/processing lands before any
    split member's outbound communication, and a later member's ingest can read an earlier
    member's fresh state. A SPLIT member is fired once per pass and told its half via a run-scoped
    `phase=ingest|outbound` boot param: `Runner.fire` writes it into the run dir's `boot.json`
    before the engine boots (so a resume keeps it — the file rides the dir), the engine reads it
    at boot beside slug/run_ts (`RunContext.group_phase`, stamped into status.json as
    `group_phase`), and the harness contract carries the pass's marching orders (`GROUP FIRE
    PHASE: …` — ingest: process and stage only, NO outbound; outbound: read the staged state and
    send, don't re-ingest; docs/prompt-anatomy.md). A NON-SPLIT member runs once, in the ingest
    pass, with no param — its whole job in one run; a group with no split members chains once,
    exactly as before. `stop` mid-ingest halts the outbound pass too (it would read state the
    halted ingest never staged). `rsched run-once <slug> --phase ingest|outbound` exercises a
    split routine's phase branch by hand through the same boot.json channel.
  Two group-wide facilities ride membership:
  - **The group schedule (D71)**: a group may carry its own `cron` (+ the server `tz`, written by
    the web beside it — the group editor has the same friendly editor a routine's schedule uses;
    web RECORDS, daemon FIRES). The scheduler arms the chain on the group's cron — member 0 fires,
    the rest chain on completion — and while a routine belongs to a SCHEDULED group its OWN cron is
    SUPPRESSED from the fire table and boot catch-up (one fire path, no double-firing); its
    Schedule dropdown renders a locked "group managed" state linking to the group, the routines
    overview shows the GROUP's schedule on the member's row instead of the suppressed cron
    (R313 — `/api/groups` ships each group's `schedule_desc` for it), and clearing the
    group's schedule (or leaving the group) restores the member's own cron at the next rescan.
    A group fire due while its chain is still in flight is SKIPPED (the chain analog of
    `overrun_skipped`); there is no group catch-up. Manual "Run now" on a member is unaffected.
    The dashboard's week strip renders the same rule: `/api/schedule/week` withholds a
    suppressed member's cron fires (drawing them would show runs the daemon never fires) and
    ships the group's own fire times under `groups` instead; the strip draws a scheduled group
    as ONE labelled lane whose members chain end-to-end at each group fire (member order, split
    members again as the outbound pass), each segment sized by that member's average runtime.
    The strip's bars also DRAG (weekgrid-drag.js): onto a sibling bar to reorder the group,
    onto another group's lane to join it, onto the remove strip to leave it, and along their
    own lane to re-time the schedule — the group's cron on a scheduled-group lane, the
    routine's own otherwise, both through the same `schedule.friendly` PATCH the editors use
    (custom crons have no draggable shape and are refused with a pointer to their editor).
  - **The shared group store (D67)**: every run of a grouped routine gets
    `.control/group-stores/<group-id>/` injected into its effective fs read+write roots at boot
    (`RunContext.group_store_roots`, created lazily engine-side — run data, not config; children
    inherit it like every resource). The harness contract names the root and its collision
    semantics: writes are whole-file atomic and LAST WRITE WINS PER FILE, so members exchange
    files under per-routine names (`<slug>-<topic>.md`) and treat shared files as read-mostly.
    It is also the natural home for what a split member's ingest pass stages for its outbound
    pass (its own `state/` works too — the phase prose names both).
- **Event triggers fire through the same seam** (docs/triggers.md): the webhook route
  (`web/api_hooks.py`, POST `/api/hooks/<slug>/<token>` — the ONE unauthenticated API route:
  constant-time token compare, generic 404, 64 KiB cap, rate limit + spool cap, rejections logged,
  payload never echoed) only RECORDS events durably in the `.control/triggers/<slug>/` spool
  (request-file idiom, like restart.request); the scheduler-ticked **`TriggerManager`**
  (`daemon/triggers.py`) turns them into fires — the trigger analog of overrun is QUEUE, not skip:
  N events while a run is active/queued/cooling coalesce into ONE fire, each event still landing as
  its own inbox message for that fire (deterministic filenames → exactly-once across crashes).
  `cooldown_s` per trigger (default 60) bounds trigger-fire frequency, so a leaked URL can't burn
  budget; `state.json` in the spool is the daemon-written fire ledger the Triggers card renders.
- **API auth is two-tier (R94; operator decision 2026-08-05: ENFORCE — superseding D68's
  earlier "leave as-is")**: the PRIMARY bearer (`config.yaml token:`) is the human/web
  credential and authorizes everything; the ROUTINE bearer (`routine_token:`, generated by
  `bootstrap.ensure_config` when absent — an existing config gains it at the next daemon
  boot) authorizes read-only methods plus an explicit non-config allowlist
  (`web/app.py ROUTINE_TOKEN_MUTATIONS`; empty today — the wild-usage survey found only
  reads plus the config writes this seal stops). The
  engine injects the routine token into util subprocesses as `RSCHED_API_TOKEN`
  (`engine/executor._extra_secrets`, overriding any secrets-store value for that reserved
  name), so a run holding the API secret can READ the daemon API but every config-mutating
  route (routine/conversation PATCH, permissions PUT, grant decisions, settings, triggers,
  groups, schedule spools) answers 403 with a pointer to `ask_user config_patch` — the
  HTTP flank of "config is the user's" is sealed, mutating routes are primary-only BY
  DEFAULT, and opening one to routines is an explicit allowlist edit with its reason.
  `components/searchbox.js`, focused by `/` or Ctrl-K): SQLite FTS5 over both homes' PROSE —
  transcript say/note/finish/questions/answers/user messages (gz + subrun trees included),
  result.md, history/ archives, LEDGER.md, `.memory/`, pending decision records, recipe
  files; NEVER config, state/, inbox, artifacts, `.util_outputs/`, or tool observations
  (bulk, and where a leaked secret would live). The db (`<routines_home>/.control/search.sqlite3`) is a PURE
  CACHE of the filesystem — delete it and it rebuilds; per-file stat fingerprints drive
  incremental refresh (newest runs first, budget-bounded with a per-pass progress
  guarantee) and prune rows for files retention removed. ONE writer: the daemon/web
  process (a lifespan maintainer task + a ~2s query-time top-up) — engine subprocesses
  never import it. Raw FTS5 syntax passes through when it parses; anything else falls
  back to escaped-phrase terms, and an empty/unsearchable query is a 400, never a 500.
  Hits carry {home, slug, run_ts, sub, kind, turn, phase, snippet} and the client groups +
  deep-links them (`#/run/<slug>:<ts>[?sub=…]`, `#/conversations/<slug>`, `#/questions`,
  `#/routine/<slug>`). See docs/search.md.
- **Self-update restart** (`restart.py`): a sentinel triggers a drain, then a clean exit; systemd
  `Restart=always` relaunches on the committed code (`uv run` re-syncs deps). A parked run
  (`waiting_user`/`paused`) DEFERS the drain's start (never freeze scheduling on a human); once
  draining, active runs are waited out. In-flight creation builds AND live clarify runs (spawned by
  the web layer, invisible to the runner — `restart.clarify_states` reads
  `clarification/runs/*`) hold the drain the same way. Orphaned runs claiming to be alive are
  closed out at boot.

