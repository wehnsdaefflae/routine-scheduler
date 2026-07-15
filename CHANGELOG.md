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

