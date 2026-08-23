# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# routine-scheduler — working conventions

LLM agent routine scheduler. A **routine** = instruction + workflow + schedule, living in its own
git repo under `~/routines/<slug>`. Runs execute on a provider-agnostic engine where *the workflow
is the harness* — the orchestrator LLM follows the workflow document and acts only through one JSON
action per turn. **A second AGENT LOOP in the path is banned**: it fights this harness and hides the
conversation. Endpoints are model TRANSPORTS only (docs/architecture.md). Routines have **no shell** — the
only way to run code is a global util (a reserved `shell` util exists behind the `shell`
permission), and every util subprocess runs inside a Landlock sandbox scoped to the run's
permissions (docs/sandboxing.md). The instruction contains only the task; cross-cutting conduct is
a set of GENERAL RULES with ONE library copy each (`rules:` in routine.yaml holds slugs — the run
reads the prose with `read_rule` and applies the principle to its own case); schedule, PERMISSIONS,
workdir, budgets, and model roles are routine config (`routine.yaml` / UI).

## Where the detail lives

This file is the working tier: what to run, what must not be repurposed, and the gotchas
that are not discoverable from the code. Subsystem narration lives in `docs/` — read the
one you are about to touch, not all of them.

- `docs/architecture.md` — the full subsystem reference (engine loop, endpoints, routines
  on disk, child tasks, conversations, libraries & seeds, daemon ownership, OAuth, machines)
- `docs/prompt-anatomy.md` — every string the orchestrator sees, and why. Revise it with ANY
  change to composer / loop / actions / schema_guard wording; `tests/test_prompt_anatomy.py`
  fails on drift
- `docs/rules-permissions.md`, `docs/curated-rules.md` — the general-rules layer, the two-layer
  permission set, the ACCESS-REQUEST grant model (entities.py ids; allow/deny × now/forever, plus
  allow-once for turn-action classes), and each curated rule's provenance
- `docs/subtasks.md`, `docs/background-tasks.md`, `docs/triggers.md`, `docs/schedule-once.md`
  — the child-task and firing mechanisms
- `docs/conversations.md`, `docs/playbooks.md` — interactive sessions and reusable briefs. A
  conversation's spine is EMERGENT: it writes its own `state/plan.md` (inlined at the top of every
  reply by `state_digest`) where a routine gets `stages/` + `phase.json` compiled at creation —
  don't "fix" a conversation by giving it a compiled workflow
- `docs/items.md` — the maintenance-item index (findings, decisions, bug reports): the
  item shape, the status vocabulary and its precedence, and the changelog join;
  `docs/messages.md` — a routine's four message folders (the Messages page/D74), the
  per-folder write surface, and the outbox-retraction decision
- `docs/sandboxing.md`, `docs/endpoints.md`, `docs/oauth-connections.md`,
  `docs/remote-machines.md`, `docs/darknet.md`, `docs/usenet.md`, `docs/notifications.md` — the outward-facing
  surfaces
- `docs/search.md`, `docs/run-analytics.md`, `docs/authoring.md`, `docs/examples.md`,
  `docs/getting-started.md` — read models, authoring, onboarding
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
- `uv run ruff check` + `uv run mypy` — the strict quality gates (ruff runs `select = ALL`;
  every ignore in pyproject.toml carries its house-style reason). Both MUST be green in every
  commit; `uv run pre-commit install` wires them into git.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=catalog-name` overrides a model role (catalog names, not endpoint:model pairs); `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>]`
  — see `rsched --help`. `validate` checks every routine's `routine.yaml` AND lints its recipe
  prose (`main.md`, `stages/`); `lint` covers the library. `engine-run` is internal
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
  docstring header declares (the util model — `rsched/scripts.py`), and NO util or model access
  inside (`gu` off PATH; a step needing a util's capability belongs in the recipe). Gated by the
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
  `subtask` runs a child sub-workflow SEQUENTIALLY and blocks (the parallel `spawn`'s
  sibling — one child-task executor, `engine/childrun.py`); a `subtask` with `workflow: "generate"`
  drafts a new pattern when the `workflows: generate` capability is held (see docs/subtasks.md).
  **`report` is the ONE channel for work that is not the run's own task** — ungated, held by
  every routine. What varies is whether the run can name an owner. UNADDRESSED goes to the
  triage stream self-audit reads; ADDRESSED (`target`) is ALSO delivered into that routine's
  `inbox/`, which its NEXT SCHEDULED RUN drains — it starts no run and wakes nobody. The target
  closes it by reporting back with `answers: "<R id>"`, adding `closes: true` when the reply ends
  the exchange — a closure is born settled; without it the reply is itself a new open report.
  One `R<n>` namespace, one append-only
  ledger `.control/reports.jsonl` (order rows + `delivered` event rows), one Items type; the
  page shows open → in_progress once drained → settled once answered. Triage is therefore
  FORWARDING, not absorbing.
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
  run writes routine.yaml), the TEXT is the library's. There is deliberately **no remove_rule** —
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
  compaction, error, finish`. This vocabulary is consumed by the web renderer AND the meta routine.

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
- **A run never writes its own config.** `routine.yaml` is never writable by any run — the
  block is by FILENAME anywhere a run can write, external repos included.

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
  are green in every commit — on the FULL repo, not just changed files; pre-commit enforces
  both. New ignores need the same one-line justification the existing ones carry. The two
  seed trees are excluded (`extend-exclude`): `library-seed/workflows` are never-executed
  ast-parsed pattern files gated by `workflows/lint.py`, `util-seed` are PEP 723 scripts
  gated by `utils_lib.header_problems` + their `--selftest`.
- ONE outbound notification seam: any engine/daemon-implicit "reach the user" send goes through
  `rsched/notify.py` (see docs/notifications.md); new channels become a permission + a notify
  transport, never an inline util call. Discord is opt-in per routine via `communication`
  (instance-level events like an OAuth reauth ping only fire when a BINDING routine holds it).
  Browser push (`web/push.py`) is the WEB channel's delivery arm — it renders the same
  open-decisions record, so it rides beside the seam, not through it.

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
the container layer on recreate. **`docker compose up -d` NEVER reloads code**: the source is
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
