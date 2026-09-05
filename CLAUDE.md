# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through one JSON
action per turn. **A second AGENT LOOP in the path is banned**: it fights this harness and hides the
conversation. Endpoints are model TRANSPORTS only (docs/architecture.md). Routines run code through a global util,
plus — for the ONE-OFF only — the `shell` ACTION behind the `shell` capability (0.287.0; it was a
reserved util until then, where `capabilities.utils` gated it as an exception rather than
projecting it out of the schema). Every util subprocess runs inside a Landlock sandbox scoped to
the run's permissions INTERSECTED with the util's own `fs:` declaration, and a `shell` command
runs in the same jail on the widest of those terms — the run's granted roots, no store secret
(docs/sandboxing.md). The instruction contains only the task; cross-cutting conduct is
a set of GENERAL RULES with ONE library copy each (`rules:` in routine.yaml holds slugs — the run
reads the prose with `read_rule` and applies the principle to its own case); schedule, PERMISSIONS,
workdir, budgets, and model roles are routine config (`routine.yaml` / UI), started from a
library **settings TEMPLATE** (`rsched/templates.py`) that is COPIED IN once at creation or by
the routine page's adopt action — a PRESELECTION, not a layer: the file then says what the
routine IS and every value is edited where it lives. Only a GROUP's shared config layers live
(D82).

## Where the detail lives

This file is the working tier: what to run, what must not be repurposed, and the gotchas
that are not discoverable from the code. Subsystem narration lives in `docs/` — read the
one you are about to touch, not all of them.

- `docs/architecture.md` — the full subsystem reference (engine loop, endpoints, routines
  on disk, child runs, conversations, libraries & seeds, daemon ownership, OAuth, machines)
- `docs/prompt-anatomy.md` — every string the orchestrator sees, and why. Revise it with ANY
  change to composer / loop / actions / schema_guard wording; `tests/test_prompt_anatomy.py`
  fails on drift
- `docs/rules-permissions.md`, `docs/curated-rules.md` — the general-rules layer (each doc's
  `effect:` line — what a routine holding it DOES differently — is what the page labels its
  on/off control with, and the linter requires it), the two-layer
  permission set, the SETUP SURFACE (`readmodels/surface.py` forwards, `library_impact.py`
  backwards, `daemon/library_watch.py` for changes with no writer), the ACCESS-REQUEST grant model (entities.py ids; allow/deny × now/forever, plus
  allow-once for turn-action classes), and each curated rule's provenance
- `docs/child-runs.md`, `docs/background-tasks.md`, `docs/triggers.md`, `docs/schedule-once.md`
  — the child-run and firing mechanisms
- `docs/conversations.md`, `docs/playbooks.md` — interactive sessions and reusable briefs. A
  conversation's spine is EMERGENT: it writes its own `state/plan.md` (inlined at the top of every
  reply by `state_digest`) where a routine gets `stages/` + `phase.json` compiled at creation —
  don't "fix" a conversation by giving it a compiled workflow
- `docs/status-pages.md` — the shared web UI routines publish to (one shell, one
  append-only feedback contract, the `status-page` rule that makes it opt-in)
- `docs/items.md` — the maintenance-item index (findings, decisions, bug reports): the
  item shape, the status vocabulary and its precedence, and the changelog join;
  `docs/messages.md` — a routine's four message folders (the Messages page/D74), the
  per-folder write surface, and the outbox-retraction decision
- `docs/sandboxing.md`, `docs/endpoints.md`, `docs/oauth-connections.md`,
  `docs/remote-machines.md`, `docs/browser-sessions.md`, `docs/darknet.md`, `docs/usenet.md`, `docs/notifications.md` — the outward-facing
  surfaces
- `docs/search.md`, `docs/run-analytics.md`, `docs/authoring.md`, `docs/examples.md`,
  `docs/getting-started.md` — read models, authoring, onboarding
- `docs/designs.md` — specs for work DECIDED BUT UNBUILT (one entry per queued finding, or
  per decision taken before a finding exists).
  Nothing there describes current behaviour, so never read it as a reference; an entry is
  deleted when it ships and its narration moves to the subsystem doc it belongs to
- `.codemap/` — the derived module/route/contract map `self-audit` works from
  (gitignored; regenerate with the `codemap` util, never hand-edit)

## Commands

- `uv sync` — install/refresh the venv
- `uv run pytest -q` — full suite (fast, no network; PARALLEL by default via pytest-xdist
  `-n auto --dist worksteal` in addopts — pass `-n0` for a serial run / debugging with `-s`). Two conftest
  env knobs keep it honest AND fast: `RSCHED_SKIP_DOCS_BUILD` (the app lifespan's pdoc build
  — a to_thread task shutdown can only await) and `RSCHED_RETRY_BASE_DELAY` (endpoint retry
  LOGIC runs, the 1s/2s backoff clock doesn't); test_docs_build / test_with_retries_backoff
  clear them to pin the real paths. Single test: `uv run pytest tests/test_loop.py -q`
  or `-k <name>`. Live endpoint smoke tests run only with `RSCHED_LIVE_TESTS=1`. The suite
  includes the browser UI tests in `tests/ui/` (Playwright driving the REAL console over a
  stub runner — no scheduler, no engine, no LLM; see `tests/ui/conftest.py`). One-time per
  machine: `uv run playwright install chromium`. EVERY UI change gets exercised here — it is
  the safety net that lets the frontend be reworked boldly.
  `tests/production_guard.py` is the suite's SANDBOX FLOOR (session-scoped autouse): no test
  may write inside the live instance's data homes, and none may spawn this package's CLI —
  that child is a fresh interpreter that would load the production config. Never weaken it to
  make a test pass; a test that needs to write there is pointed at `tmp_path` instead.
- `uv run ruff check` + `uv run mypy` — the strict quality gates (ruff runs `select = ALL`;
  every ignore in pyproject.toml carries its house-style reason). Both MUST be green in every
  commit; `uv run pre-commit install` wires them into git.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=catalog-name` overrides a model role (catalog names, not endpoint:model pairs); `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>]`
  — see `rsched --help`. `validate` checks every routine's `routine.yaml`, lints its recipe
  prose (`main.md`, `stages/`) AND reports its SETUP SURFACE (`readmodels/surface.py`: what the
  held docs, bound rules and reserved utils still need — a `blocks` row fails the command, a
  `warn`/`note` row is reported only); `lint` covers the library. `engine-run` is internal
  (daemon-spawned).

## Core contracts — extend, never repurpose

- **Actions** (`engine/actions.py` — flat schema on purpose; weak models and Ollama grammars handle flat
  far better than `oneOf`): `util, write_util, remove_util, read_file, view_image, write_file, edit_file,
  memory_read, memory_write, read_rule, write_rule, script, llm, spawn, subtask, detach, schedule_run,
  create_routine, manage_group, subruns, kill, wait, ask_user, report, finish` (25). **`script` runs
  the routine's OWN `scripts/<name>.py`** — persistent helper TOOLING, deliberately NOT a co-equal
  interpreter of the routine (the "procedure" symmetry doctrine was reversed 2026-08-12): the recipe
  stays the single interpreter of the task and delegates only judgment-free sub-steps. A repeating
  deterministic step (poll, parse, compute, render) is written ONCE via `write_file` and called
  thereafter — versioned by the routine repo, run in a persistent workdir venv (`<routine>/.venv`,
  PEP 723 deps on demand, gitignored) inside the run's fs jail, with ONLY the granted secrets its
  docstring header declares (the util model — `rsched/scripts.py`) PLUS whatever the utils it names
  on its `calls:` line declare: the library is reachable through `gu` exactly as it is for a util's
  own siblings, DECLARED-ONLY (no `calls:` line → no `gu` on PATH at all; an undeclared or unknown
  sibling is refused rather than run without the secrets and net that declaration carries), one jail
  and one env over the whole call tree. There is no model channel inside — a judgment call belongs
  in the recipe. Gated by the
  `script` capability (`scripts` permission doc), no approval dial — the blast radius is a subset of
  the routine's own sandboxed permissions. routine-improver scouts recipes for deterministic prose
  responsibilities and nudges them into scripts.
  `finish` and `report` are ALWAYS_KINDS — available on every
  turn regardless of the workflow's `tools:` allowlist or the capability set. **The engine never ends a run
  the model could have ended itself**: the FIRST budget violation spends a one-time RESERVED FINISH TURN
  (schema narrowed to `finish`, one turn granted, `OBSERVATION (budget spent)` telling it so), and only a
  second violation force-finishes — so a run overruns a budget by at most one turn and the summary is
  always authored. A finish emitted while an undrained user message waits is deferred so the message
  becomes the next turn (a finish that must stand — the spent reserved turn, an abort — names the
  still-queued message in the summary instead). Budgets are a runaway BACKSTOP, never a pace; do not reintroduce prose that has a run
  ration its work against the turn counter. Every action carries `say` (finding-first narration:
  what the last observation taught you + why this action; terse for routine steps, 2-3 sentences
  at decision points; worded per the routine's `deliberation` level) + `kind`, plus an optional
  **`note`** — 1-3 SELF-CONTAINED lines worth keeping beyond the context window, engine-filed to
  `state/notes.md` at no turn cost, stamped run·turn·phase·action (`engine/notes.py`; the stamp is
  an address into the transcript archive; the digest carries the file's tail into the next run;
  curation into `.memory/` stays memory_write's turn-priced job). `read_file` batches
  related reads via `paths` (one turn, one
  observation section per file); `edit_file` anchor-replaces in place so revisions cost the diff, not
  the document; `write_util` mirrors it — `anchor`/`replacement` instead of `content` patches an
  existing util in place under the same approval + selftest + rollback gate (`util show <name>
  --full` returns the complete source). `write_file` is GROUNDED: overwriting an existing file OUTSIDE the routine's own dir
  is rejected unless this run has seen it (`ctx.seen_paths` — read/viewed/written this run, rebuilt
  from the transcript on resume); the own dir is exempt (state/report rewrites are the normal mode),
  append and new files pass, and `edit_file` needs no gate — its verbatim anchor is self-grounding.
  There is ONE **CHILD RUN** concept (`engine/child.py`) — an isolated run with its own dir, its
  own budget, its own recipe, and a declared relationship to its parent. `spawn` (parallel),
  `subtask` (sequential) and a conversation `branch` are three scheduling MODES of it, never three
  concepts and never a fourth action kind; `engine/child.py` owns the mode vocabulary the prompt
  renders and the hand-back path, so the kind copy, the observations and the docs cannot drift
  apart (that drift once had the prompt claim children share the parent's working directory).
  Every mode obeys the same contract: isolation, a budget sliced from the parent's remainder, and
  a HAND-BACK — summary always, FILES by the child writing into its own `artifacts/`, which the
  engine copies to the parent's `artifacts/from-sub-<n>/` and NAMES in the one
  `CHILD RUN FINISHED (<mode>)` notification. Collection lives in `subruns._collect`, the child's
  single finalization point: two paths report an exit (`wait` and the turn boundary), so anything
  that must happen once per child belongs there and not in a reporter. One child-task executor,
  `engine/childrun.py`; a `subtask` with `workflow: "generate"` drafts a new pattern when the
  `workflows: generate` capability is held (see docs/child-runs.md).
  **`report` is the ONE channel for work that is not the run's own task** — ungated, held by
  every routine. What varies is whether the run can name an owner. UNADDRESSED goes to the
  triage stream self-audit reads; ADDRESSED (`target`) is ALSO delivered into that routine's
  `inbox/`, which its NEXT SCHEDULED RUN drains — it starts no run and wakes nobody. The target
  closes it by reporting back with `answers: "<R id>"`, adding `closes: true` when the reply ends
  the exchange — a closure is born settled; without it the reply is itself a new open report.
  Teammates inside one GROUP have a lighter channel that is NOT the report ledger (F335,
  `rsched/groupnotes.py`): a member writes `<group-store>/notes/<sibling>/note-*.json` with an
  ordinary file write and the engine surfaces it in the sibling's state digest at boot, dropping
  it as it reads — no approval, no ledger row, no Messages item, and no new action kind. The
  boundary IS the safety model: the store is in members' fs roots and nobody else's, so a note
  cannot leave the group. A note is coordination; a report is work an OWNER must act on.
  One `R<n>` namespace, one append-only
  ledger `.control/reports.jsonl` (order rows + `delivered` event rows), one Items type; the
  page shows open → in_progress once drained → settled once answered. Triage is therefore
  FORWARDING, not absorbing.
  **`create_routine` / `manage_group` are conversation-INITIATED, not conversation-only** (F328):
  a root conversation materializes them, and a run with no user in the loop writes a PROPOSAL to
  `.control/pending-creations/` that the Decisions page materializes with one click through the
  same `workflows.scaffold` / `rsched.groups` path. A queued proposal is rendered by ONE shared
  branch checked before any kind's success wording (`obs_admin.QUEUEABLE_KINDS`): teaching the
  handlers about the queue and not the renderers is how a proposal came back reading as a
  completed action over an absent payload (R1200/R1183). `create_routine` carries an optional
  `stopping:` — the user's own words for what DONE looks like for one run — which seeds the new
  routine's STOPPING CONDITIONS instead of evaporating into the instruction prose — so the engine still never writes
  `routine.yaml`, and a scheduled run holding a finished design no longer has to hand it back to
  the operator by hand (R353). `manage_group list` still answers directly (naming each group's MEMBERS in fire order, F424); every mutating verb
  queues. A within-reply CHILD (depth > 0) is still refused outright and never sees the kinds:
  the queue is for a run that HAS a user, just not right now. Ungated like `report` — the
  approval is the gate, and it is a human.
  `ask_user` carries an optional `default` — what the run DOES when a blocking ask times out —
  and an optional `request` ("<class>:<name>" entity id, entities.py): a typed ACCESS REQUEST the
  Decisions page settles with one of four decisions (allow/deny × now/forever). Forever-decisions
  are written to routine.yaml by the WEB at click time (`grants:` rows = deny tombstones + secret
  exposure; the engine NEVER writes config); now-decisions live in-memory on the run and reach all
  three enforcers (validate_action, the util sandbox's roots, declared-only env injection).
  `memory_*` are the ONLY way into `.memory/` (generic file actions are rejected there); the engine
  owns `.memory/INDEX.md` (built from each write's `about`) and the 100-line note cap.
  **`read_rule` / `write_rule` are the general-rules layer** — ONE library copy per rule
  (`<library>/rules/`), never a per-routine fork. `read_rule` is UNGATED (a routine must be able
  to read what binds it, and library prose has no side effect); `write_rule` is gated by the
  `rule-authoring` permission and carries its OWN approval dial `rule_confirm` — a rule revision
  lands on every holder at its next run, which is not the decision `confirm` (write_util) governs.
  The two halves are owned apart: WHICH rules bind a routine is config (`rules:`, user-only, and no
  run writes routine.yaml), the TEXT is the library's. A rule may carry `expects:` — the SOFT
  edge, entities its prose presumes (a write root to publish into), advisory forever — but never
  `requires:`, which would switch a capability on. There is deliberately **no remove_rule** —
  deleting a rule silently un-binds every holder with nothing to catch it, so a run reports it and
  the user deletes it. The `rules-review` meta routine owns the layer: it reads how runs actually
  interpreted each rule and revises the shared text from that evidence.
  **Util output too large for its observation is SAVED, not lost** — `engine/outputs.py` spills the
  full captured text to `.util_outputs/<run-ts>/t<turn>-<util>.out` and the observation that lost the
  middle carries the path (so the store needs no index). ONLY truncated output is kept: an
  untruncated one is already in the transcript verbatim, and a copy would duplicate a file the system
  has. Engine-owned and read-only for the run like `runs/`, gitignored on first use (autocommit is
  `git add -A`, and util output can carry tokens), never search-indexed, pruned to the last
  `KEEP_RUNS` runs — retention is a backstop, never a promise.
- **The prompt surface is documented** in `docs/prompt-anatomy.md` (rendered on the Help tab). Revise
  it with ANY change to composer/loop/actions/schema_guard wording — `tests/test_prompt_anatomy.py`
  pins the load-bearing strings and fails on drift.
- **Transcript events** (`engine/transcript.py` — append-only JSONL, the engine is the only writer):
  `header, assistant_action, observation, question, answer, user_injection, subrun_start, subrun_end,
  compaction, error, stopping_update, finish`. This vocabulary is consumed by the web renderer AND the meta routine.

## Gotchas

Non-obvious rules that cost a run or a commit when missed. Each is enforced somewhere —
by a test, by the engine, or by a past incident.

- **No backwards compatibility.** Never ship tolerant or dual-convention code. Migrate the
  production data in the same change and keep only the canonical form.
- **Migrations are one-shot and machine-checked.** Historical data migrations are NOT kept:
  each runs once on the production instance and is deleted after convergence (a pre-0.8
  backup converts by booting the matching older tag first). Migration code MUST carry a
  `MIGRATION(expires=YYYY-MM-DD)` marker comment — `tests/test_policy.py` fails once the
  date passes, and on migration-shaped code without a marker.
- **Documentation is swept, not patched.** On any change, revise ALL affected doc surfaces
  (CLAUDE.md, `docs/`, `static/views/help.js`, README, docstrings) — not the one you were
  asked about.
- **The quality gates run on the FULL repo**, not the changed files. `tests/test_quality.py`
  runs ruff, mypy AND vulture (dead code — what ruff cannot see is a symbol whose last caller
  went away; `src` and `tests` are scanned together so a src symbol used only by a test is not
  reported), and the engine bypasses pre-commit — so a red gate can otherwise sail through.
- **A recipe says WHAT, never which tool.** Workflow patterns, materialized recipes, general
  rules and playbooks may not name a util or show its flags — they name the capability, the run picks the
  tool from its live CAPABILITIES catalog, and what worked is persisted in the ROUTINE'S memory,
  never the recipe. Enforced AT GENERATION — `workflows/adapt.py` (materialization) and
  `workflows/generate.py` (pattern drafting) spell out the forbidden forms in the prompt — not
  by a linter over the finished file: these documents are LLM-written, so the generator is the
  cause and a name-matching check over a DYNAMIC util catalog turns unrelated files red the day
  a util is named after an ordinary word. See docs/authoring.md for the two things a recipe may
  legitimately name (services/protocols; paths named after their tool).
- **Fix the cause, not the symptom.** The corollary, and a standing instruction: when an
  artefact comes out wrong, correct whatever produced it. For anything LLM-written that is the
  generation prompt — make it correct, unambiguous and strict, then repair the existing files
  once by hand. Add a machine check only for what no prompt can guarantee.
- **The composed prompt is a caching contract.** The message list is appended-to, never
  mutated; per-turn boilerplate is banned. Only compaction, schema-retry cleanup and the
  media fallback may rewrite it, each invalidating the provider cache by design.
- **What DONE means is the user's, and it is not a budget.** A run's meaning-level bounds are
  STOPPING CONDITIONS (`engine/stopping.py`, F334/D98, user order 2026-08-14): user prose in
  `state/stopping.json` that the composer inlines (`engine/stopping_digest.py`) and the finish
  gate makes impossible to ignore.
  Budgets stay a runaway BACKSTOP; this is what actually decides when a job is finished.
  Every condition declares a **SCOPE** and the two are answered differently. `run` (the default)
  bounds ONE run and is re-asked every run — it records its verdict (`last_verdict`) and NEVER
  transitions, because a per-run bound cannot be "already met". `goal` is the state after which
  the ROUTINE is finished: sticky, and it RETIRES the routine — `registry.RoutineInfo.retired` is
  derived from it, the scheduler builds no fire entry, group chains skip the member as
  `outcome: "skipped"` (not a failure), and `engine/goalreached.py` queues ONE Decisions-page
  proposal whose approval writes `enabled: false` through the ordinary PATCH and whose refusal
  REOPENS the goal. Nothing about retirement writes config: that is how a routine disables itself
  without breaking "a run never writes routine.yaml". Only the web (`api_stopping`) creates a goal
  condition, so a routine can report against a finish line but never draw its own. Sticky + per-run
  was the defect the scope split undid: 22 of 31 live routines were reading "the job is DONE.
  Finish NOW" at the top of every run.
  Conditions are LOGICALLY CONNECTED — groups combine with `all`/`any`, the document combines the
  groups the same way (two levels: enough for "(A AND B) OR C", shallow enough for a UI and a weak
  model), `requires` gates one condition on another, and `stage` scopes one to a routine phase.
  The engine judges NO semantics: the contract is an ACCOUNTING (`[s<n>] met|unmet — why` per
  ACTIVE condition), the gate rejects a summary that skips one, and `record_accounting` stamps the
  model's verdict back at the finish so the panel, the next run and the user all read the same
  state. Satisfaction is REPORTED, never enforced. **v2** (`engine/verifier.py`) checks the
  claims a summary marks `met` against the run's own transcript with a `tool_call` subcall, and
  is built around its own two failure modes: FAIL-OPEN everywhere (an unavailable endpoint, an
  unparseable answer, an unmentioned condition or anything short of an explicit
  `supported: false` all accept) so it cannot strand a finished job, and AT MOST ONE challenge
  per condition per run so a stubborn model and a stubborn judge cannot livelock the run into a
  dead budget. A re-asserted verdict STANDS and the disagreement is recorded (`disputed`).
- **Global chrome is positioned by `base.css` ALONE, and losing that fails silently.** The
  components mounted outside `#view` so they survive navigation — the side table-of-contents
  (`components/toc.js`) and the LLM activity dock (`components/taskmanager.js`) — set no
  `position` of their own. Delete their stylesheet block and nothing throws: the component still
  builds, still fetches, still updates, and lands in the document flow at the foot of every page.
  The 0.277.0 palette migration deleted both; the TOC was caught three releases later, the dock
  twenty, each time by an operator reading a screenshot. `tests/ui/test_global_chrome.py` pins the
  pair to `position: fixed` — put any new out-of-view chrome in that list the same day.
- **A run never writes its own config.** `routine.yaml` is never writable by any run — the
  block is by FILENAME anywhere a run can write, external repos included.
- **The engine subprocess INHERITS NOTHING — the spawn names its config and its homes.**
  `engine-run` is a fresh interpreter, so it defaults NEITHER `--config` nor `--homes`
  (`daemon/runner_state.py` `engine_cmd` → `cli.cmd_engine_run`, F394): it loads exactly the
  config it was handed and refuses when that config resolves to different run homes than the
  spawner is using. A spawner whose config was never loaded from a file is refused before a
  process exists. Never give either flag a default — the fallback is `~`, i.e. production,
  and a tmp-homed test once spent real money and real ledger rows there.
- **The setup surface answers "what does this routine still need?" — including WHEN it runs.**
  `readmodels/surface.py` joins the effective config against the library's `requires:`/`expects:`,
  the util headers, the live stores AND the group store: a member cron a group's schedule
  suppresses (D71) is a routine.yaml naming a time it will never fire at, and a routine in no
  scheduled group with no cron of its own is started by nothing on a clock. It also reads
  `state/phase.json`: the composer looks the phase up as `.get("phase")` and that value is what
  scopes a stopping condition to a stage, so a routine recording its own key (`lifecycle`,
  `state`) or nothing at all wrote a file matching nothing. All of these are NOTE rows —
  nothing is broken, the file is misleading — and the BOOT note carries only `blocks`/
  `interrupts` (`surface.BOOT_SEVERITIES`): a NOTE is for the operator, and a run can neither
  act on it nor be saved a turn by it. `rsched validate` adds the instance-level cases no
  routine's surface can see: a scheduled group with no members, and a group naming a slug that
  is NOT a routine — routines are deleted out of band, so nothing cascades the membership away,
  and the web refuses only the slugs a caller ADDS so one stale member cannot lock a whole group
  against every further edit (F442). An `expects:` row must be an
  UNCONDITIONAL presumption: it fires on EVERY holder, and it has been wrong twice the same way
  (`git-checkpoint`, then `status-page`'s write root — false for all seven holders, because a page
  is published through an upload channel and a routine's own dir is always writable).
- **An exclusive machine's compute is QUEUED, not locked.** `MachineConfig.exclusive` makes
  `remote submit` take a ticket instead of launching (`rsched/machine_queue.py`). The order is
  FAIR SHARE — round-robin across ROUTINES by each one's oldest waiting ticket, FIFO within one —
  so a routine that submits three jobs never starves one that submits one. The submitting run is
  NEVER blocked: it gets a job id and a position back at once, reads that position in
  CAPABILITIES, and spends the run on work that does not need the machine. The truth is ON THE BOX
  (tickets under the job root, enforced by the util at the one place that opens an SSH
  connection), so it survives a restart, a recreate and a migration; the daemon only MIRRORS it
  into `.control/machine-queue/`. Every ticket carries a mandatory DEADLINE — a detached job has
  no live process to heartbeat, so a wall clock is the only self-healing part. A machine that
  cannot be read says UNKNOWN, never FREE: an unreachable box reading as free is the one failure
  mode that would cause the collision this prevents. Cooperative, like every machine guard — a
  human on the box or a `shell` action still bypasses it.
- **A model's limits are DISCOVERED, not configured.** `endpoints/limits.py` asks each provider
  what its models' real context window and output maximum are (OpenRouter/Nano-GPT/Ollama have
  metadata APIs; `anthropic` and `claude-cli` have none and use a built-in table), caches it under
  `<routines>/.control/model-limits.json` — derived state, never config — and refreshes on a 24h
  TTL from the scheduler tick. ONE precedence chain: per-MODEL config → provider → endpoint
  default → floor. The endpoint value sits BELOW the provider because it has always been
  documented as a default a model inherits, and putting it there means nothing has to be deleted
  from an unversioned config.yaml. `resolve()` is on the per-turn path and NEVER fetches — a miss
  is the next tier down. The two knobs are OPPOSITE: the input window is adopted verbatim, the
  output cap is `min(provider max, ENGINE_OUTPUT_CEILING)`, because providers validate
  `input + requested_output <= window` and a 943k-token output limit would starve the prompt.
- **A config field must declare whether it reaches a LIVE run.** `configflow.CLASSIFICATION`
  (F337) maps every `RoutinePatch`/`ConversationPatch` field to LIVE (adopted at a turn boundary
  — budgets, deliberation, grants) or NEXT_RUN, with the reason the operator is shown;
  `tests/test_configflow.py` fails on an undeclared field. Both PATCH handlers signal a live run
  through `control.json` and the engine appends ONE ENGINE NOTE naming EVERY changed field and
  which half it is in — a change that silently does or does not reach a run is the bug.

## Standards

- One responsibility per file, ≤ ~350 lines. Split rather than grow.
- Prefer a fitting, well-maintained package over hand-rolled plumbing (pydantic validates config,
  tenacity retries, python-frontmatter parses frontmatter, sse-starlette speaks SSE). The bar is net
  reduction AND net clarity — `paths.atomic_write` and `schema_guard` stay bespoke on purpose.
- Cross-process files are written atomic (tmp+rename) via `paths.atomic_write` — never ad-hoc, and
  through its typed pairs where one fits: `atomic_write_json`/`read_json`, and
  `atomic_write_yaml`/`read_yaml`. Those two carry the dump options (`sort_keys=False` keeps the
  key order a human wrote; `allow_unicode=True` keeps an umlaut readable), so no call site spells
  them and none can drift. `read_yaml` deliberately does NOT swallow errors the way `read_json`
  does: nearly every YAML read here is the first half of a read-modify-write of `routine.yaml`,
  and a default returned for an unparseable file would rewrite the user's hand-broken config FROM
  that default. The loaders that must turn a broken file into a problem STRING catch around it.
- `static/` is no-build vanilla-JS ES modules (no bundler, no node, no external assets). Keep it
  that way. The design system is `base.css` ("watchfloor"): colour is STATE, and the palette turns
  on one distinction — SIGNAL (cyan) is the machine working and is the interactive colour, SUMMONS
  (coral) is what waits on a PERSON, IRIS (violet) is structure. Type says who wrote the words:
  system-ui for the console's own voice, mono for anything a counter emitted, a reading serif for
  anything a mind wrote. Dark is the default with a real three-state theme; every token is defined
  for both. `views.css` builds only on those tokens and adds no colour of its own.
- Tests accompany every module in the same commit; `ScriptedEndpoint` in `tests/conftest.py` replays
  canned actions and is the main engine harness. Endpoint adapters are mock-tested; anything touching the
  network hides behind `RSCHED_LIVE_TESTS=1`.
- `ruff check` (select ALL — every pyproject ignore names its house-style reason) and `mypy`
  are green in every commit — on the FULL repo, not just changed files; pre-commit enforces
  both. New ignores need the same one-line justification the existing ones carry. The two
  seed trees are excluded (`extend-exclude`): `library-seed/workflows` are never-executed
  ast-parsed pattern files gated by `workflows/lint.py`, `util-seed` are PEP 723 scripts
  gated by `utils_lib.header_problems` + their `--selftest`.
- ONE outbound notification seam: any engine/daemon-implicit "reach the user" send goes through
  `rsched/notify.py` (see docs/notifications.md); new channels become a permission + a notify
  transport, never an inline util call. There is currently NO such transport and no
  `notify.py`: 0.230.0 deleted every engine/daemon-implicit outbound send (the Discord
  decision mirror, the OAuth-reauth ping, the background-task ping), so a message to a
  person is always an explicit util call by the run, gated by a `messaging-*` permission.
  Browser push (`web/push.py`) is the WEB channel's delivery arm — it renders the
  open-decisions record and is the only away-from-console tier.

## Versioning

`src/rsched/__init__.py` `__version__` is the single source (pyproject reads it via hatch's
version hook) — bump the minor on every user-facing revision. `/api/status` pairs it with the
running checkout's git commit stamp; the header's brand shows `v<version>` (tooltip = commit).
A bump MUST land with a matching `## [x.y.z]` CHANGELOG.md header in the same commit —
`tests/test_policy.py` (also a pre-commit hook) fails on a mismatch.

## Deploy

`deploy/install.sh` (idempotent host install: venv, config + token, seeds, systemd user service + linger)
or Docker (`docker compose up -d` — a disposable engine-only image; source, config, `~/.credentials`,
`~/routines`, `~/conversations`, `~/background`, the messenger session stores
(`~/{telegram,signal,whatsapp}-sessions` — a linked session IS the credential, so losing one
unlinks the account), and the library repo are all bind-mounted, so the
whole system migrates as a tarball of those dirs — EVERY data home must be a bind, or it dies with
the container layer on recreate. That inventory has ONE copy, `deploy/state-paths.sh`, read by both
consumers: `bundle.sh` writes the one-shot migration tarball (DOCKER.md's flow ends by
decommissioning the source, which is why a frozen snapshot is fine there) and `backup.sh` mirrors
the same homes incrementally with rsync (nightly via the `rsched-backup` user timer, which
`install.sh` deliberately does NOT install — the mirror root is host-specific). **The tarball is not a backup** — `routines` and
`conversations` are rewritten by every run, so it is stale within minutes, and a nightly re-tar
moves gigabytes to capture megabytes. Keep the two lists in the one file: a second copy is how five
data homes went unbundled for a release. **`docker compose up -d` NEVER reloads code**: the source is
bind-mounted, so compose compares the CONFIG, finds no drift and no-ops while the running process
keeps the modules it imported at boot — a green `compose config` and a `Container rsched Running`
both look like success and mean nothing about what is live (probe a changed behaviour through the
API to know). Shipping code needs the process itself replaced: drop the RESTART SENTINEL
`~/routines/.control/restart.request` (`{"reason": …, "requested": <iso>}`) and the daemon DRAINS
— starts no new runs, waits for every in-flight one, never kills a run, defers while a run is
parked on the user — then exits into `restart: unless-stopped`, which relaunches it on the new
code. This is the path self-audit uses after a `__version__` bump, and it is the right one for a
hand-made change too; `docker compose restart rsched` is the blunt equivalent that bounces the
process immediately and takes any running routine with it. The host's `/etc/localtime` + `/etc/timezone` ride along read-only
so the container keeps the host's zone; `schedule.server_tz()` honors TZ env / the zoneinfo key /
`/etc/timezone` / the localtime symlink, in that order). Server config:
`~/.config/routine-scheduler/config.yaml` (generated with a random token on
first boot by `bootstrap.ensure_config`, so a fresh deploy is never an open API). Web UI on `:8321`,
two-tier bearer auth (the operator token, plus a generated `routine_token` — what runs get injected
as `RSCHED_API_TOKEN` — which is refused on config-mutating routes); `RSCHED_BIND` / `RSCHED_PORT`
override for containers. First launch redirects to
Settings until setup (secrets, endpoints + system model, GitHub device-flow) is finished; the
library repo has NO settings surface — the library-sync routine manages it exclusively.
