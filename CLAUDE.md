# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through one JSON
action per turn. **A second AGENT LOOP in the path is banned**: it fights this harness and hides the
conversation. Endpoints are model TRANSPORTS only (docs/architecture.md). Routines run code through a global util,
plus — for the ONE-OFF only — the `shell` ACTION behind the `shell` capability. Every util
subprocess runs inside a Landlock sandbox scoped to
the run's permissions INTERSECTED with the util's own `fs:` declaration, and a `shell` command
runs in the same jail on the widest of those terms — the run's granted roots, no store secret
(docs/sandboxing.md). All three callable kinds run through ONE process seam
(`utils_run.run_jailed`): its own process group ended SIGTERM-first (`procgroup.terminate`) at
the deadline or the moment its run is aborted (`RunContext.aborted`, polled — no abort signal
reaches the group's own session), tempfile capture read through a capped reader, and what was
printed before the group ended is kept. The "a run never writes `routine.yaml`" seal holds in BOTH layers (D179): the action gate, and
the jail itself — Landlock has no deny rule, so `ownseal.mount_own_dir` mounts the routine's own
directory LIST-ONLY and grants read-write only to its unsealed children, and
`ownseal.sealed_names` subtracts `routine.yaml`, `.memory/`, `runs/`, `.util_outputs/`,
`.doc_cache/` and (without the recipe-authoring permission) the recipe from that set, so no
subprocess can write them however it writes (docs/sandboxing.md). The residual is where the jail
does not engage at all — `sandbox: off`, or a kernel without Landlock — and there `ungated_write`
plus the runner REPORTING a change it sees is again the whole mechanism. The instruction contains only the task; cross-cutting conduct is
a set of GENERAL RULES with ONE library copy each (`rules:` in routine.yaml holds slugs — the run
reads the prose with `read_rule` and applies the principle to its own case); schedule, PERMISSIONS,
workdir, budgets, and model roles are routine config (`routine.yaml` / UI) — the routine's OWN
values, read against the library **settings PATTERN** it follows as a reference, never a layer
(docs/patterns.md): nothing is merged under the file, so it says what the routine IS and every
value is edited where it lives. A proposal to change them — creation's, the recommender's, a
pattern switch's — is a PENDING change the person keeps or drops with ONE accept, never a write.
Routines that work together SHARE A STORE — a directory under `.control/group-stores/` named
among each one's own write roots (`rsched/sharedstores.py`).

## Where the detail lives

This file is the working tier: what to run, what must not be repurposed, and the gotchas
that are not discoverable from the code. Subsystem narration lives in `docs/` — read the
one you are about to touch, not all of them.

- `docs/architecture.md` — the full subsystem reference (engine loop, endpoints, routines
  on disk, child runs, conversations, libraries & seeds, daemon ownership, OAuth, machines)
- `docs/prompt-anatomy.md` — every string the orchestrator sees, and why. Revise it with ANY
  change to composer / loop / actions / schema_guard wording; `tests/test_prompt_anatomy.py`
  fails on drift
- Compaction's archive is ENGINE-INDEXED (`compaction._build_index`): the model supplies each
  file's content and a one-line `about`, the engine supplies the filenames and therefore writes
  `INDEX.md` — the same split that governs `.memory/INDEX.md`, because a model-authored index
  named files the engine then renamed and 36% of live history reads returned ENOENT. Every
  archived file has exactly one index line, carried forward by the engine; the prior index is
  never re-fed to the model. `engine/recall.py` surfaces one archived file when the action
  overlaps its topic, and `window._warn_before_eviction` gives the run one turn to move what
  matters into a durable store first. Three invariants the gate depends on, narrated in
  docs/architecture.md: it is sized in CALIBRATED tokens (`window.note_prompt_size` →
  `_calibrated_window`, read through the one line in `compact_if_needed`), `maybe_compact` takes
  the cap as an ARGUMENT so the caller's decision is the only one, and `clamp_to_cap` never
  touches message 0 — the composed system prompt is the largest body in every run, so ordering by
  size cut the recipe's own contract first and rewrote the cached prefix from byte zero
- The reminder layer is ON by default at `local` (`reminders: "local"` in DEFAULT_CAPABILITIES
  and in every settings pattern — a SETTING, no permission behind it): a caution a run leaves
  itself about its own actions is ordinary conduct; a layer nobody switches on never learns
  anything. The dial governs AUTHORING: at `local` a run writes its own and applies every CURATED
  reminder that reaches it — a curated one declares its reach, `universal` (every routine whose
  action matches) or `listed` (only routines whose `shared_reminders` name it, which a pattern
  carries). `global` — writing the curated store, each write approved — is the curator's:
  rules-review holds it and runs a census-driven `reminders` stage. A label is recorded only
  against a hold of that reminder in that run, once. The four-way tally has exactly ONE automatic
  reader (`reminders.looks_too_broad`); nothing demotes a reminder behind the run's back
- `docs/reminders.md` — the consequence-reminder layer (the pre-execution hold, the two
  stores and a curated one's reach, the curation process, the four-way tally that tunes a
  pattern); `docs/patterns.md` — settings patterns, pending changes and the one accept;
  `docs/run-gates.md` — the declarative checks that skip a fire with nothing to do; `docs/rule-assists.md` — its curated
  half: a rule's own `assists:` block surfacing its operative line at the moment it applies
  (why timing is possible where compliance-checking is not, the seven moments — a pre-action
  HOLD plus six free ones, including both ends of a child run's life and `run-end`, whose
  `note` reaches the NEXT run through `state/notes.md`, coupled to their
  payloads — the `action` payload, where a rule
  PERFORMS one authored action at the turn boundary with no model turn, and the
  one-shot migration each batch needs to reach a live library)
- `docs/rules-permissions.md`, `docs/curated-rules.md` — the general-rules layer (each doc's
  `effect:` line — what a routine holding it DOES differently — is what the page labels its
  on/off control with, and the linter requires it), the two-layer
  permission set, the SETUP SURFACE (`readmodels/surface.py` is the JOIN; the rows come from
  `surface_needs` / `surface_schedule` / `surface_caps` / `surface_outcome` speaking `surface_nodes`' vocabulary,
  `readmodels/remedies.py` says the same rows in WORDS, `library_impact.py` reads the join
  backwards, `daemon/library_watch.py` catches changes with no writer), the ACCESS-REQUEST
  grant model (entities.py ids; allow/deny × now/forever, plus
  allow-once for the once-grantable classes: turn actions, secrets, fs-read and fs-write, D76), and each curated rule's provenance
- `docs/child-runs.md`, `docs/background-tasks.md`, `docs/triggers.md`, `docs/schedule-once.md`
  — the child-run and firing mechanisms; `docs/jobs.md` — the JOB layer (one routine whose
  regular duties are several standing jobs: the setting, the store, the workspace as working
  directory, gated processing, which jobs are due)
- `docs/lanes-tags.md` — how routines relate to each other on THREE axes: a LANE is when
  they fire and in what order (daemon-owned, at most one, enforced), a SHARED STORE is which
  files they read and write together plus the notes channel between them (a directory under
  `.control/group-stores/` named in each routine's OWN `fs_write_roots`, any number), `tags:` is
  what a routine is about. Read it before touching any of the three — their cardinalities and
  their owners differ, which IS the design
- `docs/conversations.md`, `docs/playbooks.md` — interactive sessions and reusable briefs. A
  conversation's spine is EMERGENT: it writes its own `state/plan.md` (inlined at the top of every
  reply by `state_digest`) where a routine gets `stages/` + `phase.json` compiled at creation —
  don't "fix" a conversation by giving it a compiled workflow
- `docs/status-pages.md` — the RETIRED Steward hub (2026-10-28): a run's two-part finish
  summary (`**Process:**` then `**Work:**`) and its blocking/closing asks replace a published
  page; the two own-body pages left (deficit-coach, birthday-admin) carry their publishing
  conduct in their own recipes, on the kit whose `CONTRACT.md` says how
- `docs/items.md` — the maintenance-item index (findings, decisions, bug reports): the
  item shape, the status vocabulary and its precedence, the `first_seen` age stamp and the
  changelog join;
  `docs/messages.md` — a routine's four message folders (the Messages page/D74), the
  per-folder write surface, and the outbox-retraction decision
- `docs/sandboxing.md`, `docs/endpoints.md`, `docs/decision-models.md`, `docs/oauth-connections.md`,
  `docs/remote-machines.md`, `docs/browser-sessions.md`, `docs/desktop-sessions.md`, `docs/darknet.md`, `docs/usenet.md`, `docs/notifications.md` — the outward-facing
  surfaces. The agent DESKTOPS are one VM per routine (one pointer and one focus per screen —
  the shared browser needs no such split, each caller owns a tab); the broker learns WHICH
  routine calls from a one-time file the util writes into its own jailed working directory,
  and a folder mount is proved the same way against the folder, so a run mounts exactly what
  its sandbox may touch. The screens answer a second, operator-only token no routine is granted
- `docs/search.md`, `docs/run-analytics.md`, `docs/authoring.md`, `docs/examples.md`,
  `docs/getting-started.md` — read models, authoring, onboarding
- `docs/designs.md` — specs for work DECIDED BUT UNBUILT (one entry per queued finding, or
  per decision taken before a finding exists).
  Nothing there describes current behaviour, so never read it as a reference; an entry is
  deleted when it ships and its narration moves to the subsystem doc it belongs to. Two
  routines split the work on this instance between MAINTENANCE and FEATURE DEVELOPMENT and never do
  each other's: `self-audit` audits, triages, keeps the item ledger (`audit/report.json`, the
  changelog) and fixes self-evident defects that it can gate within one run; `scheduler-builder`
  builds what the operator DECIDED — a decision self-audit hands over by the line
  `Handed to scheduler-builder` in its row, every `## ` entry here (self-audit gives each a
  decision row), and every report whose title begins `DECIDED:` — carrying anything larger than
  one run as a campaign in a `.audit-wt/` worktree, deleting the entry in the commit that ships
  it and handing each release back through its `state/shipped.jsonl`, which self-audit turns into
  changelog rows — and an item finished WITHOUT a release (config, a recipe edit) as a
  `"commit": null` row there, or nothing ever closes it. An entry there is an ORDER, not a note
- `docs/admin.md`, `docs/run-gates.md`, `docs/revise-recipe.md`, `docs/output-compression.md`,
  `docs/model-gateway.md` — the admin conversation tier, the pre-engine admission gate,
  in-place recipe revision, stdout compression (LOSSLESS stdlib encodings only — grep headings,
  path folding, JSON minified with tables — engine behaviour rather than a setting, no optional
  dependency), and the model gateway every call leaves through (a separate repo's Otari stack)
- `.codemap/` — the derived module/route/contract map `self-audit` works from
  (gitignored; regenerate with the repo-wide code-search util's `map` verb, never hand-edit)

## Commands

- `uv sync` — install/refresh the venv, on Python 3.12: `.python-version` pins the host to the
  interpreter the engine image runs (`FROM python:3.12-…`, kept in step by `tests/test_policy.py`);
  the suite refuses to start on another minor version — a host run on 3.14 once tested an
  interpreter production does not use
- `uv run pytest -q` — full suite (fast, no network; PARALLEL by default via pytest-xdist
  `-n auto --dist worksteal` in addopts — pass `-n0` for a serial run / debugging with `-s`). Two conftest
  env knobs keep it honest AND fast: `RSCHED_SKIP_DOCS_BUILD` (the app lifespan's pdoc build
  — a to_thread task shutdown can only await) and `RSCHED_RETRY_BASE_DELAY` (endpoint retry
  LOGIC runs, the 1s/2s backoff clock doesn't); test_docs_build / test_with_retries_backoff
  clear them to pin the real paths. Single test: `uv run pytest tests/test_loop.py -q`
  or `-k <name>`. Live endpoint smoke tests run only with `RSCHED_LIVE_TESTS=1`.
- **The BROWSER suite is opt-in.** `tests/ui/` drives the REAL console with Playwright over a
  stub runner (no scheduler, no engine, no LLM; see `tests/ui/conftest.py`). Its conftest marks
  the whole directory `ui` AND puts it in one `xdist_group`, and addopts carry `-m "not ui"`,
  because ~276 browser tests against ~2 150 fast ones is the difference between a two-minute
  gate and a thirty-minute one — and it
  is the thirty that decides whether the inner loop gets used. Three selections:
  `uv run pytest -q` (fast), `-m ui` (browsers), `-m ""`
  (everything — **what ships a release**).
  **The browser selections run in the `uitest` container, never beside the daemon**: the
  suite launches a Chromium per xdist worker (`tests/browsersource.py`), and run in the engine
  container it shared the daemon's 5 GiB memory cgroup — the kernel's OOM killer took the DAEMON
  eight times between 2026-10-22 and 2026-10-30, each time ending the container and orphaning
  every live run. `uitest` is the engine image in a cgroup of its own, mounting the checkout and
  a read-only browser cache and nothing live: `docker compose exec uitest
  /opt/rsched-venv/bin/python -m pytest -q -m ui`. The release gate reaches it from INSIDE the
  engine container through a spool (`deploy/uitest/`: the runner is the service's only process,
  `submit.py` streams a job back as if it ran in place, under the caller's own `flock`); a
  caller that can write that spool can already write the code the daemon imports, so it grants
  nothing. To WATCH the suite, attach it to the `chrome` sidecar instead — engine container,
  `RSCHED_TEST_CDP=http://172.30.7.10:9222 RSCHED_TEST_BIND=172.30.7.2 BROWSER_CDP_TOKEN=…`, one
  worker, so its memory stays small. Whenever browser tests are in the selection, `tests/conftest.py::pytest_configure`
  switches xdist to `--dist loadgroup`, so that one group runs on ONE worker while the fast suite
  keeps spreading — three workers rendering into one headful Chrome is what the rerun shield had
  been absorbing. Still serialize any browser run under
  `flock /tmp/rsched-gates.lock`: two concurrent gates is the OOM this box has already hit once.
  EVERY UI change still gets exercised there — it is the safety net that lets the frontend be
  reworked boldly, and deselecting it by default only trades the safety for speed if the absence
  is LOUD. So every fast run ends by naming the suite it skipped, and says so pointedly when
  `static/` carries uncommitted changes (`tests/conftest.py::pytest_terminal_summary`). A suite
  that silently does not run reads exactly like a suite that passes.
  `tests/production_guard.py` is the suite's SANDBOX FLOOR (session-scoped autouse): no test
  may write inside the live instance's data homes, and none may spawn this package's CLI —
  that child is a fresh interpreter that would load the production config. Never weaken it to
  make a test pass; a test that needs to write there is pointed at `tmp_path` instead.
  `tests/render_guard.py` is its RENDER FLOOR: every observation `executor.dispatch` or
  `actionroute.dispatch_action` returns in a test is RENDERED, and a renderer that raises FAILS
  the test — the engine's own floor, which keeps a live run going, raises in the suite. A test
  of the result DICT proves nothing about the words the run reads: 0.410.0 tested three refusals
  that way and the first live one ended a run. A handler test past both seams renders its own.
  `tests/test_double_conformance.py` is its DOUBLE-CONFORMANCE FLOOR: every attribute a test
  double ASSIGNS must exist on the class it impersonates (`loop` → `EngineLoop`, `ctx` →
  `RunContext`, `budgets`, `transcript`), read statically with `ast` over `tests/**.py` — never
  by importing them. F644 is why: `engine/ladder.py` dispatched through `loop.subs` where the
  real name is `loop.subruns`, and the ladder's tests passed because their fake loop defined
  `subs` to match the caller, so no rung judged anything for months. A double's attribute
  surface is authored by the test out of the same misreading that produced the caller, so the
  two agree with each other and with nothing else. `SimpleNamespace` stays the suite's double
  (410 of them) — the floor checks what they assign, bans nothing. A real attribute may be
  declared in any of FOUR places and the checker reads all four (class annotations, class
  assignments, `self.x =` in its own methods, and a COLLABORATOR's `loop.x = …` anywhere under
  `src/` — `_recipe_unlocked` exists only because `engine/loopsetup.py` assigns it), so
  `dir(cls)` is not the surface. Its `MIN_DOUBLES`/`MIN_SURFACE` floors make a detector that
  stops finding doubles red instead of passing vacuously: widen the recognised shapes, never
  lower a floor. The extraction (the `ast` reader both rules share) is `tests/double_survey.py`;
  that file is the reader, this one is the rules — the same split as
  `tests/class_survey.py` / `tests/test_static_class_contract.py`.
  Its SECOND rule runs the other way and F766 (0.448.0) is why: a `SimpleNamespace` the real
  dataclass would have CONSTRUCTED from the same keywords is itself the violation
  (`test_no_namespace_stands_in_for_a_real_dataclass`). The stand-in at
  `tests/test_util_guard.py:85` assigned `parsed` and `text` — both REAL `Completion` fields, so
  the assigns-only rule was green — and the release broke on the field it OMITTED
  (`parsed_rest`, F754), which `engine/completion.py` had just started reading. No check of
  assigned names can see a field that is not there, so the rule removes the hand-built surface
  instead of policing it: **a scripted endpoint returns a real `Completion`, never a namespace
  shaped like one**, and the same goes for any `endpoints/`/`engine/` dataclass with a required
  field. Recognition is by SHAPE, so it needs no coverage list to maintain — which is the point:
  measured on this tree, 441 keyworded `SimpleNamespace(...)` sites in `tests/` yield 4 hits, 0
  false, and it found a site the defect report's own grep had missed. Scope is `endpoints/` +
  `engine/` and a claimable class needs ≥1 required field: over all 48 dataclasses under
  `src/rsched` the same rule flags 12 sites of which 8 are false, because a dataclass with no
  required field (`GrantPolicy`, 20 fields) matches any namespace at all. Its floors are on the
  scan's MACHINERY (`MIN_SCOPED`, `MIN_NAMESPACE_SITES`), not on its hits — zero hits is the
  healthy state, so a floor on hits would be a floor on the defect.
- `uv run ruff check` + `uv run mypy` — the strict quality gates (ruff runs `select = ALL`;
  every ignore in pyproject.toml carries its house-style reason). Both MUST be green in every
  commit; `uv run pre-commit install` wires them into git.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=catalog-name` overrides a model role (catalog names, not endpoint:model pairs) and `--effort kind=level` a role's reasoning effort (effort is its own setting, `routine.yaml` `efforts:` — never a catalog row per level); `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>] | drain | postdeploy`
  — see `rsched --help`. `validate` checks every routine's `routine.yaml`, lints its recipe
  prose (`main.md`, `stages/`) AND reports its SETUP SURFACE (`readmodels/surface.py` joined with
  `readmodels/remedies.py`: what the held docs, bound rules and reserved utils still need, each
  unmet line ending in the remedy that settles it — a `blocks` row fails the command, a
  `warn`/`note` row is reported only); `lint` covers the library. `engine-run` is internal
  (daemon-spawned). `drain` and `postdeploy` BRACKET A DEPLOY and are the only two commands that
  are about the instance rather than about its config: drain quiesces the box before the restart
  (F746), postdeploy checks the box AFTER it (R2428) — served build vs the checkout, backup
  freshness, state-path ownership, and which instance answered — printing one line per check and
  appending one row to `.control/postdeploy.jsonl`. Exit 1 means ACT; a NOTE keeps it green. It
  exists because a deploy session is this project's highest-yield defect detector (nine of
  2026-10-01's releases say "found while deploying") and was entirely unstructured: the checks are
  pure functions in `rsched/postdeploy.py` over inputs `cli_postdeploy.py` reads, so the
  checklist is testable without a live box.

## Core contracts — extend, never repurpose

- **Phase, stage, state — one word each, and this is the only place that says so** (D193). A
  **PHASE** is the CONCEPT: where a run is in its own progression. It is what the engine
  persists (`state/phase.json`'s `phase` key, status.json, every `assistant_action` event's
  `phase` stamp), what the API serves (`/api/{routines,conversations}/{slug}/phasegraph` →
  `{phases, current}`, `/api/runs/{id}/phases`) and what the UI says — the rail's **phases**
  card, `static/components/phasegraph.js`, `.pg-node`. A **STAGE** is only the FILE LAYOUT a
  phase is derived from: a recipe's `stages/<name>.md` modules, and `stage_phases` /
  `stage_coverage` / `phases_entered` which read them. `stages/` is NOT renamed — 34 live
  routines carry it on disk and the layout is a real, separate thing from the concept. **STATE**
  is reserved for its older, unrelated meanings and never means a phase: a RUN's state
  (`queued|running|finished|failed|aborted` — `static/states.js`), the routine's `state/`
  directory, and a read-model's state. That overloading is why the concept is not called state:
  `state*` outnumbers `phase*` roughly 5:1 in `src/rsched`, so the word cannot carry one more
  meaning. Writing a new surface: ask whether you mean WHERE THE RUN IS (phase), A FILE
  (stage), or THE RUN'S LIFECYCLE / A DIRECTORY (state), and use that word in the identifier,
  the API field, the CSS class and the user-visible label alike.
- **Actions** (`engine/actions.py` — flat schema on purpose; weak models and Ollama grammars handle flat
  far better than `oneOf`): `util, write_util, remove_util, read_file, view_image, write_file, delete,
  move, mkdir, edit_file, memory_read, memory_write, read_rule, write_rule, script, shell, llm, decide,
  spawn, subtask, detach, schedule_run, create_routine, manage_lane, task, goal, list_models,
  subruns, kill, wait, ask_user, report, do_nothing, finish` (34, `actionschema.KINDS`).
  `do_nothing` (D186) is how a scheduled run states that nothing was due and WHY — no fields,
  the reasoning is `say` (non-empty, checked for this kind alone), and it grounds no `ok`
  finish (`engine/donothing.grounds_a_finish`, read by the loop and the resume rebuild alike). **A reply's actions run IN
  ORDER, inside one turn** (`engine/replyactions.py` for the order, `engine/chain.py` for the run,
  D203): a thinking model chains actions in one reply (JSON text, then the call, the second
  narrating the first as done), so they run as written and every result comes back together at the
  next turn. The chain STOPS at the first failure (`&&`), a hold, a cancel, an abort, a spent
  budget, the cap, or an element failing the four checks every action meets; what did not run is
  named back NOT EXECUTED **with the reason**, and a `finish`/`do_nothing` runs only as a reply's
  FIRST action. A routine opts out with `chain_actions: false`. An adapter that kept only the call
  is how runs narrated edits that never happened (R2443-R2445) — never let a parser keep a later
  action over an earlier one, nor drop one without saying so. The CAUSE is removed at the wire: on the
  Anthropic `auto` route past actions are SENT as `tool_use` calls and observations as their
  `tool_result`s (`endpoints/anthropic_messages.native_turns`), because a model reading its own
  history as JSON text writes actions as text, which ends nothing. The rendering is positional
  and deterministic — never make it depend on a later message, or the cached prefix breaks. **`goal` keeps the
  run's GOALS** (`engine/goals.py`) — what a PERSON asked of it, `b<n>` lines the run transcribes
  as a scribe, each carrying the person's own words (`quote`), which the engine must find in what
  they wrote or it refuses the goal; a later word is needed to reword or drop one. Offered at
  depth 0 only (a child has its parent's brief), in routines and conversations alike. **`job` is the JOB layer**
  (docs/jobs.md), present only where the routine's `capabilities.jobs` SETTING is `on` (`tasks:`
  is the pre-D174 key, still read — `rsched/jobs_compat.py`): the run keeps its standing work as
  jobs in the engine-owned `state/jobs.json` and the engine
  enforces GATED PROCESSING — every job due this run (a `job:`-tagged gate check that found
  work, the job's own clock, or a fire whose checks were never asked) is opened and
  checkpointed before the finish stands, and what a run never checkpointed is CARRIED. An OPEN
  job lends the run its WORKSPACE as the working directory (`run_context.work_dir` — paths,
  scripts, shell, utils, memory, notes), which is how a whole routine becomes a job unchanged;
  every seal stays anchored on the routine dir. **`script` runs
  the routine's OWN `scripts/<name>.py`** — persistent helper TOOLING, deliberately NOT a co-equal
  interpreter of the routine: the recipe
  stays the single interpreter of the task and delegates only judgment-free sub-steps. A repeating
  deterministic step (poll, parse, compute, render) is written ONCE via `write_file` and called
  thereafter — versioned by the routine repo, run on the engine's own interpreter when it declares
  no PEP 723 dependencies and otherwise in a persistent venv at `<routine>/scripts/.venv` (deps
  installed on demand, gitignored; `rsched/scriptenv.py` — F764: the routine dir's ROOT is mounted
  read-only by D179's own-dir seal, so a subprocess cannot create a new entry there at all)
  inside the run's fs jail, with ONLY the granted secrets its
  docstring header declares (the util model — `rsched/scripts.py`) PLUS whatever the utils it names
  on its `calls:` line declare: the library is reachable through `gu` exactly as it is for a util's
  own siblings, DECLARED-ONLY (no `calls:` line → no `gu` on PATH at all; an undeclared or unknown
  sibling is refused rather than run without the secrets and net that declaration carries), one jail
  and one env over the whole call tree. There is no model channel inside — a judgment call belongs
  in the recipe. A BASE kind, like `util` and the memory pair — no capability, no approval dial:
  the blast radius is a subset of the routine's own sandboxed permissions; a switch every
  routine held was ceremony. routine-improver scouts recipes for deterministic prose
  responsibilities and nudges them into scripts.
  `finish` and `report` are ALWAYS_KINDS — available on every
  turn regardless of the workflow's `tools:` allowlist or the capability set. **The engine never ends a run
  the model could have ended itself**: the FIRST budget violation spends a one-time RESERVED FINISH TURN
  (schema narrowed to `finish`, one turn granted, `OBSERVATION (budget spent)` telling it so), and only a
  second violation force-finishes — so a run overruns a budget by at most one turn and the summary is
  always authored. The engine EXECUTES nothing else on that turn: anything outside `ALWAYS_KINDS`
  (`finish`, `report`, `list_models`) is refused at the dispatch seam, so the promise the observation makes is kept even on a
  provider with no constrained decoding. The violation is recorded as a transcript event and as
  `resource`/`limit` fields on the `budget_exhausted` health event and in status.json's
  `budgets.spent`, so which budget ended a run is answerable by a filter rather than by
  reconstruction. **A resumed run that finishes `ok` SUPERSEDES its own earlier not-ok record**
  (`run_corrected`, `rsched/runcorrection.py`, F637/F703): a resume reuses the run_id, so
  status.json, result.md and the usage stream's newest-leg fold all read `ok`, while the
  append-only health stream said "failed" forever — two surfaces permanently disagreeing about
  one run. The correction carries `superseded` and the readers resolve it; the ORIGINAL ROW IS
  NEVER EDITED OR DELETED, because that history is what the stream exists for. It supersedes
  endings only (`run_failed`, `run_canceled`, `orphaned_run`, `run_partial`, a not-ok
  `budget_exhausted`, and `wrong_turn_detected` with `reason=attempts_exhausted`): a turn merely
  rejected or repeated was still paid for, and `finish_gate_failed` claims the checks did not
  run, not that the run failed. **A turn that comes back WELL-FORMED AND WRONG is itself a health event**
  (`wrong_turn_detected`, `engine/wrongturn.py`, R2652): every net in `engine/degrade.py` — the
  module headed "when a turn comes back WRONG" — fires on a turn that failed to ARRIVE, so a
  rejected, repeated or attempt-exhausted turn was visible only as a transcript `error` and two
  per-run counters. It carries `reason`/`turn`/`model`/`effort`, is read on the blocked-work
  surface, and is a MEASUREMENT: it changes no effort, no model and no retry — the effort ladder
  behind it is a later increment that this rate has to justify, so do not wire one here. A finish emitted while an undrained user message waits is deferred so the message
  becomes the next turn (a finish that must stand — the spent reserved turn, an abort — names the
  still-queued message in the summary instead). Budgets are a runaway BACKSTOP, never a pace; do not reintroduce prose that has a run
  ration its work against the turn counter. The 85% warning is an EVENT, not a state: it is said
  once at the warn line and once at 95%, per resource. Riding every observation past 85% made it
  a countdown; runs converged at the ceiling whether or not their job was done. Every action carries `say` (finding-first narration:
  what the last observation taught you + why this action; terse for routine steps, 2-3 sentences
  at decision points; worded per the routine's `deliberation` level) + `kind`, plus an optional
  **`note`** — 1-3 SELF-CONTAINED lines worth keeping beyond the context window, engine-filed to
  `state/notes.md` at no turn cost, stamped run·turn·phase·action (`engine/notes.py`; the stamp is
  an address into the transcript archive; the digest carries the file's tail into the next run;
  curation into `.memory/` stays memory_write's turn-priced job).
  A third turn-free side field, **`role_swap`** (D185, `engine/roleswap.py`), lets the RUN choose
  which model answers its turns: `cheap` re-points its own `main` role at the routine's
  `tool_call` model, `strong` restores the model the routine configured. It is a side field and
  not an action KIND on purpose — a dedicated action would spend one full turn of the expensive
  model asking to become cheap, which is the cost it exists to save. The swap is the same
  mutation the operator's console switch makes (`switches.apply_model_switch`: assign
  `routine.models['main']`), applied at `loop._land` where `note` is filed, so it lands on the
  NEXT completion — `endpoints.for_model` re-resolves the role every turn, so model, window and
  effort self-correct. A position the routine cannot serve (no `tool_call` model) is NAMED to the
  run as an engine note rather than silently served by the system-model fallback. The operator's
  2026-10-16 choice of the run-chosen trigger over a stage- or turn-count rule made auditability
  a condition: D188's per-turn model stamp is what satisfies it.
  Two more SIDE FIELDS ride any kind the same turn-free way, behind the `reminders` capability:
  **`remind`** leaves/revises/deletes a CONSEQUENCE REMINDER — a `(regex → consequence)` pair over
  the canonical action string — and **`remind_feedback`** labels how one's hold turned out
  (could_not / would_have / did / didnt). A matching action is HELD before it executes, never
  after: a caution that arrives with the observation arrives after the consequence. Two stores by
  blast radius (the routine's `state/reminders.json`, autonomous; the library's `reminders/`,
  approval-gated by `remind_confirm`), one hold per action string per run so re-emitting the held
  action IS the confirmation, and the tally is per-routine even for a library reminder
  (docs/reminders.md). A reminder is ONE CONFIGURATION OF A HOOK (`rsched/hooks`, D152): five
  fields — trigger, addressee, payload, delivery, disposition — of which a report, a
  consequence reminder and a mid-run injection are three settings, in the one store
  `.control/hooks.jsonl`. A REPORT is written there FIRST and its `reports.jsonl` row is the
  projection (`rsched/hooks/reportproj`); the reminder and injection paths still mirror theirs
  through `engine/hookmirror`. Two rules bound the mechanism: the run
  tree is the only addressing scheme, and `hold_before_action` is the only delivery moment that
  can change what runs. `read_file` batches
  related reads via `paths` (one turn, one
  observation section per file); `write_file`/`edit_file` may carry `then_script` —
  `["<script>", …args]`, run the moment the change lands, both results in ONE observation
  (`engine/thenscript.py`). It is the ONE field that names another kind's call, so that call is
  gated AS that kind: `validate_action` on the synthesized script (the `tools:` list),
  `hold.before_dispatch` asking about each part (a reminder on `script:x` holds the fused write),
  D39's secret gate before it runs, and `kindsurface` projecting the field out wherever `script`
  is not usable. Scoped to `script` because a util or a shell command riding a write would pass
  every gate keyed on its own kind; `edit_file` anchor-replaces in place so revisions cost the diff, not
  the document — and a `.json`/`.yaml`/`.toml` file that PARSED before an edit or a string
  overwrite may not stop parsing because of it (`engine/fileformat.check_after`): the write is
  refused and the file untouched, so a degraded model's corrupted `replacement` is caught on the
  turn it is made rather than by the next reader (`.jsonl` is excluded — a partial line is legal
  there — and repairing an already-broken file is never refused; a binary, over-cap or non-UTF-8 file
  is refused before any decode rather than ending the run); `write_util` mirrors it — `anchor`/`replacement` instead of `content` patches an
  existing util in place under the same approval + staged-selftest gate (`util show <name>
  --full` returns the complete source; the candidate is selftested under a throwaway
  `staging--` slug and installed at `utils/<name>` only on green — `rsched/utils_stage.py`,
  F584/D161 — so no routine's `gu <name>` ever reaches an untested script). `write_file` is GROUNDED: overwriting an existing file OUTSIDE the routine's own dir
  is rejected unless this run has seen it (`ctx.seen_paths` — read/viewed/written this run, rebuilt
  from the transcript on resume); the own dir is exempt (state/report rewrites are the normal mode),
  append and new files pass, and `edit_file` needs no gate — its verbatim anchor is self-grounding.
  `delete` and `move` hold the same gate for a path outside the own dir, act on a symlink
  itself rather than what it points to, and may never remove a directory that holds a sealed
  path (a finish line, a routine.yaml) — `delete .` once took a whole routine with it. **`read_file` never
  materialises a file**: it streams the requested window line by line, a directory path returns its
  LISTING (one entry per line, paged like a file), and a binary file or one over
  `fileops.READ_MAX_BYTES` (8 MiB) is refused from a stat plus an 8 KiB NUL sniff BEFORE any
  decode — the refusal names the size. A DOCUMENT — a PDF or an OOXML docx/pptx/xlsx, known by
  the MAGIC BYTES in that same head, never by its name — is CONVERTED instead
  (`engine/docread.py`): the engine runs the library's `doc-read` util in the run's own jail,
  `offline` and with no secret, which streams Markdown into a private DROP DIRECTORY under
  `tempfile.gettempdir()` (`docread.DROP_PREFIX`) that the ENGINE then moves into the
  content-hash cache `.doc_cache/<sha256>.md` (engine-owned, gitignored, pruned). The two steps
  are not a nicety: `.doc_cache/` is one of `ownseal.SEALED_DIRS`, so the util has no right to
  create a name in the engine's own cache, Landlock has no deny rule to except it with, and
  pre-creating the file does not help because the util atomic-writes BESIDE its `--out` target.
  Handing it the cache path killed every FIRST conversion in a routine dir with `[Errno 13]
  Permission denied` — invisible until 0.462.0 because a cache HIT returns before the util runs.
  read_file pages the cache file like
  any text — `start_line`/`max_lines`/`start_char` address the converted text, at most
  `docread.MAX_PAGES` (200) pages per conversion, and the observation says it is a conversion and
  names every page with no text layer (a scan is reported, never silently OCR'd). The engine
  still never holds the document or its conversion whole, so a document over the cap is
  converted rather than refused; a failed conversion is never cached and falls back to the
  binary refusal with what failed appended. The listing and the refusal both GROUND the path (a stat
  is a look; `history.seen_paths` reads the refusal's `size` key back on resume), so the delete
  gate never sends a run to read a media file to satisfy it (one that did decoded a 1.5 GB .mkv
  into a str twice and swap-thrashed the host for five hours). A shell `ls` does not ground — the
  engine sees only what read_file returned.
  There is ONE **CHILD RUN** concept (`engine/child.py`) — an isolated run with its own dir, its
  own budget, its own recipe, and a declared relationship to its parent. `spawn` (parallel),
  `subtask` (sequential) and a conversation `branch` are three scheduling MODES of it, never three
  concepts and never a fourth action kind; `engine/child.py` owns the mode vocabulary the prompt
  renders and the hand-back path, so the kind copy, the observations and the docs cannot drift
  apart (that drift once had the prompt claim children share the parent's working directory).
  A child's seals are the ROUTINE's, not its workspace's: the write gates cover every routine dir
  a run can reach, compared resolved (`reach.routine_dirs`) — anchored on `ctx.routine.dir`,
  a child could rewrite the recipe, `.memory/`, the finish line and the parent's control.json.
  Its TOOLING is the routine's too: a `script` a child calls is looked up in its own workspace,
  then in the routine's `scripts/` (`executor._script_homes`) — a child holds no `shell`, so
  without that it could not run the test gate of a parallel build it was briefed to test.
  Every mode obeys the same contract: isolation, a budget sliced from the parent's remainder, and
  a HAND-BACK — summary always, FILES by the child writing into its own `artifacts/`, which the
  engine copies to the parent's `artifacts/` and NAMES the landed PATHS in the one
  `CHILD RUN FINISHED (<mode>)` notification. `spawn`, `subtask`, a conversation `branch` and a
  detached task's delivery all hand back through ONE implementation (`engine/child.py`:
  `handback_dirname` / `collect_handback` / `handback_text`) — three landing places
  (`from-sub-<n>`, `from-branch-<slug>`, `from-bg-<id>`), one copy, one wording, and every one of
  them names the paths rather than a count. Collection lives in `subruns._collect`, the child's
  single finalization point: two paths report an exit (`wait` and the turn boundary), so anything
  that must happen once per child belongs there and not in a reporter. One child-task executor,
  `engine/childrun.py`; a child picks its pattern from the catalog and never drafts one — a
  pattern is drafted only when a person picks `generate` while creating a routine.
  **`report` is the ONE channel for work that is not the run's own task** — ungated, held by
  every routine. What varies is whether the run can name an owner. UNADDRESSED goes to the
  triage stream self-audit reads; ADDRESSED (`target`) is ALSO delivered into that routine's
  `inbox/`, which its NEXT SCHEDULED RUN drains — it starts no run and wakes nobody. The target
  closes it by reporting back with `answers: "<R id>"`, adding `closes: true` when the reply ends
  the exchange — a closure is born settled; without it the reply is itself a new open report.
  An addressed report is also how an OPERATOR-ACCEPTED PROPOSAL reaches a builder: a `DECIDED:`
  title marks the body as already-authorized work rather than a suggestion, so the receiving
  routine's queue tells authorization from suggestion without re-surfacing the decision. It keeps
  its `R` id and gets no second one — an accepted proposal is one piece of work, not two items.
  A report may also TAKE ROWS OVER (`supersedes`, beside `target`): each named row gets a
  `superseded` event, leaves triage at once and reads the CARRIER's status from then on,
  through a chain if the carrier is itself folded later (first fold wins — a row is in exactly
  one thread). A reply may also SETTLE rows it is not
  answering (`settles`, up to `actionschema.SETTLES_MAX`): folding moves rows into this thread,
  settling declares them finished, and with `settles` a reply needs no `answers` to carry
  `closes` — the shape that ends a drained queue in one reply instead of leaving rows
  settled-in-prose and open-in-the-ledger (F497). That pair is what routing and consolidating
  both lacked; without it every triage pass re-routed work already handed off (F492), and
  "add your evidence to the OLDEST open one" asks the append-only ledger for an append it
  cannot perform.
  Paired with it, `file_report` REFUSES a fourth parallel thread from one sender to one owner
  (`report_threads.OPEN_THREAD_CAP`, D110) and names the open ids oldest-first; a reply and a
  fold are exempt, because both reduce the count and capping the way out is how a cap loses a
  finding. Closing what you received is the SAME RUN's job, not the next audit's (D131): the
  `problem-routing` rule carries a `pre-finish` assist that spends one turn on a run ending
  while `ctx.reports_open` is non-empty.
  Routines that SHARE A STORE have a lighter channel that is NOT the report ledger (F335,
  `rsched/sharedstores.py`): a routine writes `<store>/notes/<addressee>/note-*.json` with an
  ordinary file write and the engine surfaces it in the addressee's state digest at boot,
  dropping it as it reads — no approval, no ledger row, no Messages item, and no new action
  kind. A store is shared by naming it among a routine's OWN `fs_write_roots`, so a note cannot
  leave the routines sharing it; one written for a routine that does NOT share the store would
  never be read, so the engine's write gate refuses it (`sharedstores.note_refusal`) and names
  an addressed `report` as the channel that reaches that routine. A note — or an addressed
  report — to a routine that starts no run (switched off, retired) is refused the same way, and
  every such refusal SUGGESTS where it goes instead (`rsched/recipients.py`): the routine
  carrying the addressee as a job (`origin`, docs/jobs.md "Merging routines") first, then its
  lane-mates, store-sharers and tag-sharers, each one a routine that reads. A note is coordination; a
  report is work an OWNER must act on.
  One `R<n>` namespace, one append-only
  ledger `.control/reports.jsonl` (order rows + `delivered` event rows), one Items type; the
  page shows open → in_progress once drained → settled once answered. Triage is therefore
  FORWARDING, not absorbing.
  **`create_routine` / `manage_lane` are conversation-INITIATED, not conversation-only** (F328):
  a root conversation materializes them; a run with no user in the loop writes a PROPOSAL to
  `.control/pending-creations/` that the Decisions page materializes with one click through the
  SAME `workflows.scaffold` / `rsched.lanes` path. Teach the RENDERERS about the queue, not only
  the handlers — one shared branch checked before any kind's success wording
  (`obs_admin.QUEUEABLE_KINDS`), or a proposal reads as a completed action over an absent
  payload (R1200/R1183). The two handlers live in `engine/admin_handlers.py` beside that
  renderer; `interact.py` is the ASK protocol alone. `create_routine` carries what the
  clarification settled beside the task, each in the user's own words and never folded into the
  instruction prose (R353): `pattern` + `setup` (the settings pattern and its answers),
  `done_when` (the recipe's `## Done when`), `finish_line` (judge-led outcomes and `until`),
  `never` (the recipe's `## Never`) and `produces` — the OUTCOME contract, REQUIRED before
  anything is created or queued (`engine/outcome_words.py`), written into routine.yaml as
  declared because the confirmed draft is the accept. The routine is saved with its pattern's
  values; everything specific to it lands as PENDING changes under "check the changes i
  recommend." — the engine still never writes `routine.yaml`. `manage_lane list` answers directly (naming each
  lane's MEMBERS in fire order, F424); every mutating verb queues. A lane is the TEMPORAL axis
  only — no verb reaches a routine's settings or the stores it shares, because those are its
  own routine.yaml, which no run may write. A within-reply CHILD (depth > 0) is
  refused outright and never sees the kinds: the queue is for a run that HAS a user, just not
  right now. Ungated like `report` — the approval is the gate, and it is a human.
  ONE exception creates at once: `create_routine` with `replaces: <slug>` from a depth-0 run
  holding the `rebuild_routine` capability (the `routine-rebuild` permission, routine-improver's)
  makes a REPLACEMENT CANDIDATE (`engine/candidate.py`) — the old routine's routine.yaml with
  its own slug, on no schedule, in no lane and WITHOUT its `grants`, so it holds no authority the
  operator did not already give and cannot act in the principal's name — and files ONE
  `replace-routine` card. Accepting it is the SWAP (`api_pending._materialize_replace`): the
  candidate takes the old routine's lane place, cron and grants through the ordinary writers and
  the old one is switched off (kept); discarding switches the candidate off.
  `.control/candidates.json` says which routine is a candidate for which.
  `ask_user` asks ONE question or SEVERAL in one action (`questions:`, each entry with its own
  fields — one decision record per question, sharing one `batch` id, `engine/askbatch.py`; a batch
  is never `mode: blocking`). Every record carries its CLASS, `blocking` or `closing`
  (`engine/askclass.py`), which is ORTHOGONAL to `mode`: the class says whether the asking run
  could do its work without the answer, `mode` whether its process waited for one. The subject
  sets the default (an access `request` is blocking, a plain question closing); the run overrides
  per question with `blocks_work: true`.
  `ask_user` carries an optional `default` — what the run DOES when a blocking ask times out —
  and an optional `request` ("<class>:<name>" entity id, entities.py): a typed ACCESS REQUEST the
  Decisions page settles with one of four decisions (allow/deny × now/forever). A `util:` request
  is WIDENED where the record is written (`engine/grantsecrets.widen`) to also carry
  `secret:<NAME>` for each secret that util declares — verb-bounded, undecided-only, named in the
  question — so one decision makes a granted util usable (F562). Forever-decisions
  are written to routine.yaml by the WEB at click time (`grants:` rows = deny tombstones + secret
  exposure; the engine NEVER writes config); now-decisions live in-memory on the run and reach all
  three enforcers (validate_action, the util sandbox's roots, declared-only env injection).
  A deny tombstone is UNDOABLE from the subject's own surface, both homes (F783): the routine
  page's *Declined access* draft replaces the whole mapping, a conversation's capabilities panel
  clears ONE named row (`api_conversation_grants.py`) because it has no draft to accept — rows
  (`secret:` allow-forever among them) arrive there from Decisions clicks while the page is open.
  `memory_*` are the ONLY way into `.memory/` for a RUN (generic file actions are rejected
  there); the engine owns `.memory/INDEX.md` (built from each write's `about`) and the 100-line
  note cap. The operator's file endpoint agrees about the files the engine owns:
  `PUT /api/routines/{slug}/file` refuses `routine.yaml`, `state/finish-line.json`,
  `.memory/INDEX.md`, `.git/`, `runs/`, `inbox/` and `questions/` BY NAME
  (`api_routine_files.NOT_EDITABLE_HERE`), each refusal naming the endpoint or owner that holds
  it — the operator's own `.memory/` notes stay editable there.
  **`read_rule` / `write_rule` are the general-rules layer** — ONE library copy per rule
  (`<library>/rules/`), never a per-routine fork. A rule may also declare **`assists:`** —
  `(moment, predicate) → line` entries that surface its operative line when it becomes
  relevant, so noticing the moment is the engine's job and not the model's. It is not a
  capability (holding the rule IS the decision), the predicate is named and resolved from
  `engine/assist_predicates.py` because a rule may never ship code, and the seed sync being
  ADD-ONLY means each batch needs a one-shot migration to reach live rules (docs/rule-assists.md).
  The MOMENT and the PAYLOAD are coupled: `pre-action` can only HOLD (a chosen action is only
  reachable by stopping it), the free moments can only REMIND. A hold — from either layer —
  goes through ONE seam (`engine/hold.py`): the canonical string is computed once, sources are
  asked in precedence order (reminder before rule: specific before general), the model is
  stopped ONCE per action, and the per-run ledger is keyed `(source, canon)` so the two layers
  cannot spend each other's single allowed hold. `hold.is_hold(obs)` is the predicate the
  fabrication guard and the allow-once spend both read. EVERY once-only guard (that ledger,
  an assist's one fire, the one finish deferral, the verifier's one challenge per line, …)
  counts per GUARD SCOPE — a routine's whole run, a conversation's current REPLY — and a
  resumed leg REBUILDS it from the transcript (`engine/guardscope.py`); a new one gets a
  `rebuild` beside its `configure`, and an event that does not record its decision gains a
  payload key, never a new event type. `read_rule` is UNGATED (a routine must be able
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
  **Captured util and script output is REDACTED of the secret values the engine injected**
  (8+ characters, `captured_output.read_capped`) before an observation, the transcript, a spill
  file or the search index sees it; a slash command (`/util …`) goes through the same
  `dispatch_action` seam as a model action, so the D39 secret gate holds for both, and a handler
  that raises becomes an error observation instead of ending the run. So does a RENDERER
  (`engine/obs_floor.py`): an `error` result is worded for every kind by default, and a renderer
  that raises costs the run one line — the raw result plus the defect, filed as
  `observation_render_failed` — because a resume re-renders every stored observation, and a
  renderer's KeyError was a run no resume could recover. **A `background: true` call
  passes that gate on its STARTING turn, before the thread exists** (F633): the gate asks the user
  and a thread has no turn to block on, so a deferral that skipped it would have moved a security
  decision out of reach — every call-time gate a kind has runs synchronously first. **A deferred
  MUTATION is ordered by the target it declares, never by trust** (D118 phase 3,
  `engine/background_order.py`): `write_file`, `edit_file` and `memory_write` may defer because
  each names the one thing it writes, and `dispatch_action` holds any later action touching that
  target until the write has landed — so a run that never calls `wait` is still correctly
  ordered. Compare a path target as the RUN would resolve it (`run_context.work_dir`), or one
  file reached by a relative and an absolute path counts as two and the barrier misses the stale
  read it exists to prevent. A kind whose handler is not reached by `background._runner` may not
  join that list — `write_util`/`write_rule` are gated in `actionroute._route`, and a
  `then_script` is refused on a deferred change for the same reason: the thread would land the
  change and drop the script with its gate never asked. **There is NO
  concurrency cap** (D166 answered (c), unbounded: the run's own turn budget is the practical limit).
  0.392.0 read a cap of 3 at that seam; the decision row had recorded the option the operator did
  not choose, so 0.408.1 removed the mechanism rather than widening the number (F650) — the
  reasoning for a cap, heard and decided against, is a comment in `engine/background.py` so it is
  not rebuilt as an obvious improvement. **A backgrounded call's model spend books ON COLLECTION** (D167):
  `RunContext.add_usage` parks a reading made on a background thread and `background.collect` books
  it at the turn boundary, because folding into `ctx.usage` from the thread both races the loop's
  own fold and makes a turn's budget check depend on thread timing.
  **A background result the run NEVER READ is said in `result.md`, both ways** (F715/D195):
  `background.settle` returns the line `loopend.finish_run` appends, and
  `engine/background_summary.settle_note` renders TWO distinct sentences — a call still running
  has lost its result, one that landed after the last turn has a result nobody read, sitting in
  the transcript as an `unread` observation. Keep them distinct (the reader's next move differs:
  re-run versus read) and keep the silent case silent. The landed case used to say nothing at
  all, which is exactly the flaw a run cannot flag for itself, having never been shown it.
  Its INSTRUCTION half is the other side of D195: the harness contract (`engine/harness.py`)
  states two opposite failures in ONE paragraph — a claim that outruns the evidence, and the
  OMISSION of a flaw the run does know about. The second is the measured intervention of
  [arXiv 2609.36139](https://arxiv.org/pdf/2609.36139) (*"Language Models Are 'Insecure'
  Reporters"*; a planted negative result was flagged in 2 of 200 reports, 190 of 200 with the
  prompt), quoted VERBATIM as *"Be honest in your response."* — the paper's own alternatives
  ("Be critical", "Be thorough", "Be skeptical") measured worse, so do NOT reword it, and do not
  move it behind a capability, a kind projection or a per-run schema field: a summary is written
  by every run. `tests/test_finish_honesty.py` pins the wording, the paragraph and the doc.
  **Util output too large for its observation is SAVED, not lost** — `engine/outputs.py` spills the
  full captured text to `.util_outputs/<run-ts>/t<turn>-<util>.out` and the observation that lost the
  middle carries the path (so the store needs no index). ONLY truncated output is kept: an
  untruncated one is already in the transcript verbatim, and a copy would duplicate a file the system
  has. Engine-owned and read-only for the run like `runs/`, gitignored on first use (autocommit is
  `git add -A`, and util output can carry tokens), never search-indexed, pruned to the last
  `KEEP_RUNS` runs — retention is a backstop, never a promise.
- **The prompt surface is documented** in `docs/prompt-anatomy.md` (rendered on the Help tab). Revise
  it with ANY change to composer/loop/actions/schema_guard wording — `tests/test_prompt_anatomy.py`
  pins the load-bearing strings and fails on drift. The ENGINE CONTRACT (harness, schema, example — the same
  for every routine, re-sent every turn, 60-85% of a run's first prompt) is held to a SHRINK-ONLY
  budget (`tests/test_prompt_budget.py` over `tests/prompt_budget.json`; `--update` lowers it and
  refuses to raise it): new always-on prose pays for itself by cutting other always-on prose, or
  the ceiling is raised by hand with the reason in the commit. Every run records its prompt's
  size per section (`quality.prompt_chars`).
- **Transcript events** (`engine/transcript.py` `EVENT_TYPES` — append-only JSONL, the engine is
  the only writer): `header, assistant_action, observation, question, answer, user_injection,
  subrun_start, subrun_end, compaction, error, refusal, stopping_update, stages_skipped, finish,
  oversight_dispatch, oversight_directive, oversight_skipped, oversight_no_directive,
  oversight_failed, action_cancelled`
  (20 — `oversight_*` are the escalation ladder's, `engine/ladder.py`; `action_cancelled` is a
  person stopping ONE call, F586/D160-C, written by `engine/loop._observe`). This vocabulary is
  consumed by the web renderer AND the meta routine, so an unknown kind
  reads as corruption: extend the tuple and both readers together, never one alone.
  `Transcript.event` ASSERTS membership, so a type the tuple does not carry is not a rendering
  gap — it is an exception where the record would have been written, and a caller that swallows
  exceptions (the ladder's boundary wrapper does, by design) then loses the record silently.

## Gotchas

Non-obvious rules that cost a run or a commit when missed. Each is enforced somewhere —
by a test, by the engine, or by a past incident.

- **No backwards compatibility.** Never ship tolerant or dual-convention code. Migrate the
  production data in the same change and keep only the canonical form.
- **Migrations are one-shot and machine-checked.** Historical data migrations are NOT kept:
  each runs once on the production instance and is deleted after convergence (a pre-0.8
  backup converts by booting the matching older tag first). Migration code MUST carry a
  `MIGRATION(expires=YYYY-MM-DD)` marker comment — `tests/test_policy.py` fails once the
  date passes, and on migration-shaped code without a marker. The grammar and its one reader are
  `rsched.migration_markers`. **A marker DECLARES only when it opens its sentence**: narration
  about one (`their MIGRATION(expires=…) came due`) is prose, because a tombstone comment that
  quoted its own marker once reddened `main` itself and blocked every release on the host. So
  write a completed migration's tombstone however you like — and never spell a live marker
  mid-sentence, where it still counts. A **back-compat alias table** (a constant holding a
  pre-rename spelling) must declare an expiry too: it is as temporary as a migration, and
  migration-shaped-code detection cannot see it.
  **The hard fail keeps its teeth and gets LEAD TIME** (F628): a second check in the same file
  WARNS — never fails — for every marker inside `MIGRATION_WARN_DAYS` (21), naming the file, the
  date and the days left, so the deadline reaches whoever wrote the migration instead of whoever
  happens to ship on the day it turns red. Six markers in four modules once expired on one single
  day; `pytest -W` and the gate log both carry the warning. A warning that can fail would be a
  second hard deadline under another name, so it never can. And because a warning on a passing
  test reaches nobody, the daemon files the same window into the health stream —
  `migration_due_soon` / `migration_expired`, rendered on the blocked-work surface
  (`daemon/marker_watch`, F702).
- **Documentation is swept, not patched.** On any change, revise ALL affected doc surfaces
  (CLAUDE.md, `docs/`, `static/views/help.js`, README, docstrings) — not the one you were
  asked about. A module DOCSTRING is a doc surface: pdoc renders it on the Help tab beside the
  `docs/` page that may now say the opposite.
- **The quality gates run on the FULL repo**, not the changed files. `tests/test_quality.py`
  runs ruff, mypy AND vulture (dead code — what ruff cannot see is a symbol whose last caller
  went away; `src` and `tests` are scanned together so a src symbol used only by a test is not
  reported), and the engine bypasses pre-commit — so a red gate can otherwise sail through.
  `tests/test_file_size.py` is the fourth, and the only one no tool can do: the ≤350-line bar in
  Standards, as a ratchet over the frozen ceilings in `tests/size_ratchet.json`. A standard
  nothing reads is advice — the tree had drifted to 107 files over the bar before anything
  counted a line.
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
  mutated; per-turn boilerplate is banned. Only compaction (its window clamp included),
  schema-retry cleanup, the media fallback and a rule unbind the user asked to ERASE
  (`switches.apply_rule_drop`) may rewrite it, each invalidating the provider cache by design.
- **What a routine is FOR is its OUTCOME contract, and no run judges it.** `outcome:` in
  routine.yaml (config/outcome.py: a `principal` and up to six `measures`, each a `count` or a
  `level` with a `better` direction) is the operator's declaration; the routine's own
  `scripts/outcome.py` prints `{<measure id>: <number>}` from its state, and the engine runs it
  after every run's last turn (engine/outcome.py — shape-checked only; a failure records
  `error`, never a zero) and records `outcome_measures` in status.json and the usage row, so
  outcome and cost form ONE series (readmodels/outcome_series.py, `GET
  /api/changes/{slug}/outcome`, the Outcome panel that LEADS the development view). Every other
  judge reads the routine's own account — run `ok`, Done-when met, self-checks green — which is
  how freelance-radar read healthy through 89 applications and no interview. The setup surface's
  `recipe:outcome` row BLOCKS until the contract, the script and a Done-when line naming a
  measure all exist (readmodels/surface_outcome.py); it is the routine's own row, so it is never
  library drift and never in a boot note. Not the FINISH LINE: that says when a routine is done
  for good; this is what a routine that never finishes keeps producing.
- **What DONE means has three owners — none of them is a budget** (reports/goal.md).
  WHAT ONE FINISHED RUN LEAVES BEHIND is the recipe's `## Done when` (`engine/donewhen.py`,
  `- d<n> · <stage> — <outcome>`), re-asked every run and never "already met"; a run started by
  hand with a BRIEF answers for its GOALS instead (`engine/goals.py`: the brief seeds `b1`, a
  person's mid-run requests add more, and a conversation keeps its goals across replies — a reply
  declaring `final: true` must meet every open one). A ROUTINE's goals left open at a run's end
  are OBLIGATIONS (`engine/obligations.py`, the engine-owned `state/obligations.json`): the next
  run's ledger starts with them (`source: carried`) and every finish answers for them until a run
  meets one or the person drops it in later words — an approval is a goal, and an answer given
  between runs is recorded at boot as an `answer` event so a run can quote it. One open over
  a week is `obligation_overdue` on the blocked-work surface, and a report nobody owns for a
  week becomes an `unowned-report` Decisions card (`daemon/owed_watch.py`). WHEN THE ROUTINE IS DONE
  FOR GOOD is the operator's FINISH LINE (`state/finish-line.json`, `engine/finishline.py`):
  outcomes each with a JUDGE — `date` (the calendar), `run` (a run proves it, its `met` checked),
  `you` (a run reports the distance and may never claim it) — plus an optional `until`. WHAT A
  RUN MUST NEVER DO is held before the action by permissions, rules and reminders; a
  recipe's `## Never` carries what none of them can hold. The finish owes an `accounting` FIELD
  — one entry per Done-when line, per open goal and per open outcome (`engine/accounting.py`); the gate checks
  presence, shape and a CONTENT FLOOR, never semantics: an evidence clause under two words, or
  made only of words a real account never consists of alone (`test`, `tbd`, `n/a`, `ok`, …), is
  a placeholder and the deferral names the entry verbatim (F748 — a run finished `ok` on
  `d1 met: test`). A field that never ARRIVED is deferred as absent, not as incomplete
  (F805 — nine "incomplete" verdicts about nine entries one run could see in its own reply
  cost it ten turns). A `met` is checked once against the run's own
  transcript (`engine/verifier.py`: FAIL-OPEN everywhere, AT MOST ONE challenge per line per
  run, a re-asserted verdict STANDS and is recorded `disputed`). Budgets stay a runaway
  BACKSTOP. Reaching the finish line RETIRES the routine without writing config:
  `registry.RoutineInfo.retired` is derived from the file, the scheduler builds no fire entry,
  lane chains skip the member as `outcome: "skipped"`, and ONE Decisions card
  (`engine/goalreached.py`) makes it permanent — approving writes `enabled: false` through the
  ordinary PATCH, declining reopens the outcomes (a line the calendar reached is re-dated
  instead; that save withdraws the card). No run writes the finish line; the engine stamps
  only what a finish reported. EVERY FINISHING RUN IS ALSO ASKED FOR ITS OWN RECIPE REVISIONS
  (D200, `engine/recipeproposal.py`) — the ask in the digest's FINISH block, modelled on D195's
  known-flaw prompt, never in the budget-capped harness contract; the answer one
  `RECIPE REVISION: <file> — <change>` line per revision in the summary, with no new action
  field. A scheduled run holds no `write_recipe`, so the lines become ONE `recipe-revision`
  Decisions card (deduped on the lines), and approving it (`web/api_recipe_revision.py`) ARMS
  the existing revise unlock (`engine/revise.py`) and resumes the proposing run with its recipe
  writable rather than writing the recipe from the web. A run that already holds recipe
  self-write makes the edit and files no card. THE OPERATOR MAY REWORD THE LINES BEFORE
  APPROVING (F790): prose has no patch to adjust, so his wording rides the approval as an
  optional body, capped server-side at `MAX_LINES`, and `_instruction()` has a second framing
  saying THE LINES ARE HIS — never tell a leg that an edited sentence is its own, because it
  cannot see where the two differ and will defend wording it never chose. Discarding picks WHICH
  true thing happened (not wanted / already made by hand): the reason is quoted verbatim to the
  routine, so one hardcoded reason for both was a lie its next run reasoned from. Never let the per-run list and the routine's end share one store
  again: sticky + per-run had most of the fleet reading "the job is DONE. Finish NOW" at the top
  of every run.
- **Global chrome is positioned by `base.css` ALONE, and losing that fails silently.** The
  components mounted outside `#view` so they survive navigation — the navigation rail (which now
  carries the side table-of-contents, `components/toc.js`) and the `#docks` column holding the
  LLM activity dock (`components/taskmanager.js`), the browser dock and the desktop dock (both
  `components/screendock.js`) — set no `position` of their own. Delete their stylesheet block and
  nothing throws: the component still builds, still fetches, still updates, and lands in the
  document flow at the foot of every page — a palette migration once deleted both blocks and only
  an operator reading a screenshot caught it, releases later. `tests/ui/test_global_chrome.py`
  pins each to the viewport (fixed itself, or through the fixed `#docks`) — put any new
  out-of-view chrome in that list the same day. The docks are ONE flex column, never separately
  positioned boxes: three independently fixed docks covered one another whenever two were open,
  so opening one now pushes the others up, the LLM panel shrinks and scrolls when height runs
  out, and `test_open_docks_never_cover_each_other` holds it at three widths.
  Being fixed is not the same as being WELCOME: a screen dock is an OVERLAY, and the console's
  reading column (`--rail-w` 212 + `--shell-max` 1240 = 1452px) reaches the right edge at every
  width between 861 and 1900px, so an open dock lies on the Routines page's run-now column and on
  an endpoint card's save-key row. It therefore rests open only at ≥1900px, where the side TOC
  also lives and now ends above it; below that it starts collapsed and a click-open folds again
  on the next route change (`components/screendock.js`).
- **A run never writes its own config through an ACTION.** `routine.yaml` is blocked by FILENAME
  anywhere a run can write, external repos included, on the action path (`grantpolicy`,
  `fileops._write_gate`, `grants`) — which is the only path that CAN block it: the file sits in
  the routine's own directory, every callable kind must have that directory read-write, and a
  narrower Landlock rule beneath an allowed directory subtracts nothing. So a `shell` heredoc, a
  script, or any `fs: roots` util handed a payload CAN write it, and the change lands at the next
  run's boot. `utils_run.run_jailed` reads the file either side of every jailed call and names a
  change on the call's stderr — detection, not prevention, because the operator's own PATCH is a
  legitimate concurrent writer (F337).
- **The engine subprocess INHERITS NOTHING — the spawn names its config and its homes.**
  `engine-run` is a fresh interpreter, so it defaults NEITHER `--config` nor `--homes`
  (`daemon/runner_state.py` `engine_cmd` → `cli.cmd_engine_run`, F394): it loads exactly the
  config it was handed and refuses when that config resolves to different run homes than the
  spawner is using. A spawner whose config was never loaded from a file is refused before a
  process exists. Never give either flag a default — the fallback is `~`, i.e. production,
  and a tmp-homed test once spent real money and real ledger rows there. A model trial's run
  is told its models and efforts the same way (`--model role=name`, `--effort role=level`, from
  the run dir's own `trial.json`).
- **Under memory pressure the kernel takes a RUN, never the daemon.** `engine-run` raises its
  own `oom_score_adj` to 1000 before anything else (`rsched/oomrank.py`), and every util, script,
  `shell` command and test suite it starts inherits that; the daemon keeps 0. The daemon is the
  container's main process, so a kill there ends the CONTAINER and orphans every live run — eight
  times in eight days before 0.478.0 — while a killed run costs one run. Never lower the value or
  make it per-kind: a jailed process cannot raise its own (`/proc` is read-only in the sandbox),
  so the engine is the only place it can be set. And read an OOM from the HOST (`journalctl -k`):
  a restart starts the container in a fresh memory cgroup, so `memory.events` inside it says
  `oom_kill 0` about the kill that caused that very restart — three runs concluded "not an OOM"
  from exactly that.
- **A model trial ends by COUNTING, never by a write.** `trial:` in routine.yaml
  (`rsched/trials.py`) is active while fewer than `runs` depth-0 usage runs carry its id in
  `fingerprint.trial`; every fire while it is active is armed (`trial.json` in the run dir,
  `--model` to `engine-run`, re-read from that dir on a resume). Nothing clears the field when
  the trial finishes — it stays as history until the operator's next accepted change — so never
  add a writer that tidies it up, and keep it out of the fingerprint's config hash
  (`runrecord.NOT_BEHAVIOUR`), or the runs after a finished trial stop comparing equal to the
  identical runs before it. That hash covers an ALLOWLIST (`runrecord.BEHAVIOUR`), and every
  RoutineConfig field sits in exactly one of the two sets — a new field fails
  `tests/test_runrecord.py` until it is decided: hashing a field by default made every cron
  edit read as a behaviour change.
- **Git is never SIGKILLed; a commit always says what happened** (docs/architecture.md,
  "Git writes"). Git deletes its `index.lock` in its SIGTERM handler only, so `libgit.git`
  runs it in its own process group and ends a timed-out call with SIGTERM first — the
  `subprocess.run` timeout's SIGKILL is what left empty locks in two routine repos on
  2026-09-30, after which every write there failed while reads worked. `utils_run.run_jailed`
  ends a timed-out util, script or `shell` command — or one whose run is aborted — through the
  same `procgroup.terminate`, because git runs inside those too. It waits for EVERY member of
  the group, never its leader alone: in a util git is a grandchild (`uv run` → python → git) and
  the leader exits within milliseconds of SIGTERM. Its SIGKILL never depends on the caller
  living through the 30 s grace — `terminate` arms a backstop in a session of its own — which is
  why the daemon's 10 s abort grace (`runner_state.KILL_GRACE_S`) is shorter ON PURPOSE: never
  lengthen it to "order" the two, it would make a stop during a model call wait out the disk's
  grace. A new git call goes through `libgit.git` (with `timeout=` when it
  needs another), never a `subprocess.run` of its own — the two that cannot are named in its
  docstring.
  `libgit.commit` returns a `Commit` (committed · clean · unversioned · failed) and files a
  failure as `commit_failed`; its `routines_home` is REQUIRED so every call site decides
  where that lands. A WRITER calls it directly — never through a per-module `git_commit`
  pass-through, which hides which primitive was reached and is where a divergent default
  (`routines_home=None`, an unscoped stage) would spread unseen. C1.10 converted
  `workflows.library`'s wrapper and its 7 call sites;
  `tests/test_libgit.py::test_no_pass_through_forwarder_stands_in_front_of_libgit_commit`
  holds the line, with `library_docs`/`utils_lib` named as still-held-back and shrink-only.
  A lock is removed only when `gitlock` proves it stale — never widen
  those four conditions to clear a lock faster. The library's `git` util cannot import
  `procgroup`, so it carries the same rule in its own runner: git leads its own group and a
  timeout ends it SIGTERM-first.
- **The setup surface answers "what does this routine still need?" — including WHEN it runs.**
  `readmodels/surface.py` JOINS the effective config against the library's `requires:`/`expects:`,
  the util headers, the live stores and the lane store; the ROWS come from `surface_needs`,
  `surface_schedule` and `surface_caps`, all speaking `surface_nodes`' vocabulary. Three checks
  are easy to break and hard to notice: a member cron a lane's schedule suppresses (D71) names a
  time the routine will never fire at, a routine in no scheduled lane with no cron of its own is
  started by nothing on a clock, and `state/phase.json` must record its phase under the key
  `phase` — the composer reads `.get("phase")`, so any other key writes a file matching
  nothing. Those are NOTE rows: nothing is
  broken, the file is misleading. The BOOT note carries only `blocks`/`interrupts`
  (`surface.BOOT_SEVERITIES`) — a NOTE is for the operator, and a run can neither act on it nor
  be saved a turn by it.
  Every UNMET row carries a `fix`: a machine-readable remedy (`kind` + params) naming WHAT must
  happen, never where a control lives. UNMET is the whole test, not the severity — a NOTE
  reporting a GAP carries one, a NOTE reporting a STATE carries none, and there are exactly two
  of those (`action:write_recipe` "on", `schedule:goal` "retired"), because the only undo for a
  deliberate act reads as a defect report on a routine that is fine.
  **A NEW kind is FOUR registrations** — its words (`REMEDIES` in `readmodels/remedies.py`), the
  client `FIX` map (`static/components/surface-view.js`, imported by the setup-check strip), the
  section anchor it lands on, and its `CASES` row — listed in `docs/rules-permissions.md`. Three
  are bound by equalities so the vocabulary cannot grow at one end alone: emitted kinds ↔
  `REMEDIES` over the five emitter modules' ASTs (`_EMITTERS` in `tests/test_surface.py` — a
  join that moves into a new file joins that tuple or its kinds stop being checked), then
  `REMEDIES` ↔ the parsed `FIX` map ↔ the `CASES` table (`tests/ui/test_surface_fix.py`). The
  ANCHOR is the one nothing static can see: a kind whose link travels somewhere the act cannot be
  performed is caught by that file's per-kind journey, in a browser, or by nobody.
  `rsched validate` adds the instance-level cases no routine's surface can see: a scheduled lane
  with no members; a lane naming a slug that is NOT a routine (routines are deleted out of band
  and nothing cascades the membership away — ARCHIVING is the in-band delete and the one moment
  the web layer knows, dropping the slug from every lane holding it as `lanes_left`; the web
  refuses only the slugs a caller ADDS, so one stale member cannot lock a lane against every
  further edit, F442).
  It also asks whether THIS INSTANCE's config can FAIL OVER (`rsched/conformance.py`, R2196) —
  the question a fixture-only suite is structurally blind to, since every `load_server_config`
  call in `tests/` but two passes a tmp path, and rightly so (`production_guard.py`). Two
  offline properties, no network call: a role whose chain has no reachable fallback (a hard
  provider failure or a classifier refusal then ends the run — R1492), and a chain whose every
  member shares ONE failure domain. A failure domain has two independent axes and sharing either
  is fatal: the endpoint's `base_url` (one host, one outage) and its `key_var` (one credential,
  one expiry). That second axis is the 2026-09-14 incident — `Astra high`'s only fallback was
  `Opus high`, both on `cliproxy`, and eight routines died at turn 0 within two minutes on one
  expired token. `system_model` is checked as the INSTANCE's, since every unset role runs on it.
  REPORTED, never counted: whether a single-domain chain is acceptable is a judgement about this
  instance's providers (a one-provider instance has no other domain to reach), so it prints as a
  `NOTE  failover ·` line beside the setup and recipe notes and leaves the exit code alone.
  Making it fatal is the operator's call.
  `src/rsched/cli_validate.py` is where all of this lives — `cli.py` was at the size bar, so the
  command moved out whole, exactly as F746 moved `abort`/`drain` into `cli_runctl.py`.
  An `expects:` row must be an UNCONDITIONAL presumption — it fires on EVERY holder, and has
  been wrong twice the same way by presuming a write root a holder never needs.
- **A lane's fire table is process memory; the WATERMARK is what survives.** `lane_next_fires`
  is recomputed as the NEXT future fire at every boot, so a lane fire due during a restart,
  a recreate or a drain would otherwise vanish — and D71 suppresses every member's own cron, so
  nothing else fires them (a Tue/Thu lane once lost a whole week, unnoticed). Every
  `lane_runs.arm` stamps `.control/lane-fires.json` (`rsched/lane_fires.py`), and so does the
  scheduler when the operator's global pause skips a due fire on purpose: EVERY path that
  handles a fire moves the watermark, so what boot finds unstamped is a fire nobody was there
  for. `daemon/lane_catchup.py`
  makes up ONE missed fire per lane at boot (`catchup: run_once`, the lane default). Never
  write that file from the web layer and never let boot arm more than one chain per lane.
- **Nothing starts a run but the schedule, a trigger the operator configured, or the
  operator's own click.** A routine reads its inbox when it next RUNS: a report from another
  routine waits (a fleet-wide report trigger fired six runs at once — a message
  is cheap, a run is a whole recipe, and routines answer each other, so it chains), and so does
  a person's answer to a deferred question (a run per answer started three in one second when
  the operator cleared his inbox). Both were reversed; do not re-propose either. Urgency has
  explicit forms:
  a BLOCKING question parks and resumes its run, and the Decisions page's "answer & run now"
  (`run_now: true` on the answer route) is ONE manual fire, the same as the routine page's Run
  now. The report trigger stays an explicit opt-in for a routine whose job IS its inbox. The
  health stream files a `partial` as `budget_exhausted` ONLY when a budget violation forced it;
  a partial the model chose is `run_partial`.
  **The GLOBAL PAUSE is the complement of that sentence, so every self-starting path owes it a
  check** — and `daemon/pause.py`'s docstring is the contract listing them. A path added outside
  it fails SILENT by construction: a pause only ever shows what it stopped, never what it did
  not. `detached.tick` started runs through a paused instance for as long as the feature existed
  (intake's `fire`, the wake's `resume_terminal`, and every undelivered task at boot, since
  `_boot` reconciles before `boot_catchup`) with no event and no log line — F721. The gate
  belongs where the run STARTS, not at the tick's call site, which is why the detached hold lives
  per phase in the manager; what starts nothing (a finished task's delivery) is not held.
- **A run gate may answer "no work" only when it KNOWS there is none.** Every check
  (`rsched/gatekit/`) reads what it cannot establish — a refused login, a timeout, a file that
  does not parse, no earlier ok run to compare against — as WORK; a fire is skipped only when
  EVERY check said no work. Built-in reasons run before any check and cannot be configured away:
  inbox freight, an answer waiting to be read, a note in a shared store, a last run that did not
  finish ok, a changed config or recipe (`daemon/gate_prepare.py`). A wrong run costs one fire; a
  wrong skip silently loses work — prefer a gate over none, never a check that guesses
  (docs/run-gates.md).
- **An inbox message's `via` is not a label — it is the delivery POLICY.** It decides when the
  message is consumed, whether the post-finish reap may resume a finished run for it, whether the
  conversation surface offers it as the operator's own editable text, and whether it counts as the
  user having spoken (`inbox.MACHINE_VIAS`, read by `ctx.user_replies`). It is a CLOSED set
  (`engine/inbox.VIAS`) that the single writer `inbox.file_message` validates, seeded from what
  the code actually writes — a branch hand-back filed on the USER channel was read by the parent
  as the operator speaking. A QUEUED message is edited only through `inbox.rewrite_message`, under
  the inbox lock the drain holds: a check-then-write lost the race to a drain and re-queued the
  edited text, so the run received it twice.
- **A console view acts only while it is mounted, and only on its newest read.** Work a view
  schedules past an `await` or a timer (a remount, a URL rewrite, a scroll, a rail refresh, a
  transcript catch-up) checks the view is still the current one, and of overlapping reads only
  the one started LAST may paint — a late answer otherwise scrolled, re-filtered or reverted
  whatever page had replaced it (tests/ui/test_view_teardown.py, tests/ui/test_read_order.py).
- **A live refresh on a bus event fetches only what that event can change.** The dashboard and
  the activity feed reload on `run_*` events, debounced; `llm_task`/`llm_process` fire several
  times a second during a run and are ignored. ONE reader per endpoint, not one per view:
  `static/questions-store.js` owns the single bus listener, the single 3 s cadence and the single
  in-flight fetch for every surface showing open decisions (the header badge, the notifier, the
  Decisions page, the run view's inline forms) — four of them had four throttles and the daemon's
  load depended on which tab was open; a fifth consumer SUBSCRIBES, it does not fetch. The same
  rule the other way: a component that POLLS takes a predicate for whether anybody can see it
  (`createSubRunTree`'s `isLive`, `activityFeed`'s `isOpen`) and its view's teardown stops it
  (`tests/ui/test_view_teardown.py`, `tests/ui/test_bus_budget.py`). `EventBus.publish` may be
  called from any thread — it hands each put to its subscriber's loop — so a sync route running
  in the threadpool publishes directly instead of hopping by hand.
  Never put a config-shaped or expensive endpoint
  (`/api/schedule/week`, `/api/stats`, 300 runs) on that path — one config-shaped endpoint
  refetched every 600 ms under five active runs queued every daemon request behind it for
  20-50 s. The watch ribbon splits its two halves for exactly this reason: run
  events repaint its rectangles from `/api/runs`, while `/api/schedule/week` rides its own 120 s
  timer and is repainted from cache in between. When the console is slow, `/api/debug/slow` and
  `docker logs rsched | grep "slow request"` say which path, before anyone guesses. The
  server-side half of the rule: a read model on a bus-event path is MEMOIZED on a stat
  fingerprint of its sources with SINGLE-FLIGHT misses (`readmodels/memo`) — `/api/items`
  (0.312.1) and `/api/questions` (0.340.0, three catalog walks per call at 10-20 in flight)
  both starved the daemon before they were; a new read model joins them before it is fetched
  on an event. A read model that is WRITTEN on an event rather than fetched satisfies it the
  other way: the util-stats snapshot has exactly ONE subscriber (`daemon/util_stats_watch.py`),
  which debounces the burst, awaits its own pass so two can never overlap, and runs the compute
  in a thread — it used to be written by every engine at its own run end, which is the per-run
  cost of a fleet-wide view no engine reads.
- **An exclusive machine's compute is QUEUED, not locked.** `MachineConfig.exclusive` makes
  `remote submit` take a ticket instead of launching (`rsched/machine_queue.py`), ordered FAIR
  SHARE — round-robin across ROUTINES by each one's oldest waiting ticket, FIFO within one. The
  submitting run is NEVER blocked: it gets a job id and a position back at once, reads that
  position in CAPABILITIES, and spends the run on work that does not need the machine. Three
  properties are load-bearing and must survive any change: the truth is ON THE BOX (tickets under
  the job root, enforced at the one place that opens an SSH connection), so it survives a restart,
  a recreate and a migration — the daemon only MIRRORS it into `.control/machine-queue/`, at most
  once a minute and one refresh at a time, because every read is an SSH session and an interpreter
  boot; every ticket carries a mandatory DEADLINE, since a detached job has no live process to
  heartbeat; and a machine that cannot be read says UNKNOWN, never FREE, because an unreachable
  box reading as free is the one failure mode that would cause the collision this prevents.
  Cooperative, like every machine guard — a human on the box or a `shell` action bypasses it.
  See docs/remote-machines.md.
- **Cache READS alone cannot tell a working prompt cache from a broken one.** A transport
  that stops resuming its session still serves the static system+tools prefix from cache,
  so `cached_in` stays large and the token count actually FALLS — while the whole
  conversation is re-WRITTEN every turn at 1.25x instead of re-read at 0.1x. Only reads ÷
  (reads + writes) shows it (`endpoints.base.cache_read_share`), which is why every Stats
  slice carries both halves and a run finishing under 0.5 raises `cache_read_degraded`. Read the
  SLICE as well as the ratio: the four days that burned a weekly subscription limit were ONE
  endpoint going bad beside healthy ones.
- **The cache gap BETWEEN two fires is a separate bill, and it is the bigger one.** Everything
  above reads within a run. Between two fires of the same routine the provider's cache expires,
  so turn 1 re-writes the whole system+recipe+digest prefix at 1.25x instead of reading it at
  0.1x — measured over this instance's 686 retained run dirs on 2026-10-26: 462 fires started
  COLD, re-writing 25.2M prompt tokens for **$124.58** above what a cache read would have cost.
  `readmodels/cache_gap.py` carries it per routine on `/api/stats` (F507/D163); it reads each
  run's TRANSCRIPT, because the usage stream sums a leg's cache traffic and cannot separate a
  cold first write from the run's own later re-reads. Two traps it was built around, both found
  by measuring instead of assuming: turn 1's row sits beyond transcript line 8 in 11.4% of real
  runs (pre-turn-1 injections push it down, as far as line 34), and an eight-line scan window
  understated the premium by 22% while every unit test stayed green; and a fire whose turn 1
  cannot be read must count as UNDETERMINED inside the denominator, because a cold share
  computed over only the fires it managed to read reads exactly like a complete measurement.
  See docs/run-analytics.md.
- **What a run COSTS is its VOLUME, never `tokens`.** `tokens` (fresh input + output) is what a
  token BUDGET meters — adapters keep cache traffic out of it — and a subscription's `cost` is
  0.0 on every call, so ranking by either put freelance-radar (33M cached tokens a run) among the
  cheap routines. Every usage row carries `cached_in`, `cache_write`, `volume` (all four token
  classes) and `notional_cost` (`usage_log.spend`); every cost RANKING reads `volume`, and a row
  with `volume: null` is unmeasured, never zero. The dollar figure is priced at the
  instrumentation seam (`endpoints/prices.py`) from ONE source per route — a metered route's
  reported bill or its own published price for that exact id, a subscription route's list price
  from the public catalog — re-read daily; what no source publishes stays UNPRICED (no figure,
  named in `model-prices.json`'s `unpriced`). No static price table, no guessed rate: a fallback
  is a frozen guess that looks like a measurement (operator, 2026-10-17). And a cost BUDGET
  meters that same priced figure: `budgets.max_cost` is checked against
  `endpoints/usage.dollars_spent` — `notional` first, a reported `cost` only as the fallback for a
  reading that never passed the pricing seam. It read `usage["cost"]` alone until F758, which ONE
  adapter writes and no `anthropic` endpoint does, so the documented "ceiling on real provider
  spend" bounded nothing on the route 17 of 30 enabled routines inherit through `system_model`. On
  a subscription route the cap is therefore LIST-PRICE dollars, not an invoice — the only dollar
  figure that exists there — and an UNPRICED model still meters 0.0, so no ceiling binds it.
- **A dead model CREDENTIAL is noticed before a run spends a turn.** `daemon/endpoint_watch.py`
  (R2195) is to endpoints what `daemon/oauth_refresh.py` is to connections: every
  `CHECK_EVERY_S` (10 min, never the 5 s tick, `_off_tick` like every other manager) it asks each
  CLIProxy-managed endpoint for its account list — the LIGHTEST already-implemented probe, never
  the live completion test and never an upstream request — and writes one of four states to
  `<routines>/.control/endpoint-health.json` (`endpoints/endpoint_health.py`, derived state, never
  config): `ok` · `cooling` (all accounts unavailable, a retry expected — a usage limit) ·
  `needs_reauth` (all unavailable, no retry: the credential is dead) · `unknown` (the proxy could
  not be read, or no account is signed in — nothing claimed, `backup_stale`'s rule).
  `needs_reauth` files `endpoint_needs_reauth` once a day while it holds, and the latch lives in
  the RECORD so a daemon restart cannot re-file the fleet. It PREDICTS failed work rather than
  reporting a missed fire, so it is deliberately NOT in `health_stream.BLOCKED_EVENTS`. An
  endpoint with no proxy binding is skipped: its light probe is the credits read, still inline in
  `web/settings/endpoint_probe.py`. Before this, one expiring claude-proxy token (2026-09-14
  06:18) failed eight routines at turn 0 within two minutes and nothing detected it.
- **A model's limits are DISCOVERED, not configured.** `endpoints/limits.py` asks each provider
  what its models' real context window and output maximum are (OpenRouter/Nano-GPT/Ollama have
  metadata APIs; a listing that carries NO figures — a gateway or subscription proxy whose
  `/v1/models` is ids only — falls back to a built-in Claude table,
  then to OpenRouter's public catalog under the name vendors share: `source: reference`),
  caches it under
  `<routines>/.control/model-limits.json` — derived state, never config — and refreshes on a 24h
  TTL from the scheduler tick (new model keys refresh immediately). Context windows use
  `context_tokens` throughout; compaction occupancy is explicitly estimated in tokens, with
  output and action-schema space reserved separately. ONE precedence chain: per-MODEL config → provider → endpoint
  default → floor. The endpoint value sits BELOW the provider because it has always been
  documented as a default a model inherits, and putting it there means nothing has to be deleted
  from an unversioned config.yaml. `resolve()` is on the per-turn path and NEVER fetches — a miss
  is the next tier down. The two knobs are OPPOSITE: the input window is adopted verbatim, the
  output cap is `min(provider max, ENGINE_OUTPUT_CEILING, window // 4)` (`limits._output_cap`; a
  provider that publishes no maximum gets the ceiling), because providers validate
  `input + requested_output <= window` and a 943k-token output limit — or a flat 32k on a 32k
  window — would starve the prompt. The per-provider catalog readers live in
  `endpoints/catalogs.py`; `limits.py` keeps the policy and the cache.
  **And a window the RUN was told must stay true.** The `CAPABILITIES` line states
  `context window ≈ N tokens` once, at boot, from the BOUND model's figure, and the run plans
  every read against it — while the fallback chain's fit test is a preference, not a veto
  (`endpoints/failover.fits`), so a substitute may serve a run six times smaller. A switch that
  steps DOWN in window therefore appends a durable `ENGINE NOTE: CONTEXT WINDOW REDUCED …`
  naming both models and both windows (`engine/substitution.py`, F539) — measured against the
  chain HEAD, not the previous rung, and latched at the smallest window already announced. The
  prompt is append-only under the caching contract, so a note is the only correction there is;
  the one-turn `[MODEL FAILOVER …]` notice beside it is the turn's debris and steers only the
  inherited reply. `llmsectest-weekday:20260922-060001` spent $1.03 over 106 turns on a run that
  was unrunnable from turn 0 for want of this.
- **A config field must declare whether it reaches a LIVE run.** `configflow.CLASSIFICATION`
  (F337) maps every `RoutinePatch`/`ConversationPatch` field to LIVE (adopted at a turn boundary
  — budgets, deliberation, grants) or NEXT_RUN, with the reason the operator is shown;
  `tests/test_configflow.py` fails on an undeclared field. The same file pins
  `ROUTINE_PATCH_FIELDS`/`CONVERSATION_PATCH_FIELDS` to the two models one-to-one — the
  vocabulary a filed `config_patch` is checked in, per the surface its apply will PATCH — so a
  new PATCH field is two declarations, not one. The INSTANCE is the third surface
  (`{"server": {...}}` → `PATCH /api/settings/server`), and its `SERVER_PATCH_FIELDS` is an
  ALLOW-LIST, not a model's field set: a new `ServerConfig` field is sealed until someone adds
  it there deliberately, and the auth tokens, bind address, homes and credential catalogs are
  sealed by omission. A patch a routine's own `config_confirm` dial permits for ITSELF applies
  with no click (D191): the engine queues the request (`rsched/autoapply.py` — it still writes
  no config) and the web layer's drain applies it through the same validated writer
  (`web/autoapply_drain.py`), re-judging the dial at that boundary. The sealed set, another
  routine's config, the `server` surface and any ask that also needs an ANSWER are never
  autonomous. Both PATCH handlers signal a live run
  through `control.json` and the engine appends ONE ENGINE NOTE naming EVERY changed field and
  which half it is in — a change that silently does or does not reach a run is the bug.
  **And the ASKING RUN is told whether its proposal landed.** An answer to a question carrying a
  `config_patch` states the verdict on the record, in the HTTP response, on the `/api/questions`
  row and as a line of the asking run's state digest — one owner for the field names and both
  wordings (`rsched/patchreceipt.py`). Answering never applies a patch (R2189, operator
  2026-10-05: only the button), so the verdict is the APPLY CONTROL's to declare:
  `config_patch_applied` on the answer POST the button sends, or
  `POST /api/questions/{qid}/patch-applied` when it applies a patch on an already-answered card.
  **The apply control belongs on EVERY surface that renders the question** — one factory
  (`static/components/configproposal.js`), mounted by the Decisions page's two card shapes AND
  by the pinned blocking-question panel (`components/answerform.js` `questionPanel`: the
  conversation above the composer, the run page). A blocking question is answered where the run
  is waiting, so a proposal the operator can read there and not take is the one state a decision
  surface must never be in (F807/R3060: he could only apply on the Decisions page and asked
  why). That needs the LIVE question to be self-describing: `interact.handle_ask` publishes
  `config_bridge.live_fields` (patch, resolved target, home) into status.json, because
  everything shown while a run blocks is built from that copy — the panels through the state
  stream, and the Decisions page's own live row, which `web/decisions_read` builds from
  `active.question` and not from the durable record. A field added to the record alone reaches
  no waiting operator at all.
  A flag that is only ever `false` is worse than silence — a run was told "Ok" to its own
  budget-raise proposal, recorded the raise as applied and planned 600 turns against a live
  400-turn ceiling (R2778), and a run told "NOT APPLIED" about a patch that IS live would plan
  the same way round.
  **A decision can also be DECLINED** — `POST /api/questions/{qid}/decline`
  (`web/api_question_decline.py`, vocabulary in `rsched/decline.py`). Answering closed an item
  only by SAYING something, a snooze is noise control that leaves it open in every run's digest
  and a defer releases a parked run without deciding, so a refusal was prose and nothing else
  (R2197). The decline writes the ORDINARY answer payload — same consumption path, same archive
  — and adds the VALUE `decision: "declined"` (+ optional `reason`), which rides the answer pair
  to the asking run, the `/api/questions` row and the settled card's red `declined` chip. It is
  deliberately NOT `engine/requests.DECISIONS`: those values decide an ENTITY and seed a run's
  grant overlay, so an access request's "no" stays `deny_now`/`deny_forever` and the decline
  route refuses that record with a 400 naming them.

## Standards

- One responsibility per file, ≤ 350 lines. Split rather than grow. **Enforced as a RATCHET** —
  `tests/test_file_size.py` over `src/**.py`, `tests/**.py`, `static/**.js`, `static/**.css`
  (862 files), with every current violation's length frozen as a CEILING in
  `tests/size_ratchet.json`: a file at or under the bar may never cross it (755 files), a file
  over it may only shrink, and one that comes back under loses its entry for good. After a split,
  `python tests/test_file_size.py --update` lowers the ceilings; it REFUSES to raise one or to
  baseline a newly-over-bar file, so growth cannot be laundered into the record. A shrink nobody
  records is itself a failure — an untightened ceiling is permission to re-grow. An allowlist was
  rejected: it would have exempted exactly the files that needed splitting.
- Prefer a fitting, well-maintained package over hand-rolled plumbing (pydantic validates config,
  tenacity retries, python-frontmatter parses frontmatter, websockets relays the browser screen). The bar is net
  reduction AND net clarity — `paths.atomic_write` and `schema_guard` stay bespoke on purpose.
- Cross-process files are written atomic (tmp+rename) via `paths.atomic_write` — never ad-hoc, and
  through its typed pairs where one fits: `atomic_write_json`/`read_json`, and
  `atomic_write_yaml`/`read_yaml`. Those two carry the dump options (`sort_keys=False` keeps the
  key order a human wrote; `allow_unicode=True` keeps an umlaut readable), so no call site spells
  them and none can drift. `read_yaml` deliberately does NOT swallow errors the way `read_json`
  does: nearly every YAML read here is the first half of a read-modify-write of `routine.yaml`,
  and a default returned for an unparseable file would rewrite the user's hand-broken config FROM
  that default. The loaders that must turn a broken file into a problem STRING catch around it.
  An APPEND-ONLY stream (the report ledger, the health and usage streams, the admin audit, the
  UI traces) is the other cross-process shape, and it has its own pair: `paths.append_jsonl`
  (the rows in ONE `write(2)` on an O_APPEND descriptor — a buffered handle splits a long line
  where another process's line can land) and `paths.read_jsonl`/`jsonl_records`, which split on
  the newline byte alone (`str.splitlines` also breaks on U+2028, which these streams keep raw)
  and skip a torn or non-object row without hiding the rest. The engine's own transcript writer
  and the gate kit (a standalone script that cannot import the package) keep their own.
- `static/` is no-build vanilla-JS ES modules (no bundler, no node, no external assets). Keep it
  that way. **A NEW button's action goes through `util.js`'s `act()`** — it disables the control,
  awaits the call, toasts the outcome and re-enables in a `finally`, which is the only way the
  re-enable cannot be forgotten. The ~36 hand-rolled copies that predate it **STAY**, and this is
  a ruling rather than debt nobody got to (operator, 2026-10-28, answering F786): *"act() is for
  NEW controls only: the 32 judgement sites stay, the 4 mechanical ones ride along the next change
  that opens those files"*. The measurement behind it, so nobody re-proposes the sweep: of the 36,
  only **4** could be converted mechanically (the criterion is "ignores `act()`'s return value AND
  has no post-success remount") — 10 both read the response and remount, 8 read it, 14 remount.
  The rest are judgements with reasons in their own code, e.g. `configproposal.js` throws after
  comparing `res.updated` against every patch key (the R102 honesty gate, which an
  undefined-on-failure return would erase) and `skewbanner.js` deliberately leaves its button dead
  on success. Convert an old site only while you are in that file for another reason; the four
  that qualify are `decision-lifecycle.js`, `swap-card.js`, `unowned-card.js` and
  `conversations.js` — and that last one is shrink-only, so a conversion there must tighten its
  ratchet ceiling in the same change. The design system is `base.css` ("watchfloor"): colour is STATE, and the palette turns
  on one distinction — SIGNAL (cyan) is the machine working and is the interactive colour, SUMMONS
  (coral) is what waits on a PERSON, IRIS (violet) is structure. Type says who wrote the words:
  system-ui for the console's own voice, mono for anything a counter emitted, a reading sans at
  a longer measure for anything a mind wrote (NO serif type anywhere in the console — operator,
  2026-10-12; `tests/test_stylesheet_tokens.py` pins it at both copies of the type tokens). Dark is the default with a real three-state theme; every token is defined
  for both. `views.css` builds only on those tokens and adds no colour of its own.
  Two machine checks hold the palette, on its INPUTS and on its OUTPUT:
  `tests/test_stylesheet_tokens.py` (every colour a token, every token defined for both themes)
  and `tests/test_static_class_contract.py` (every class a `static/**/*.js` module mounts is
  styled by a rule, and a STATE class — `ok`/`bad`/`warn`/`failed`/`unservable`/… — is
  REACHABLE from the module that mounts it). The second exists because being named somewhere is
  not enough: 0.399.2 shipped `ladderstrip.js` emitting a bare `bad` that only
  `.test-result.bad` named, so a rung that BROKE rendered in the ink of a rung that declined to
  run. A severity class is COMPOUND (`chip ok`, `test-result bad`, `panel warn`) or scoped by an
  ancestor the module mounts (`.ladderstrip .bad`) — never a bare token, and never waived in
  that file's allowlist, which is for classes carrying no meaning at all.
  The console keeps PRODUCTION apart from DEVELOPMENT (operator, 2026-10-08): the rail's Work and
  Fleet groups are the routines doing their jobs, its **Develop** group (Changes, Stats, Library —
  one iris band, `.nav-zone.dev` in base.css) is how the system is changing and whether that
  helped. Development information about one routine (its measured changes, model fit, Recipe
  health) lives at `#/changes/<slug>`; the production routine page carries ONE line pointing
  there (`components/dev-line.js`) and nothing else of it — a new development reading joins the
  development view, not the production page (docs/run-analytics.md).
- Tests accompany every module in the same commit; `ScriptedEndpoint` in `tests/conftest.py` replays
  canned actions and is the main engine harness. Endpoint adapters are mock-tested; anything touching the
  network hides behind `RSCHED_LIVE_TESTS=1`.
- `ruff check` (select ALL — every pyproject ignore names its house-style reason) and `mypy`
  are green in every commit — on the FULL repo, not just changed files; pre-commit enforces
  both. New ignores need the same one-line justification the existing ones carry. The two
  seed trees are excluded (`extend-exclude`): `library-seed/workflows` are never-executed
  ast-parsed pattern files gated by `workflows/lint.py`, `util-seed` are PEP 723 scripts
  gated by `utils_header.header_problems` + their `--selftest`.
- **No engine or daemon path reaches a person by itself.** A message to a person is an explicit
  util call by the RUN, gated by a `messaging-*` permission, and a new channel becomes a
  permission plus a util — never an implicit send. Browser push (`web/push.py`) is the WEB
  channel's delivery arm: it renders the open-decisions record and is the only away-from-console
  tier. If an implicit send is ever wanted it arrives the same way PLUS one seam module every
  such send goes through; there is no such module today, so do not import one
  (see docs/notifications.md).

## Versioning

`src/rsched/__init__.py` `__version__` is the single source (pyproject reads it via hatch's
version hook) — bump the minor on every user-facing revision. `/api/status` pairs it with the
running checkout's git commit stamp; the header's brand shows `v<version>` (tooltip = commit).
It also carries `disk_build` + `build_skew` (`web/assetskew.py`, F696): `/static` is served LIVE
from the checkout while every Python object stays at the build the process booted with, so a
release landing mid-day puts NEW javascript in front of an OLD server. The server is the only
party holding both stamps, so it compares them and the console raises a non-dismissible reload
banner (`static/components/skewbanner.js`). The other half of that defect is pydantic's SILENT
drop of an unknown form field: every multipart intake model declares `extra="forbid"`, so a
field the running build does not know is a 422 naming it rather than work the operator loses
(R2546). The mid-RUN sibling, against held Python objects, is `engine/buildskew.py` (F677).
A bump MUST land with a matching `## [x.y.z]` CHANGELOG.md header in the same commit —
`tests/test_policy.py` (also a pre-commit hook) fails on a mismatch.

## Deploy

`deploy/install.sh` (idempotent host install: venv, config + token, seeds, systemd user service + linger)
or Docker (`docker compose up -d` — a disposable engine-only image; source, config, `~/.credentials`,
`~/routines`, `~/conversations`, `~/background`, the messenger session stores
(`~/{telegram,signal,whatsapp}-sessions` — a linked session IS the credential, so losing one
unlinks the account), the library repo, and any PROJECT WORKSPACE a routine works inside
(`~/git-repos/LLMSecTest_agentic` and its read-only grant folder are the first) are all
bind-mounted, so the
whole system migrates as a tarball of those dirs — EVERY data home must be a bind, or it dies with
the container layer on recreate. And every container that WRITES one writes as the instance's uid
(`RSCHED_UID`): both consumers below read the homes as the host user, and one root-written 0600
file fails its home, which keeps no snapshot that night — the root-run subscription-proxy sidecar
this compose file carried until 0.483.0 failed one every night from 2026-09-14 until 0.373.1 gave
it compose's `user:` (`tests/test_deploy_state.py` holds the compose file to this). No model
transport runs in this compose project any more: every model call leaves through the separate
llm-interfaces gateway stack (Otari, with the subscription logins' CLIProxyAPI beside it), reached
on the docker host's bridge address (`docs/model-gateway.md`). That inventory has ONE copy, `deploy/state-paths.sh`, read by both
consumers: `bundle.sh` writes the one-shot migration tarball (DOCKER.md's flow ends by
decommissioning the source, which is why a frozen snapshot is fine there) and `backup.sh` keeps
DATED SNAPSHOTS of the same homes (nightly via the `rsched-backup` user timer, which `install.sh`
deliberately does NOT install — the backup root is host-specific): `<root>/snapshots/<YYYY-MM-DD>`,
rsync `--link-dest` so a file unchanged since the newest earlier snapshot is a hard link into it,
built under a temporary name that takes the date only once every home copied, and pruned — after
a successful run only — to the 14 newest plus the newest of each of 8 older ISO weeks. A single
converging mirror copied a night's damage over the last good copy. Never edit inside a snapshot:
a linked file is ONE file every snapshot shares. **The tarball is not a backup** — `routines` and
`conversations` are rewritten by every run, so it is stale within minutes, and a nightly re-tar
moves gigabytes to capture megabytes. Keep the two lists in the one file: a second copy is how five
data homes went unbundled for a release. **Every compose command runs bare, from the checkout's
root**: the host's file set and profiles live in `.env` (`COMPOSE_FILE`, which `deploy/nat64.sh`
writes while NAT64 is on; `COMPOSE_PROFILES=desktop` on a host running the agent desktops), which
Compose reads on every command.
A file flag or a profile flag REPLACES that selection for the one command — and any file list
drops the gitignored `docker-compose.override.yml` with this host's memory ceilings, which is how
every container ran unlimited from 2026-09-27 until the 0.372.1 deploy (deploy/DOCKER.md, "One
compose selection per host"; `tests/test_deploy_selection.py` fails on a tracked file that hands
compose either flag). **`docker compose up -d` NEVER reloads code**: the source is
bind-mounted, so compose compares the CONFIG, finds no drift and no-ops while the running process
keeps the modules it imported at boot — a green `compose config` and a `Container rsched Running`
both look like success and mean nothing about what is live (probe a changed behaviour through the
API to know). Shipping code needs the process itself replaced: drop the RESTART SENTINEL
`~/routines/.control/restart.request` — its EXISTENCE is the whole signal, nothing parses the
`{"ts": <iso>, "via": "web-settings"}` the web writes into it — and the daemon waits for a QUIET
GAP. **It is not a drain, and calling it one misleads:** a pending restart NEVER blocks a start
(operator, 2026-09-03), so the scheduler keeps firing runs and conversations normally and the
exit comes only once `runner.active_states()` has been empty for `RESTART_IDLE_S` (10s), at
which point it sets the `draining` gate against a fire racing the SIGTERM window, marks the exit
(F480) and signals itself into `restart: unless-stopped`. It never kills a run, and it DEFERS
with no deadline while any run is parked on the user (`waiting_user`/`paused`) — never restart
out from under a dialogue. A LANE CHAIN cannot starve the gap: the boundary between two members
is 5s against a 10s window, so a back-to-back chain closed it for hours, and
`LaneRunManager._fire_next` now HOLDS the next member while a restart is pending and nothing
else is active — the member fires on the new code at the first tick after boot. A detached
background task COUNTS as active: `start_new_session` does not survive the daemon's exit on
either deployment, so excluding it had the drain SIGKILLing the one class of run it exists to
protect. Read `/api/status` (`restart_requested`, `active_runs`) to see what it is
waiting on; `restart_action` in `daemon/restart.py` is the whole state machine.
**An EXTERNAL restart gets none of that** (F746): a deploy, `systemctl restart` or a container
replacement delivers SIGTERM from outside, so no quiet gap is waited for and no state machine
runs — and uvicorn bounds its graceful shutdown to 10s against runs that last hours, so the exit
itself can never wait. Three things cover it instead. **`rsched drain`** is the one to type
BEFORE a deploy: it pauses scheduling, waits for the in-flight runs and exits non-zero naming
what is still running, so `rsched drain && docker compose up -d` stops rather than eating a run
(`--resume` lifts the pause afterwards; the drain deliberately leaves it in place so no fire
slips into the seconds before the process is replaced). **`rsched postdeploy`** is the one to type
AFTER it (R2428): the instance is now running the new code, and nothing ever asked whether it is
healthy or whether what it serves is what was built — `deploy/DOCKER.md` §1 verifies a build
artefact, not the live instance. It is four named checks, each derived from a defect a human found
mid-deploy (a deploy that did not relaunch; the backup that failed 19 nights with no indicator at
all; a root-owned file in a home the backup reads as the host user; the wrong host answering), one
line each, exit 1 only when the operator must act — and one row per session in
`.control/postdeploy.jsonl`, so the highest-yield inspection in this project finally leaves a
record instead of living in the operator's head. When a restart lands anyway, the app
lifespan's shutdown half writes the deliberate-shutdown mark on ANY graceful SIGTERM
(`restart.mark_graceful_exit`) — without it the next boot reaped every live run as
`cause: unknown`, 79 runs over 50 boots and not one ever attributed to a restart — and the boot
RE-OPENS what the restart killed instead of closing it `aborted` (`daemon/reap_resume.py`,
capped at `RESTART_RESUMES` so a boot loop cannot storm; a parked run keeps its durable
question, and a crash or an OOM is never resumed here). An in-flight CHILD run does not survive
either way, and the resumed leg's recovery note says so.
**A relaunch is not guaranteed either.** Docker's restart manager gives up after ONE failed
start, so anything that makes the container fail to start leaves it down with nothing retrying
and nothing said — a vanished bind source is the one that has happened, and the tor and chrome
sidecars stay up and make the box look half-alive. Verify every bind source exists
(`.HostConfig.Binds` AND `.HostConfig.Mounts` — they are separate lists) before dropping the
sentinel.
This is the path self-audit uses after a `__version__` bump, and it is the right one for a
hand-made change too; `docker compose restart rsched` is the blunt equivalent that bounces the
process immediately and takes any running routine with it. The host's `/etc/localtime` + `/etc/timezone` ride along read-only
so the container keeps the host's zone; `schedule.server_tz()` takes the first of TZ env /
`/etc/timezone` / the localtime symlink that names a zone `ZoneInfo` loads, else UTC — and a
schedule that names no zone runs in that one, `config.default_tz()`). Server config:
`~/.config/routine-scheduler/config.yaml` (generated with a random token on
first boot by `bootstrap.ensure_config`, so a fresh deploy is never an open API). Web UI on `:8321`,
two-tier bearer auth (the operator token, plus a generated `routine_token` — what runs get injected
as `RSCHED_API_TOKEN` — which is refused on config-mutating routes AND on three read subtrees
the sandbox forbids, `/api/fs`, `/api/settings`, `/api/debug`, `/api/search`,
`/api/routines/*/secrets`, `/api/desktops` and the two screen relays `/browser-view` and
`/desktop-view` (`web/app.ROUTINE_TOKEN_DENIED_READS`, matched on the path the ROUTER
dispatches, not the re-parsed URL): "read-only" is not "may read anything", since a util
subprocess is handed that token inside a Landlock jail and those GETs list any directory on the
host, name every secret with its declaring utils, dump the daemon's stacks, search every
routine's transcripts and list every routine's own secret names); `RSCHED_BIND` / `RSCHED_PORT`
override for containers. First launch redirects to
Settings until setup (secrets, endpoints + system model, GitHub device-flow) is finished; the
library repo has NO settings surface — the library-sync routine manages it exclusively.
