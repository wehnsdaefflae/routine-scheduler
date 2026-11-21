# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through JSON
actions (one per turn, or an independent few chained in one reply). **A second AGENT LOOP in the
path is banned**: it fights this harness and hides the conversation. Endpoints are model
TRANSPORTS only (docs/architecture.md).

- Routines run code through a global util, plus — for the ONE-OFF only — the `shell` action behind
  the `shell` capability. Every util, script and `shell` command runs in a Landlock jail scoped to
  the run's permissions (intersected with a util's own `fs:` declaration), through ONE process
  seam, `utils_run.run_jailed` (docs/sandboxing.md).
- A run never writes `routine.yaml`: blocked at the action gate AND in the jail
  (`ownseal.mount_own_dir` / `ownseal.sealed_names`, D179), both reading ONE table of sealed
  paths, `rsched/sealtable.py`. Where the jail does not engage
  (`sandbox: off`, no Landlock) `ungated_write` detection is the whole mechanism.
- The instruction contains only the task. Cross-cutting conduct is a set of GENERAL RULES, one
  library copy each (`rules:` holds slugs; the run reads the prose with `read_rule`). Schedule,
  permissions, workdir, budgets and model roles are the routine's OWN config, read against a
  library settings PATTERN as a reference, never a layer (docs/patterns.md). A proposal to change
  them is a PENDING change the person keeps or drops with one accept, never a write.
- Routines that work together SHARE A STORE: a directory under `.control/group-stores/` named
  among each one's own write roots (`rsched/sharedstores.py`).

## Where the detail lives

This file is the working tier: what to run, what must not be repurposed, and the gotchas that are
not discoverable from the code — one line each. **The long form of every contract and gotcha below
is `docs/contracts.md`** (the reasoning, the incident behind it, the invariant to keep); the test
suites and gates are `docs/testing.md`. Subsystem narration lives in `docs/` — read the one you are
about to touch, not all of them.

- `docs/architecture.md` — the full subsystem reference (engine loop, endpoints, routines on disk,
  child runs, conversations, libraries & seeds, daemon ownership, OAuth, machines)
- `docs/prompt-anatomy.md` — every string the orchestrator sees, and why. Revise it with ANY change
  to composer / loop / actions / schema_guard wording; `tests/test_prompt_anatomy.py` fails on drift
- `docs/reminders.md`, `docs/patterns.md`, `docs/run-gates.md`, `docs/rule-assists.md` — the
  consequence-reminder layer, settings patterns and the one accept, the pre-fire admission checks,
  and a rule's own `assists:` (the moments, their payloads, the add-only carry to a live library)
- `docs/rules-permissions.md`, `docs/curated-rules.md` — general rules, the two-layer permission
  set, the SETUP SURFACE (`readmodels/surface.py` joined with `readmodels/remedies.py`), the
  access-request grant model, each curated rule's provenance
- `docs/child-runs.md`, `docs/background-tasks.md`, `docs/triggers.md`, `docs/schedule-once.md`,
  `docs/jobs.md` — child runs and the firing mechanisms; the JOB layer
- `docs/lanes-tags.md` — LANE (when routines fire, in what order; at most one), SHARED STORE (what
  they read/write together; any number), `tags:` (what a routine is about). Their cardinalities
  and owners differ, which IS the design — read it before touching any of the three
- `docs/conversations.md`, `docs/playbooks.md` — interactive sessions and reusable briefs. A
  conversation's spine is EMERGENT (`state/plan.md`); don't give it a compiled workflow
- `docs/status-pages.md` — the RETIRED Steward hub and the two own-body pages that remain
- `docs/items.md`, `docs/messages.md` — the maintenance-item index and a routine's four message folders
- `docs/sandboxing.md`, `docs/endpoints.md`, `docs/decision-models.md`, `docs/oauth-connections.md`,
  `docs/remote-machines.md`, `docs/browser-sessions.md`, `docs/desktop-sessions.md`,
  `docs/darknet.md`, `docs/usenet.md`, `docs/notifications.md` — the outward-facing surfaces
- `docs/search.md`, `docs/run-analytics.md`, `docs/authoring.md`, `docs/examples.md`,
  `docs/getting-started.md` — read models, authoring, onboarding
- `docs/admin.md`, `docs/revise-recipe.md`, `docs/output-compression.md`, `docs/model-gateway.md` —
  the admin tier, in-place recipe revision, lossless stdout compression, the Otari model gateway
- `docs/designs.md` — work DECIDED BUT UNBUILT; every `## ` entry is an ORDER, never a reference,
  deleted in the commit that ships it. `self-audit` audits, triages, keeps the item ledger and fixes
  self-evident defects; `scheduler-builder` builds what the operator decided (entries here, rows
  `Handed to scheduler-builder`, reports titled `DECIDED:`), as campaigns in `.audit-wt/`
  worktrees, handing releases back through its `state/shipped.jsonl` (`"commit": null` for work
  finished without a release). Neither does the other's work. `docs/limits.md` is the opposite
  list: known, undecided, unbuilt
- `.codemap/` — the derived module/route/contract map `self-audit` works from (gitignored;
  regenerate with the code-search util's `map` verb, never hand-edit)

## Commands

- `uv sync` — the venv, on Python 3.12 (`.python-version` pins the engine image's interpreter;
  the suite refuses another minor version)
- `uv run pytest -q` — the FAST suite (parallel via xdist; `-n0` for serial/`-s`). It first waits
  for memory headroom, sizes `-n auto` to it and ends itself over its cap (`tests/memgate.py`;
  `RSCHED_TEST_MEM_WAIT=0` skips). Single test:
  `uv run pytest tests/test_loop.py -q` or `-k <name>`. Live endpoint tests need `RSCHED_LIVE_TESTS=1`
- `-m ui` is the opt-in BROWSER suite, `-m ""` is everything (**what ships a release**). Browser
  selections run in the `uitest` container, never beside the daemon:
  `docker compose exec uitest /opt/rsched-venv/bin/python -m pytest -q -m ui`. Serialize every
  gate under `flock /tmp/rsched-gates.lock` — two concurrent gates is an OOM this box has hit
- `uv run ruff check` + `uv run mypy` — strict gates (ruff `select = ALL`, every ignore carries its
  reason), green in every commit; `uv run pre-commit install` wires them in
- `uv run rsched run-once <slug>` — one run from the CLI (`--model role=name`, `--effort role=level`,
  `--quiet`); `uv run rsched daemon` — scheduler + web UI (what systemd/compose runs)
- `uv run rsched validate | lint | scaffold | abort | drain | postdeploy | reports --wrongly-settled`
  — see `rsched --help`. `validate` checks every routine's config, recipe prose and SETUP SURFACE
  (a `blocks` row or a recipe contract defect fails it). `drain` (before) and `postdeploy` (after)
  BRACKET A DEPLOY. `engine-run` is internal
- `uv run rsched census [--days 60] [--json]` — what the instance uses and NEVER uses (action kinds,
  side fields, config keys, capabilities, health events); read it before adding to a feature
- The test floors (`production_guard`, `render_guard`, double conformance, size ratchet, prompt
  budget, durations) and why each exists: `docs/testing.md`

## Core contracts — extend, never repurpose

Long form: `docs/contracts.md` § Core contracts.

- **Phase / stage / state** (D193): PHASE is where a run is in its progression (persisted, served,
  shown as "phases"); STAGE is only the `stages/<name>.md` file layout; STATE is a run's lifecycle,
  the `state/` dir or a read-model's state — never a phase. Use the one word in identifiers, API
  fields, CSS classes and labels alike
- **Actions** (`engine/actions.py`, `actionschema.KINDS`; flat schema on purpose). A reply's
  actions run IN ORDER in one turn and stop at the first failure; what did not run is named NOT
  EXECUTED with the reason (`engine/replyactions.py`, `engine/chain.py`, D203). Never let a parser
  keep a later action over an earlier one, or drop one silently. A RETIRED kind name (`task`,
  `spawn`/`subtask`/`subruns`) lives only in `engine/renamedkinds.RENAMED`;
  `tests/test_retired_kinds.py` refuses it in every kind table under `src/` and `static/`
- `finish` and `report` are ALWAYS_KINDS. **The engine never ends a run the model could have ended
  itself**: the first budget violation spends one RESERVED FINISH TURN; only a second force-finishes.
  Budgets are a runaway BACKSTOP, never a pace; what is LEFT rides every observation as a fact
  (`turnwindow.budget_left`), the 85%/95% warnings are events
- Side fields ride any kind turn-free: `note` (filed to `state/notes.md`), `role_swap` (D185),
  `remind` / `remind_feedback` (the reminder layer, held BEFORE the action), `then_script` (gated
  as a `script` call). `goal` keeps what a PERSON asked, quoting their words
- `script` runs the routine's OWN `scripts/<name>.py` — tooling for judgment-free steps, never a
  co-interpreter of the task; secrets only as its header and its declared `calls:` utils declare
- `write_file` is GROUNDED outside the own dir (`ctx.seen_paths`); `delete`/`move` never remove a
  dir holding a sealed path; `read_file` streams a window, lists a directory, converts a document
  through `engine/docread.py`, and refuses binaries before decoding
- ONE **child run** concept (`engine/child.py`): `child` with `mode: parallel|sequential` and a
  conversation `branch` are MODES, never concepts. One hand-back path (summary + named artifact
  paths); collection happens once, in `subruns._collect`
- **`report` is the ONE channel for work that is not the run's own task** — ungated; addressed
  reports wait in the target's `inbox/` for its next scheduled run (they start no run).
  `answers`/`closes`, `supersedes`, `settles`, the open-thread cap (D110). A shared store's
  `notes/` is the lighter coordination channel, not a ledger
- `create_routine` / `manage_lane` are conversation-INITIATED: without a user they queue a
  PROPOSAL (`.control/pending-creations/`), judged against the live vocabulary in the queue writer
  (`pendingvocab.problem`). `revises: true` proposes another routine's recipe revision (D198),
  always as one operator card. No run writes `routine.yaml`
- `ask_user` asks one or a batch; every record is `blocking` or `closing` (`engine/askclass.py`),
  orthogonal to `mode`; closing asks ride the finish. Access requests settle allow/deny ×
  now/forever; forever-decisions are written by the WEB, never the engine
- The ENGINE writes `LEDGER.md` (one entry per depth-0 routine leg, `engine/ledgerentry.py`) —
  never re-add prose asking a run to write it. `memory_*` are the only way into `.memory/`; the
  engine owns `INDEX.md`. The REVIEW LEG (`engine/reviewleg.py`) curates memory after a finish
  that gave a reason; never give it a counter trigger
- `read_rule` is ungated; `write_rule` needs `rule-authoring` + its own `rule_confirm` dial. There
  is deliberately no `remove_rule`. One hold seam for rules and reminders (`engine/hold.py`);
  every once-only guard counts per GUARD SCOPE and is rebuilt on resume (`engine/guardscope.py`)
- Captured util/script output is REDACTED of injected secrets; a renderer that raises costs one
  line, not the run (`engine/obs_floor.py`); a `background: true` call passes its gates on the
  starting turn; truncated util output is spilled to `.util_outputs/`
- **The prompt surface is documented** in `docs/prompt-anatomy.md`; the engine contract is held to
  a SHRINK-ONLY budget (`tests/test_prompt_budget.py`)
- **Transcript events** (`engine/transcript.py` `EVENT_TYPES` — append-only JSONL, the engine is
  the only writer): `header, assistant_action, observation, question, answer, user_injection,
  child_start, child_end, compaction, error, refusal, stopping_update, stages_skipped, finish,
  oversight_dispatch, oversight_directive, oversight_skipped, oversight_no_directive,
  oversight_failed, action_cancelled`
  (20 — consumed by the web renderer AND the meta routines: extend the tuple and both readers
  together; `Transcript.event` ASSERTS membership)

## Gotchas

Each is enforced somewhere — a test, the engine, or a past incident. Long form:
`docs/contracts.md` § Gotchas.

- **No backwards compatibility.** Migrate production data in the same change; keep only the
  canonical form
- **Migrations are one-shot**: a `MIGRATION(expires=YYYY-MM-DD)` marker (it declares only when it
  opens its sentence), deleted after convergence; `test_policy` warns 21 days ahead and fails
  after. A back-compat alias table declares an expiry too
- **Documentation is swept, not patched**: CLAUDE.md, `docs/`, `static/views/help.js`, README and
  docstrings
- **Quality gates run on the FULL repo**: ruff, mypy, vulture (`tests/test_quality.py`) and the
  ≤350-line ratchet (`tests/test_file_size.py`; `--update` after a shrink, never to raise)
- **A recipe says WHAT, never which tool**; **a contract names only the task**
  (`workflows/taskcontract.py`: no step, Done-when line or prose about recording the run)
- **Fix the cause, not the symptom** — for LLM-written artefacts, fix the generation prompt
- **The composed prompt is a caching contract**: appended-to, never mutated, except compaction,
  schema-retry cleanup, the media fallback and an erasing rule unbind
- **A routine's purpose is its OUTCOME contract** (`outcome:` + `scripts/outcome.py`), measured by
  the engine after every run — no run judges it
- **A run's turns belong to its task — measured** (`engine/overhead.py`, threshold 0.27,
  calibrated; never move it without re-running the calibration)
- **DONE has three owners, none a budget**: the recipe's `## Done when` (per run), GOALS and
  OBLIGATIONS (what a person asked), the operator's FINISH LINE (`state/finish-line.json`, done
  for good). Every finish owes `accounting`; a `met` is verified once. Recipe revisions are asked
  for at every finish (D200) and land through a narrow revise leg
- **Global chrome is positioned by `base.css` ALONE** — deleting its block fails silently;
  `tests/ui/test_global_chrome.py` pins it. The docks are one flex column
- **A run never writes its own config through an ACTION**; a jailed subprocess can, so
  `run_jailed` detects and names the change (F337)
- **The engine subprocess INHERITS NOTHING**: `engine-run` defaults neither `--config` nor
  `--homes` — never give them defaults (the fallback is production)
- **Under memory pressure the kernel takes a RUN, never the daemon** (`rsched/oomrank.py`); read an
  OOM from the HOST's `journalctl -k`, never the container's `memory.events`
- `runrecord.BEHAVIOUR`/`NOT_BEHAVIOUR` must classify every RoutineConfig field (a field
  hashed by default made every cron edit read as a behaviour change)
- **Git is never SIGKILLed**: every git call goes through `libgit.git`; `libgit.commit` returns a
  `Commit` and is called directly, never through a pass-through; a lock is removed only when
  `gitlock` proves it stale
- **The setup surface** answers what a routine still needs, including WHEN it runs; a new remedy
  kind is FOUR registrations (`readmodels/remedies.py`, the client `FIX` map, its anchor, its `CASES` row)
- **A missed fire is SKIPPED — there is no catch-up** (D237). Do not rebuild any make-up mechanism
- **Nothing starts a run but the schedule, a configured trigger, or the operator's click**; every
  self-starting path owes the GLOBAL PAUSE a check (`daemon/pause.py` lists them)
- **A run gate answers "no work" only when it KNOWS** — anything it cannot establish is work; the
  last run writes the next fire's checks (`state/next-gate.json`) under floors it cannot move
- **An inbox message's `via` is its delivery POLICY**: a closed set, one writer
  (`inbox.file_message`), plus `answerfile.write` for answers — no third writer
- **A console view acts only while mounted, and only on its newest read**
- **A live refresh on a bus event fetches only what that event can change**; one reader per
  endpoint (`static/questions-store.js`); read models on event paths are memoized single-flight
- **An exclusive machine's compute is QUEUED, not locked**; the truth is on the box, and an
  unreadable machine is UNKNOWN, never FREE
- **Cost is VOLUME, never `tokens`**; dollars come from one source per route, unpriced stays
  unpriced; cache reads alone cannot tell a working cache from a broken one; the gap between two
  fires is a separate bill (`readmodels/cache_gap.py`)
- **A model's limits are DISCOVERED and belong to the ROUTE** (`endpoints/limits.py`); a window
  step-down mid-run is announced durably (`engine/substitution.py`)
- **A config field declares whether it reaches a LIVE run** (`configflow.CLASSIFICATION`), and the
  asking run is told whether its proposal landed (`rsched/patchreceipt.py`); the apply control
  belongs on every surface that renders the question
- **A route handler or dependency never closes over an app** — module-level routes read
  `request.app.state` (`tests/test_app_release.py`)

## Standards

- One responsibility per file, ≤ 350 lines, enforced as a shrink-only ratchet
  (`tests/size_ratchet.json`)
- Prefer a fitting, maintained package over hand-rolled plumbing; `paths.atomic_write` and
  `schema_guard` stay bespoke on purpose
- Cross-process files: `paths.atomic_write(_json|_yaml)` / `read_json` / `read_yaml` (which does NOT
  swallow errors); append-only streams: `paths.append_jsonl` / `read_jsonl`
- A new tab from a blob URL opens only through `components/blobtab.js` (`newTabHref`); whoever mints
  an object URL revokes it
- `static/` is no-build vanilla-JS ES modules. A NEW button's action goes through `util.js`'s
  `act()` (the old hand-rolled sites stay — operator ruling 2026-10-28). Colour is STATE, tokens
  only (`base.css`, both themes; `tests/test_stylesheet_tokens.py`, `tests/test_static_class_contract.py`).
  Production pages and the Develop group stay apart
- **Writing standard** (long form `docs/contracts.md` § Standards): a docstring or comment says
  WHAT, the invariant, and a one-line WHY. Dates, run ids, measurements, quotes and "used to …"
  history go in the commit message and CHANGELOG, never the source (`tests/test_prose_history.py`
  ratchets `src/`); a finding/decision id only as a short pointer. A test docstring is one
  paragraph; a CHANGELOG entry is ≤ 40 lines; a new contract gets ONE line here (≤ 300 lines)
- Tests accompany every module in the same commit; `ScriptedEndpoint` (tests/conftest.py) is the
  main engine harness; network tests hide behind `RSCHED_LIVE_TESTS=1`
- The seed trees are excluded from ruff; `util-seed/gu` is gated by `tests/test_dispatcher_seed.py`
- **No engine or daemon path reaches a person by itself** — a message is an explicit util call by
  the run, behind a `messaging-*` permission (docs/notifications.md)

## Versioning

`src/rsched/__init__.py` `__version__` is the single source — bump the minor on every user-facing
revision, with a matching `## [x.y.z]` CHANGELOG.md header in the same commit (`tests/test_policy.py`).
`/api/status` serves the version, the commit and `build_skew` (the console's reload banner).

## Deploy

Docker (`docker compose up -d`, every compose command bare from the checkout root — `.env` holds the
file set and profiles). Every data home is a bind mount, written as
`RSCHED_UID`; `deploy/state-paths.sh` is the one inventory `bundle.sh` and `backup.sh` read.
**`docker compose up -d` never reloads code**: drop the restart sentinel
`~/routines/.control/restart.request` (the daemon exits at the next quiet gap, never killing a
run), or bracket an external restart with `rsched drain` … `rsched postdeploy`. Verify every bind
source exists first — Docker gives up after one failed start. Config:
`~/.config/routine-scheduler/config.yaml`; web UI on `:8321` with operator + `routine_token`
bearer auth (`web/app.ROUTINE_TOKEN_DENIED_READS`). Long form: `docs/contracts.md` § Deploy and
`deploy/DOCKER.md`.
