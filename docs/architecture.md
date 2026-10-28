# Architecture reference

How routine-scheduler is put together, subsystem by subsystem. This is the DETAIL tier:
`CLAUDE.md` carries the working conventions, the commands and the core contracts an agent
needs before touching anything; everything below is here to be looked up when the work
actually reaches a subsystem.

Deeper single-topic guides live beside this one: getting started, examples, child runs,
background tasks, triggers, schedule-once, run gates, lanes & tags, conversations,
playbooks, rules & permissions, curated rules, rule assists, reminders, items, messages,
status pages, sandboxing, admin, OAuth connections, remote machines, browser sessions,
darknet, usenet, notifications, search, run analytics, output compression, revise recipe,
the Claude-proxy cutover, prompt anatomy, endpoints, authoring — and `designs.md`, which is
the only one that describes work NOT built.

## How a run works (engine/)

The turn loop (`engine/loop.py`) is the heart — `EngineLoop.run` spells the turn order out in
one place, each step's body a method named for it; `engine/runtime.py` is the entry above it
(`run_routine`, workflow loading/decomposition), `engine/boot.py` the initial message list
(kickoff or resume rehydration — the once-only guards included, in the scope
`engine/guardscope.py` defines), `engine/completion.py` the get-one-valid-action side (schema
retries, model failover incl. classifier refusals, refusal clarification, media fallback, the
compaction gate), with `engine/wrongturn.py` the MEASUREMENT beside it — a turn that comes back
well-formed and WRONG files `wrong_turn_detected` (every net in `engine/degrade.py` answers a
turn that failed to ARRIVE; a wrong one is, to that ladder, a success), changing no effort, no
model and no retry: R2652's first increment is the trigger alone, so the rate can justify the
ladder rather than the other way round. `engine/schemamemo.py` is what that rate then bought
(F736): it remembers the rejection MESSAGES this run has already earned, so a repeat earns a
correction naming the field in the contract's own words plus the turn that first earned it, and
that retry sheds the provider grammar instead of re-sending the identical ask — within the same
three attempts, and never by ending the run, which the measured specimens show would cost more
than the retries it saves. `engine/denialmemo.py` is its sibling for the other half (F762): a
CAPABILITY refusal is deterministic and permanent for the whole run — only the user switches a
capability on — yet the refusal is corrected inside the retry cycle and dropped as debris with
it, so the next turn's prompt held no trace and `vodafone-outage-claims:20261023-024513`
re-emitted a write to its own `main.md` at turns 17, 18, 19 and 34. The first such refusal now
posts one ENGINE NOTE (permanence + the gate's text), and a later identical attempt is answered
as a REPEAT naming the turn it was first refused at. The verdict is re-asked of the LIVE policy
every time and never replayed from the memo, so an `allow once` folded in mid-run makes the next
attempt legal; a refusal that could legitimately succeed later (a schema rejection, a file not
written yet) is not this module's business at all. `engine/control.py` is the
between-turns control plane (abort, pause gate, injection drain, subrun announcements) and
`engine/switches.py` its `control.json` switches (model, deliberation, rule bindings, a live
config change), `engine/loopend.py` how a run ENDS (the engine-authored verdicts and the one
close-out every ending shares — result.md, autocommit, health event, final status) with
`engine/finishstands.py` the one line between the loop and `engine/finishgate.py`'s guards —
a gate that RAISES files `finish_gate_failed` and the run finishes on its own authored summary,
because that summary is the only part of a run that survives it (F658), `engine/obs_floor.py`
the renderer's two floors — an error result every action kind words, and a renderer that RAISES
files `observation_render_failed` while the run reads the raw result instead of ending (a resume
re-renders every stored observation, so a raise there was a run no resume could recover), and
`engine/interact.py` the ASK protocol (`ask_user` and the grant-gated approvals, and the ask-back
that can answer any of them) with `engine/config_bridge.py` the filing check of the
`config_patch` an ask may carry, `engine/askbatch.py` the fan-out that files SEVERAL questions
from one action through that same seam, `engine/askclass.py` the rule deciding each record's
class, `engine/askfields.py` the action's grammar and `engine/askprose.py` the double-escaped
newline repaired at intake,
`engine/admin_handlers.py` the two handlers that answer about the instance rather than to the
user (`schedule_run`, `report`) beside their renderer `obs_admin`,
`engine/donothing.py` the `do_nothing` action (D186) — a run's first-class statement that
nothing was due and why, whose `grounds_a_finish` predicate is also the one both the live loop
and `boot`'s resume rebuild ask before counting an observation towards the fabrication guard, and `engine/overflow.py` the
provider-overflow vocabulary and the two request-fault nets (`_shrink_window_to_provider`,
`_recover_oversize_prompt`) that sit under the compaction gate in `engine/window.py`. Each turn:
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
  writes its own recipe — main.md / stages/ / **tuning.yaml** — unlocked only by the
  **`write_recipe`** capability (the `recipe-authoring` permission; the model emits an ordinary
  write_file / edit_file and the engine decides from the PATH, `grantpolicy.is_recipe_path`) OR a
  per-leg **revise marker** (`engine/revise.py`: the run-view "revise recipe" mode drops one, the
  loop reads it ONCE and grants recipe self-write + the file-edit kinds for that leg only). A
  filesystem write root over the routine's own dir does NOT unlock it: that was the pre-0.261.0
  shape, where granting a routine write access to its working directory silently handed it the
  right to rewrite its own instructions.
  `routine.yaml` is NEVER writable by any run, even under an fs_write_root or a revise — config is the user's,
  no exceptions (the block is by FILENAME anywhere the run can write, external repos included — a
  deliberate over-block: any file named routine.yaml is treated as config); the machine-tunable knobs live in tuning.yaml, where the FILE boundary is the
  permission boundary; `rundepth.read_gate` backstops absolute paths and scopes `runs: last`,
  and since D207 the SAME depth is compiled into the Landlock jail — `rundepth` is the one
  predicate, `ownseal.mount_own_dir` mounts only the permitted run dirs, so a util handed an
  older run's absolute path is refused by the kernel and not merely by the action layer).
  A disallowed/switched-off call is corrected inside the schema-retry cycle with an error naming the
  covering permission, and never becomes a turn.
- **The system prompt is composed once at boot** (`engine/composer.py`; the CAPABILITIES
  section in `engine/capabilities.py`, observation rendering in `engine/observations.py`):
  harness contract → action schema
  + example → workflow body (the routine's own `main.md`, with its `## Done when` — the general rules it
  holds are named in the state digest, their prose NEVER inlined; a SUBRUN inserts its INSTRUCTION brief
  here, a top-level routine does NOT — its task is baked into the recipe) → **capabilities** (model +
  context window, the action kinds usable this run, enabled capabilities + held permissions' short
  conduct notes,
  spawnable workflow patterns, the util catalog at name+summary altitude — ONE util's usage on demand via
  `util name=list args=["<name>"]`) → **state digest** (phase, `state/` and `artifacts/` — each capped at
  `composer.DIR_LIST_MAX` entries, newest first — stage modules, held rules, last result,
  LEDGER tail, open/answered questions, inbox messages). Effect actions (`util`/`read_file`/`write_file`/
  `edit_file`/`llm`) run through `engine/executor.py`; a `write_file`/`edit_file` may carry
  `then_script` — one of the routine's scripts, run the moment the change lands, both results in
  ONE observation (`engine/thenscript.py`, SoL-Pi's Action Fusion: 1,089 of 3,459 writes in 160
  fleet runs were followed straight by a run of the file just changed, each a whole turn). The
  riding script is validated, HELD and secret-gated as the `script` action it would be alone, is
  skipped when the change did not land, and is shown in the schema only where `script` is a
  usable kind. Everything else is reachable on demand (read_file
  stages/history, read_rule, util name=list, memory_read).

  **What that costs, measured 2026-09-11 across all 33 live routines:** the composed prefix averages
  **27.7k tokens**, re-read on every turn — CAPABILITIES 32% (util catalog 4.0k, permission notes 3.1k),
  state digest 21%, harness 21%, action schema 16%, recipe 10% — which is roughly a quarter of the
  instance's weighted spend. It is nearly all load-bearing: **61.7% of util calls** since 2026-09-01 were
  made COLD, straight from the catalog with no `list`/`search` first, reaching 63 of the library's 134
  utils, while `search` was used 10 times against 370 `list` calls. So the catalog is not a convenience
  over a discovery mechanism — it IS the discovery mechanism, and `list` serves the exact-usage half it
  was designed for. Re-run the decomposition with `~/.config/routine-scheduler/prefix-probe.py` rather
  than re-deriving it.
- **The message list is a prompt-caching contract**: composed once, appended-to only, never mutated —
  so providers serve each turn's prefix from cache (~0.1x). Per-turn boilerplate is banned: the util
  reminder is ONE-SHOT on the kickoff/resume note, the history pointer re-appears only every 10th turn,
  and a turn's retry debris — schema-retry pairs, a refused finish and its re-drive note, a failover
  notice — is dropped from the live prompt once the turn's action is accepted (the transcript keeps
  the error events). The debris is remembered by identity, not as the tail past a length
  (`engine/turndebris.py`): a model switch mid-turn re-fits the prompt to the new window, and a
  compaction there moves it. Cache traffic reports as usage `cached_in`/`cache_write` (kept OUT of `in`, so
  token budgets keep their meaning). A provider's cache is earned by a BYTE-STABLE prefix and by
  nothing else — there is no per-run key an adapter is handed. FOUR sanctioned exceptions
  rewrite the list in place:
  compaction (below — the window clamp included), schema-retry debris cleanup, the media fallback
  (a failed image turn's tail message is rewritten text-only), and a rule unbind the user asked to
  ERASE (`switches.apply_rule_drop`: the messages carrying the rule's text become a tombstone, opt-in
  because the cache breaks from the first edited message) — each invalidates the provider cache
  once, by design.
- **Compaction archives context to a navigable on-disk history** (`compaction.archive_middle`): when
  the prompt exceeds ~60% of the resolved model's `context_tokens` — ~80% once cache hits are observed
  (compaction rewrites the prefix and invalidates the cache, so carried context is cheaper than
  re-archiving) — the middle turns are elided by the deterministic one-line digest
  (`compaction.maybe_compact`) and that same middle is reorganized into markdown files (~≤100 lines
  each) under `runs/<ts>/history/` + `INDEX.md`. ONE fit test (`compaction.archival_fits`, which reserves the
  archival prompt, the history schema and the model's own output) decides every candidate in
  order — the configured compaction model, then the routine's `tool_call` model (machine work
  belongs on the cheaper tier), then the main model — and when none fits the archival is SKIPPED
  with `archival: "skipped"` plus `archival_skipped` on the compaction event rather than posted
  to a model that will 400. Its spend is folded into the run's usage. The digest is what the prompt carries meanwhile, and what it keeps if the archival
  fails. The on-disk transcript keeps everything regardless.
- **The archive is built OFF the hot path** (`engine/archival.py`). The archival call takes
  180-600s and it used to take them from the run, mid-work. The two tiers are now split in
  TIME rather than traded off: the deterministic digest lands instantly and the run carries
  straight on, the archival runs in a daemon thread against the middle that was just elided,
  and when it finishes the run is told — by an APPENDED note, never a swap, because rewriting
  the digest would be a second rewrite of the prefix and a second cache invalidation for one
  compaction. **The guardrail this is built around**: the mainstream instant compaction is
  instant BECAUSE it is summarize-and-replace, which is lossy. Nothing here summarizes anything
  away — the transcript keeps every byte, the archive is still built losslessly from the real
  middle, and the digest is a PLACEHOLDER in the prompt for the minute the archive takes. What
  changed is when the run gets to keep working, not what it ends up with. A run that FINISHES
  inside the archival window settles it (`archival.settle`, a few seconds — the run's work is
  over, so nothing is waiting on the model, but a conversation's reply is rendered from the
  finish so it stays short); one that would need longer is abandoned with the reason recorded,
  which is exactly the degraded fallback the synchronous path already had.
- **The archive INDEX is engine-owned** (`compaction._build_index`). The model supplies each
  file's CONTENT and a one-line `about`; the engine supplies the filenames (`t<turn>-<slug>.md`)
  and therefore writes `INDEX.md` — the same split that has always governed `.memory/INDEX.md`,
  and for the same reason. It used to hand back a free-text index it had written against its OWN
  names, which `_swap_in_history` then renamed: the index was a map to files that did not exist.
  Measured on the live instance before the fix — one archive cited 102 filenames of which ZERO
  resolved, 36% of all history reads returned ENOENT, and runs paid a stereotyped three-turn
  recovery (read INDEX, read the bare names and fail, read the prefixed names) over and over.
  Two archives had an `INDEX.md` whose entire content was the string `INDEX.md`. The invariant
  now is that every file in the archive has exactly one index line under the name it was written
  with; entries from earlier passes are carried forward by the ENGINE (the model used to be
  asked to keep them and silently dropped them — one archive listed 13 of its 23 files) and the
  prior index is no longer re-fed to the model, which had grown the archival prompt to 20 KB by
  a run's 23rd pass.
- **One turn to externalize before the middle goes** (`window._warn_before_eviction`). Retention
  is POSITIONAL — head 6, tail 24 — so a load-bearing fact in the middle survives only in
  `history/`, reachable if the run remembers to look. `note`, `memory_write` and the LEDGER
  already exist to carry a fact out of the conversation; what was missing was the moment to use
  them. The archive is deferred by exactly one turn and the run is told why — and the deferred
  pass is OWED (`loop._evict_owed`): the next turn takes it under the cap that decided it, so
  "the archive happens on your next turn" holds even when the cap has moved since. It did not
  before: a stage boundary's decision lasts one turn, so re-testing the ordinary gate spent the
  once-per-run warning on a pass that never came, and the compaction that did come later came
  unannounced. The owed pass carries the boundary's stage and economics with it too. Safe by
  construction: when the FRACTION binds the gate there is 20-40% of the window before the hard
  ceiling, a whole turn of slack; when the CEILING binds there is none, so the warning is
  skipped and the archive happens now. `clamp_to_cap` runs unconditionally afterwards either
  way, so a deferred turn cannot 400. Once per run — per reply, in a conversation — and a
  resumed leg, whose replayed window is evicted again, reads the warning back from its
  `evict_warning` mark rather than giving it twice (`engine/guardscope.py`).
- **History recall** (`engine/recall.py`): the archive is one more store the relevance-trigger
  layer surfaces from. When what the run just did overlaps an archived topic, the observation
  tail names ONE file and the turn it was archived at — a pointer, never a fetch, and free
  (it rides a message the run was getting anyway). It is not a rule ASSIST because an assist's
  payload is a static line and this one is computed. Measured over four real archived runs:
  at least one useful file in the top 3 for 23% of the moments a run actually went looking,
  37% in the top 5 — but split by archive size that average hides the result, since archives up
  to ~23 files hit the top 3 for 7 of 8 moments while a 101-file archive managed 1 of 22.
  Deterministic overlap works while the archive is small and degrades as it grows, so it
  surfaces one file above a floor score rather than a list, and the semantic path (embeddings)
  waits behind evidence that the cheap one is insufficient.
- **The gate is sized in CALIBRATED tokens.** `estimate_input_tokens` is 3.5 UTF-8 bytes per
  token — a packing guess, not a tokenizer — while every gate compares it against a real window.
  So `window.note_prompt_size` divides what the provider just REPORTED by what was estimated,
  subtracts the action schema the provider counted and the message list does not carry, ignores
  any turn whose estimate is under a fifth of the window (the framing overhead is roughly fixed,
  so the ratio explodes on a short prompt), and clamps to 2x. `window._calibrated_window` then
  restates the window in those units and `compact_if_needed` passes it to every gate below
  through that one line. The ratio is run-local rather than per-model: what it mostly measures is
  how this CONTENT packs. Before it the estimate was corrected only BY a provider's oversize
  400 — the overflow nets in `engine/overflow.py`, which remain the backstop.
- **`maybe_compact` takes the cap as an ARGUMENT** (`compaction.maybe_compact(messages,
  turn_records, cap_tokens)`): the caller's decision is the only one. A second gate inside it
  re-testing its own fraction meant every pass triggered below that fraction spent the
  eviction-warning turn and then archived nothing.
- **Stage-boundary compaction** (`engine/boundary.py`): the gate above is a SIZE check, and on a
  1M-token window it almost never trips — 14 passes in 160 fleet runs (2026-09-17..10-08), while
  the median request carried 148k tokens and re-read all of it every turn at the cache price. So
  at a STAGE BOUNDARY — the run reaching a stage it had not reached, by reading its
  `stages/<name>.md` (`ctx.phase` moving) OR by recording it in `state/phase.json` (the visited
  set growing; a run that routes by its cursor alone never moves `ctx.phase`, F563) — the gate
  asks whether archiving the middle NOW costs less than carrying it to the end of the run. That is
  NVIDIA SoL-Pi's Online Context Compact test, priced in uncached-input-token units at
  Anthropic's list ratios: the per-turn saving (`before − after`, at the cache-read price once
  the provider has shown cache hits) against the one-time cost (the kept prefix written once
  more, plus the archival call reading the middle and writing ~5% of it back). The HORIZON is
  turns per stage reached so far × stages still ahead, capped by the turn budget; the pass is
  taken when the run has `boundary.MARGIN` (2) times the breakeven ahead of it — replayed over
  525 real boundaries, 2 kept 98% of the first passes' net saving with a third fewer passes. A
  boundary pass is taken WHATEVER the size gate says (its cap becomes 0), so unlike the 0.85
  discount it replaced it does cause passes the size gate would never have made; that is the
  point. Every anti-thrash guard still applies (the incompressible head+tail floor, a middle
  under 8 messages, less than 5,000 estimated tokens of growth since the last pass), the
  first look a run or a resumed leg takes is never a boundary, and a boundary that finds the
  previous pass's archive still in flight lets its pass go — one archive runs at a time and a
  second is dropped, so the pass would elide a middle that never reaches `history/`. The pass is stamped
  `anticipated: <stage>` and `economics: {saving_tokens, cost, per_turn, breakeven_turns,
  horizon_turns, compact}` in its `compaction` transcript event, so a boundary pass is
  distinguishable from a forced one and the constants can be tuned from what the test did.
- **Phase is derived, never bookkept**: the stage modules ARE the phases — `statemap.py` builds the
  UI's phase-graph diagram from the routine's own `stages/*.md`,
  in the order main.md STATES — its numbered `## Run flow` list or its arrow chain
  (`statemap.declared_order`; a stage named in neither follows by first mention, so every routine
  still has a diagram) — and
  the engine tracks the run's live position from its stage-module READS: a `read_file` of
  `stages/<name>.md` stamps `ctx.phase` (executor) → status.json (every turn) → the run tail's `state`
  event, which also fires on phase change (`/phasegraph` endpoints,
  `static/components/phasegraph.js` — rendered in the run view's rail and the conversation artifact
  rail, current phase highlighted live). Each node is a LINK into the transcript at the turn the
  phase begins (`phase_stats.first_turn`), as each sub-run row links to the parent turn that
  started the child (`subruntree.start_turn`) — one mechanism, `components/turnlink.js`: a turn box
  carries `data-turn`, the address is `#/run/<id>?turn=N`, and landing opens the folds on the way
  and flashes the box (`static/landing.js`). The transcript renderer chapters the say stream with
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
- **Consequence reminders hold an action BEFORE it runs** (`rsched/reminders.py` +
  `engine/remind.py`, gated by the `reminders` capability). A run leaves itself a
  `(regex → consequence)` reminder on the same turn it notices an action had an unintended effect
  (`remind`, a no-turn side field like `note`); thereafter an action whose canonical string
  (`actionschema.canon`) matches is HELD — not executed — and the caution is put in front of the
  model, which decides again. Two stores by blast radius: the routine's own `state/reminders.json`
  (autonomous) and the library's `reminders/` (approval-gated; the union is deduped by regex with
  local winning). Every fire is tallied and labelled four ways (`remind_feedback`:
  could_not / would_have / did / didnt), which is what makes a pattern tunable and the layer
  measurable. One hold per action string per run (per reply, in a conversation; kept across a
  resume — see below), so re-emitting the held action is the confirmation to proceed — the
  same anti-livelock shape the claim verifier uses. A curated
  reminder declares its reach (`universal`, or `listed` for the routines whose settings list it);
  rules-review curates the store from a census of every routine's own. Full narration in
  [reminders](reminders.md).
- **A curated rule surfaces itself at the moment it applies** (`rsched/assists.py` +
  `engine/assist.py`, `engine/assist_predicates.py`). A rule declares `assists:` in its own
  frontmatter — a deterministic predicate over the SITUATION plus the operative line to surface
  when it fires. FIVE moments (`assists.MOMENTS`): a pre-action HOLD, an observation tail, a
  turn-boundary ENGINE NOTE, a child-exit ENGINE NOTE on a child run's hand-back (those three
  free), and a finish-gate deferral (one turn, because a line surfaced as a run ends is one
  nobody can act on). The moment and the payload are coupled
  (`assists.MOMENT_PAYLOADS`): `pre-action` can only hold — the only way to reach a chosen action
  is to stop it — and the free moments cannot hold, having no action in hand. A `pre-action` hold
  goes through the same seam the consequence reminders use (`engine/hold.py`). The `boundary`
  moment can also PERFORM an action (`engine/assist_action.py`, the `action` payload): the rule
  does the thing its moment calls for, through `hooks/perform.py`, at no turn cost — bounded to
  the firing routine's own capabilities by the model's own gates, barred from the kinds that
  STEER a run (`assists.STEERING_KINDS`), and barred from both moments that change control flow,
  because a hook performs and reports and only a hold can stop an action. The curated half of the same relevance-trigger layer the consequence reminders are
  the self-authored half of. Compliance-CHECKING most rules is impossible — a compliant and a
  violating run leave byte-identical traces differing only in reasoning — but relevance is a
  property of the situation, which IS in the trace: the rule you cannot check, you can time.
  One fire per assist per run (per reply, in a conversation); a predicate that raises is inert.
  Full narration in [rule assists](rule-assists.md).
- **Every moment a hook can fire at is named in ONE table** (`rsched/hooks/points.py`, evaluated
  by `rsched/hooks/dispatch.py`). The operator asked on 2026-10-26 whether hooks are fully
  implemented, naming "a pre-action hook, post action hook, pre finalize hook, and many more".
  Three of those already fired — each at a hard-coded call site, with nothing naming the set, so
  the question could only be answered by reading the turn loop. `POINTS` is that set: per point
  what it can reach, which payloads it may carry (`remind` / `note` / `hold` / `action`), its CARRIER (an
  observation tail, an engine note, a held turn, a prompt section), which mechanisms fire there
  today and the engine seam that does it. The rule layer's five moments and the reminder layer's
  eight trigger kinds both MAP onto it (`for_moment`, `for_trigger`, pinned against
  `assists.MOMENT_PAYLOADS` and `reminders.KINDS` in `tests/test_hooks_points.py`), so the
  registry is one vocabulary rather than a second opinion beside them. The points nothing fires
  at yet are IN the table with an empty `fires` — `spawn`, `run-end`, `restart` — because a
  registry listing only what is built cannot say what is missing. `child-exit` was the fourth
  and now fires: the rule layer reaches it where a child's hand-back is delivered, which is the
  first of those gaps to be closed by a mechanism rather than renamed. `dispatch.at` is the
  one evaluator: it drops a candidate whose payload the point cannot carry, and treats a
  predicate that raises as "did not fire", never as a failed turn. TWO points can change what
  runs, which corrects the hook design's rule 2: `pre-action` prevents an action and
  `pre-finalize` defers a finish (the rung built after that rule was written).
- **A hook can PERFORM an action, not only talk** (`rsched/hooks/perform.py`), which is the
  Claude-Code half of the same ask. The operator ruled on 2026-10-26: *"Any action. This includes
  utils and therefore scripts."* He named the ACTION VOCABULARY, so the payload is built on the
  seam that already runs an authored action with no model turn — `control.run_user_command`'s
  path, which slash commands have used in production: schema validate → `validate_action` against
  the run's workflow tools ∩ capabilities → `actionroute.dispatch_action`. Two consequences fall
  out for free. A hook can never do what the FIRING ROUTINE may not, because `validate_action` is
  the model's own gate; and it must never reach `executor.dispatch` directly, because that path
  skips D39's secret-exposure gate (the bug that once let a DECLINED secret reach a util a slash
  command ran — `tests/test_hooks_perform.py` asserts the import and call graph, not just the
  behaviour). A performed action's observation is flagged `hook_action` with its point and its
  author, and increments `executed_actions`, so it grounds a later finish exactly as a command
  does. `ACTION_POINTS` is DERIVED: a point carries `action` iff it is not in `HOLDING_POINTS` —
  and that line is his SECOND ruling of the same day, not a conservative first cut: *"No veto
  ever — a hook performs and reports; only a hold (prose to the model) can stop an action."* A
  performed action therefore never changes control flow; `perform` returns an observation, never
  a verdict, and a failing action does not refuse the action the run was about to take. Stopping
  an action remains a `hold`, which TELLS the model and spends a turn on its decision — so
  `HOLDING_POINTS`' pin now records a decision rather than an open question. **Its production
  caller is the rule layer's `action` payload** (`engine/assist_action.py`): a curated rule with
  that payload performs its action at `turn-boundary` and the run reads what came of it beside
  the rule's own line. Until that caller existed the payload was honest in the registry and
  exercised only by its tests, which is the state its own docstring recorded and vulture (at
  min-confidence 60) was the pressure against.
- **A run resumes where it left off** (`run_routine(resume_from=…)`, `EngineLoop(resume=True)`): the
  transcript is replayed into the message list (`history.replay_messages`) with a fresh budget window
  (`budget_base_turn`); usage REPORTING stays cumulative across legs (`history.prior_usage` →
  `ctx.usage_base`; budgets ignore it). status.json's `budgets` block carries that base turn,
  the window's own count and both ceilings beside `turns_left`, and the runs table renders
  `348 (window 179/200, resumed at 169)` — without them a resumed run showed a turn count past
  its own `max_turns` beside a large "turns left" and read as a failed ceiling (F564/D155). The **model can be switched mid-run** — a `control.json`
  `switch_model` signal applied at the turn boundary (`for_model` re-resolves every turn).
- **"Once" survives a resume** (`engine/guardscope.py`). The once-only guards — the hold
  ledger, each rule assist's single fire and the one assist finish deferral, the repos found
  clean, the verifier's one challenge per line, a reminder hold's one label and a re-driven
  finish's one payload, the eviction warning — are in-memory ledgers, and boot REBUILDS each
  from the transcript events inside its GUARD SCOPE, the way `history.seen_paths` rebuilds the
  grounding set; each layer reads its own (`rebuild`, the counterpart of `configure`). The
  scope is one helper (operator decision, 2026-10-01): a routine's whole run, every leg of it;
  a conversation's current REPLY — the leg after an authored finish opens a new reply and starts
  them fresh, any other leg continues the reply that began after the last one. Where an event
  did not record its decision it now does, as a payload extension, never a new event type:
  `assists` on every event that carried a rule assist's line (the observation-moment assists
  are asked before their observation is recorded), `undo_point` on the write a clean repo let
  through, `evict_warning` on the warning's ENGINE NOTE.

## Endpoints (endpoints/) — transports, not agents

Chat-completion adapters implementing one `ChatEndpoint.complete(...)` (`base.py` — tenacity retries on
retryable `EndpointError`s; a 200 with an unparseable body is one of them). Both honor
`ModelRef.effort` and report prompt-cache traffic as usage `cached_in`/`cache_write` (kept out of `in`).
The credential ladder (`resolve_api_key`: inline api_key → key_var in Secrets → key_env_file) has label-only mirrors BESIDE the resolvers
(`api_key_source`) feeding the Settings card's "credential in use" line — which rung is
live, plus a shadow warning when an inline key hides a set secret; key values never leave the server.
The WRITE side of the same ladder is `web/settings/endpoint_keys.py`: an endpoints save reads
the submitted `api_key` in three states (absent and `""` keep a stored inline key, since the
editor submits the field blank; an explicit `null` clears it) and refuses a save that would
leave the endpoint with no rung resolving at all — but only when that save is what changed the
credential, so a keyless local backend and an endpoint awaiting its key both still save.
Two kinds:
- **openai** — any OpenAI-compatible API (OpenRouter, vLLM, Ollama). Schema via json_schema / json_object
  / ollama-native; degrades gracefully (retries without `response_format`/`reasoning` on a 400, and without
  `response_format` on a 503 that hides a schema-incapable backend). Caching
  is the provider's implicit prefix caching; `cached_tokens` is surfaced from usage details.
- **anthropic** — Messages API, direct or through CLIProxyAPI for subscription authentication. Schema via a single tool
  on `auto` held to one call — a FORCED `tool_choice` makes the configured effort meaningless
  (thinking is allowed only on `auto`, and the proxy strips thinking and effort from a forced
  call; measured 2026-10-08), so forcing is an endpoint's `tool_choice: forced` only, for a route
  validated that way (the Codex models). An `auto` reply no action can be read from is re-asked
  once forced in the same call (`stop_details["forced_reask"]`), so the shape 0.372.0 met cannot
  cost a run. Past actions are SENT as calls of the action tool and their observations as the calls'
  results (`anthropic_messages.native_turns`, on `auto` only), so a reply ends at its one call;
  should a reply still carry actions as text beside its call — or several CALLS, which the
  one-call ask cannot prevent (F754: `parsed` the first, `parsed_rest` the others in wire order)
  — every one of them reaches the engine, which runs them in reply order
  (`engine/replyactions.py` for the order, `engine/chain.py` for the run). Message rendering — merging, media,
  calls, cache markers — is `anthropic_messages.py`; `anthropic_api.py` is the transport. Effort via `output_config`. Every
  optional field a model may refuse — the forced `tool_choice`, `output_config`, `temperature`,
  the `cache_control` markers — is degraded on a 400 that names it, one field per 400 until the
  request is accepted, and a reply no action can be read from names its content blocks in
  `stop_details["unread"]`. Sets `cache_control` breakpoints (tools +
  system static, a moving one on the last message) on CONVERSATION turns — ~0.1x reads on the whole
  prefix every turn. A ONE-SHOT
  call is not written: `cacheable` is derived from the task kind at the one seam every completion
  passes through (`instrument.CACHEABLE_KINDS` = {"turn"}), because a prefix that is never sent again
  would pay a 1.25x write for a read that never comes (measured: 0.3% read share on `llm_action`).
  It carries ONE marker, on the smallest prefix it has — the tool definition, else a 1,000-char
  leading slice of its first text (`anthropic_messages.claim_placement`), not
  none: the subscription proxy (CLIProxyAPI) adds its own breakpoints to any request carrying no
  marker, which had every archival call's whole input — 2.94M tokens across four — billed as cache
  writes.

Whether that caching is actually WORKING is a measured reading, not an assumption:
`endpoints.base.cache_read_share` divides reads by all cache traffic, the Stats tab carries it on
every slice, and a run finishing below half raises a `cache_read_degraded` health event. Reads on
their own cannot answer it — a transport that stopped resuming its session still serves the static
prefix from cache while re-writing the conversation every turn, which reads as healthy and bills
12.5x. See docs/run-analytics.md.


The **model catalog** (`config.ModelConfig`, `ServerConfig.models`) binds a provider model id to
an endpoint and owns the PER-MODEL attributes — `multimodal`, the `audio` / `video` modality
opt-ins (D192; plain booleans that inherit NO kind default, because no endpoint kind's models
all have ears), `context_tokens`,
`temperature`, `max_tokens` (each None inherits the endpoint kind default / the endpoint's own
value; `max_tokens` — the model's real OUTPUT limit, sent on every engine call — falls back to
`DEFAULT_MODEL_MAX_TOKENS` 16_384, and Settings flags unset/implausible values so "set correctly"
is auditable), plus `fallbacks:` — the ordered FAILOVER chain (catalog names, NOT transitive).
**Reasoning effort is NOT a catalog attribute**: it is a setting of its own, chosen where the
model is USED — `routine.yaml` `efforts: {role: level}` (a conversation's too), the server's
`system_effort` / `compaction_effort`, and a trial's `efforts:` — so one catalog entry serves
every level (the catalog once carried "Opus high", "Opus xhigh", … as one row per level, and
every routine wanting another level needed another row). The registry applies the role's level
to WHICHEVER model serves it, a failover rung included (`EndpointRegistry.for_model(kind,
models, efforts)`); a role with no model of its own runs the system model at the role's level,
else at `system_effort`. `config.base.EFFORT_LEVELS` is the one vocabulary
(`low|medium|high|xhigh|max`; the `openai` kind folds the top two into its own top level).
Endpoints hold only transport + auth + those DEFAULTS; `multimodal` is NOT on the endpoint (one
endpoint serves many models with different windows and vision support). Each **routine
references models BY NAME** (`routine.yaml` `models:` maps a role → catalog name): `main` (the
loop — and every spawned child's default), `tool_call` (the `llm` action), optional
`uncensored` — plus the two DECISION roles `decision` / `decision_media`, which name a decision
model (below) and fall back to the instance's decision defaults instead. A chat role left unset falls back to the server's single `system_model` (also a catalog
name) — the ONE model for pre-routine machine work (the clarify flow + workflow
generation/suggestion). A MODEL TRIAL (`trial:`, `rsched/trials.py`) replaces chat roles (and
their efforts) for the routine's next N fires without touching `models:` / `efforts:` — the
daemon names the trial's models and efforts to the engine per run, exactly as `run-once --model
… --effort …` does by hand. A single `llm`/`spawn`/`subtask` call may override its model per call
(`model:` a role — main|tool_call|uncensored — or a CATALOG model name; D81, extended by
the 2026-08-22 order retiring the subroutine role. The `list_models` action shows the
catalog; an unconfigured `uncensored` or an unknown name is rejected with a teaching note). `EndpointRegistry.resolve(name)` /
`.for_model(kind, routine.models, routine.efforts)` / `.for_system()` produce a RESOLVED `ModelRef`
(endpoint, model id, the role's effort + the filled-in multimodal/audio/video/context_tokens/temperature/max_tokens) — the runtime
handle, no longer parsed from yaml. `supports_media(mime, *, multimodal, audio, video)` and compaction
(`ref.context_tokens`) take the resolved model's values; `complete()` gains a `temperature` kwarg.

**What media may ride a message at all** is `endpoints/media_types.py` (D192): ONE allowlist
mapping mime → MODALITY (`image` / `pdf` / `audio` / `video`), the size and pixel bounds, and the
`supports_media_type(mime, *, multimodal, pdf, audio, video)` core every adapter's
`supports_media` is built from. Before it there were two constants (a set of image mimes and a
lone PDF mime), so an audio or video file was refused by `guess_media_type` before any adapter
or fallback was asked — unreachable however the catalog was set. A file is carried natively only
where the MODEL's bit and the WIRE both say yes: the anthropic wire takes images + PDFs and
neither time-based modality; the openai wire takes images and AUDIO (an `input_audio` content
part, `endpoints/openai_media.py`) and no video. A model not configured for audio/video gets no
vision-util fallback — that util describes pictures — so the engine refuses with the reason AND
`NATIVE_MEDIA_MAX_BYTES` (7 MiB raw, ~33% base64 inflation), which a video passes within seconds
of footage.
Editing a catalog model updates every routine that names it. **Failover** (`endpoints/failover.py`)
is two-level: a hard EndpointError anywhere (marked centrally in `InstrumentedEndpoint`) puts that
(endpoint, model id) in a 5-min process-local COOLDOWN, and every role resolution
(`for_model`/`for_uncensored`/`for_system`) picks the first not-cooling chain member — while the
engine's turn completion (`completion.py`) additionally advances down the chain MID-TURN on a hard
failure, logging the switch as a transcript `error` event with a `failover` payload (never a new
event type) and stamping each turn's `usage.model` with the model that actually served it, so
status.json (`ctx.main_model`) and spend attribution stay truthful. Chain exhausted → the run
fails exactly as before; models without `fallbacks` behave exactly as before.

**Decision models** are a separate catalog (`config/decisionconf.py`: `decision_endpoints`,
`decision_models`, the `decision_model` / `decision_media_model` defaults) with a separate
transport contract — evidence + typed questions in, probabilities out (`endpoints/decisions.py`,
one adapter per wire protocol: `decisions_openai.py` for OpenAI's Decisions API and the predator
server, `decisions_systemone.py` for TypeSafe's Jev, direct or through OpenRouter). No chat role
resolves into one and the `decide` action (`engine/decideaction.py`) reaches nothing else; an
instance with none configured never shows a run the kind. A call is recorded through
`instrument.observe_call`, so it shows in the activity dock like a completion, and its usage folds
into the run's budget. See docs/decision-models.md.

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
`ServerConfig.public_url` (the external https base a browser reaches this instance at) builds
the redirect_uri.
`daemon/oauth_refresh.py` (`OAuthRefreshManager`, started by each scheduler tick and run beside it,
one pass at a time, so a provider that is down never holds a fire) refreshes EXPIRING tokens near
expiry, persists rotation and flags `needs_reauth` on
rejection — which badges it in Settings → Connections, the only notification there is (0.230.0
deleted every implicit outbound send) — a no-op for non-expiring providers (Notion).
`daemon/endpoint_watch.py` (`EndpointWatch`, R2195) does the same for the MODEL credentials every
run depends on: every `CHECK_EVERY_S` (10 min, never the 5 s tick) it asks each CLIProxy-managed
endpoint for its account list — the LIGHTEST already-implemented probe, never a completion — and
writes a four-state verdict to `.control/endpoint-health.json` (`endpoints/endpoint_health.py`,
derived state like `endpoints/limits.py`), filing `endpoint_needs_reauth` once a day while a
credential is dead with no retry expected. Before it, nothing detected a dead credential until a
run spent its first turn: a claude-proxy token expiring on 2026-09-14 06:18 failed eight routines
at turn 0 inside two minutes. An endpoint with no proxy binding is skipped — the light probe for
an API-key endpoint is the credits read, still inline in `web/settings/endpoint_probe.py`.
**Engine injection**:
`executor.do_util` resolves the routine's bound connections to `{<PROVIDER>_ACCESS_TOKEN: token}`
and passes them to `utils_run.run_util` as `extra_secrets`; `_child_env` injects each ONLY if the
util declares the var — so a token reaches a util iff the routine binds the connection AND the util
declares the var (the `notion` util declares `NOTION_ACCESS_TOKEN`). See docs/oauth-connections.md.

## Secrets — two scopes (secrets.py)

`secrets.env` beside config.yaml is the CENTRAL store: instance-wide names, so exposing one
to a routine is a decision (the four-state `secret:<NAME>` grant). `secrets.d/<slug>.env` is
a routine's OWN store (D103, operator decision 2026-08-26 — R497): `SFTP_USER` means
something different to every routine that has one, and one flat namespace forced them either
to collide or to be spelled by convention. A scoped secret is owned by its routine —
implicitly exposed to its runs, invisible to every other routine, and SHADOWING a central
value of the same name (it rides `exec_env._extra_secrets`, which wins the `_child_env`
merge). The exposure gate therefore subtracts a routine's own names before deciding anything
(`secretgate._own_secrets`), and CAPABILITIES lists the two sets apart so a run never spends a
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
  `remote-machines` permission → `requires: utils: [remote]`, the reserved-util mechanism —
  which is what `shell` used until 0.287.0 promoted it to an action kind): `list` / `exec` (short blocking) / `submit`·`status`·`logs`·`cancel` (DETACHED jobs
  for long GPU work — a setsid process group, killable; `--notify-webhook <the routine's own
  trigger URL>` lets the job ping the routine on completion instead of polling) / `push`·`pull`
  (SFTP) / `scan-host` · `pubkey` (the public half of the key a run presents, to install on
  the box) · `test`. Host keys pinned; a mismatch refuses.
- **Engine injection** mirrors OAuth: `exec_env._machine_env(ctx)` (merged with `_connection_env`
  in `_extra_secrets`) passes the two vars to `run_util` as `extra_secrets`, under the SAME
  declared-var gate — a key reaches a util iff the routine binds the machine AND the util declares
  the var. Bound machines are NAMED in the prompt's CAPABILITIES section (`capabilities_digest`),
  so the model knows its hardware without a discovery turn.
- **Filesystem shares** (`MachineConfig.share`): compute crosses via `remote exec`, the FILESYSTEM
  via an sshfs mount. A bound machine whose catalog entry sets `share` gets that remote dir mounted
  at `<routine>/mnt/<name>/` for the run — so ordinary filesystem utils act on remote files with no
  transfer (`machine_mounts.mount_routine_shares` / `unmount_routine_shares`, hooked in a try/finally in
  `runtime.run_routine`). The **engine** mounts (unsandboxed, like OAuth consent) so the key never
  enters a util; key + pinned `known_hosts` go to a daemon-private `<config>/.mounts/` dir the
  sandbox keeps invisible. The routine dir is already a sandbox write root and a Landlock rule on it
  COVERS the sshfs sub-mount (verified empirically) — no sandbox change, no `remote-machines`
  permission needed (that gates the compute util, not the filesystem). `mnt/` is gitignored so the
  autocommit never slurps the remote FS; mounting is best-effort (unreachable/no-sshfs → warn +
  proceed) and a crashed run's stale mount is cleared before the next remount. A mount is PROVEN
  live before it is advertised (R514): sshfs daemonizes, so exit 0 is not a usable share, and the
  pre-created mountpoint dir otherwise reads exactly like an EMPTY share. `mount_routine_shares`
  polls `mount_is_live` (a real mount whose root reads; `ENOTCONN` counts as dead, not empty), and
  returns `(mounted, {name: reason})` — a share that never came up has its empty mountpoint dir
  removed and is reported to the run in CAPABILITIES as `SHARE NOT MOUNTED`, so an unmounted share
  can never be mistaken for an empty one. Docker: `sshfs` in
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
- `routine.yaml` — `description` (one-line UI summary, always present), schedule (cron + tz + catchup;
  a `tz` left out is the server's zone, `config.default_tz` — the zone the console edits in; a
  fixed time the clock repeats when daylight saving ends fires once, `rsched/firetimes.py`),
  `workflow: {library_slug, library_commit}` (provenance only), `models:` (role → catalog model NAME:
  main / tool_call / uncensored / supervisor — the last one the escalation ladder's overseer,
  falling back to `main` when unset, D164), `trial:` (a MODEL TRIAL — `{id, models, runs, reason}`: the
  next `runs` fires run on these catalog models instead, counted from the usage stream and
  stopping by itself; the field stays as history until the next accepted change — see
  docs/run-analytics.md "Model trials", `rsched/trials.py`), `connections:` (provider → account label — OAuth
  connection bindings, a resource like models; see OAuth connections above),
  `permissions:` (held CONDUCT docs) + `capabilities:` (the engine-enforced surface: `actions`,
  `utils`, the approval dials `confirm` / `rule_confirm` / `remind_confirm` / `config_confirm`
  (the last one `rsched/configdial.py`'s own ladder, `always | own_fields | never`) and the
  levels `runs` /
  `reminders` — both user-changeable only, side by side on the routine page with cascades between
  them; a child run picks its pattern from the catalog and never drafts one),
  `budgets:` (max_turns — ONE resume WINDOW, which a resume re-bases (`budget_base_turn`), so it
  is NOT a ceiling on a run / max_total_turns — the RUN-SPANNING cap over every window, which
  defaults to 3x max_turns (`engine/turnwindow`) rather than unlimited, an explicit value
  winning, `-1` included / wall_clock_min /
  total_tokens / max_cost (whole-$ ceiling) — the last four honor -1 = unlimited — / subruns /
  subrun_depth / ask_timeout_min — all editable in the UI, creation flow + routine page), `fs_read_roots` / `fs_write_roots`, retention —
  budgets/fs-roots/schedules are resources, never capabilities; `improve: false` opts the routine
  out of the routine-improver's passes (default: included); `triggers:` — event-driven fires
  alongside cron (docs/triggers.md): one canonical list of `{id, type, cooldown_s, …}` entries
  (`webhook` implemented — server-generated URL token IS the auth; `imap`/`watch_path` reserved
  in the same shape), validated in `rsched/triggers.py`, added, edited and removed in the routine
  page's Triggers section and saved by its one accept (never by a run; the library-sync
  routine's export REDACTS trigger tokens).
  `ladder:` — the ESCALATION LADDER's user half (`engine/ladder.py`, `engine/oversight.py`):
  `enabled` (default **false**, so no live routine is supervised until someone opts it in) and
  `rung_height` (default 3 — the ladder's HEIGHT IN RUNGS, i.e. the recursion limit; a SEMANTIC
  ceiling: by the third rung the distinct questions are exhausted, and it is additionally floored
  by `budgets.max_subrun_depth`). It is config and not
  tuning because authority over being watched is the user's, not a meta-routine's; every
  unreadable value degrades toward OFF, since `true` is the field's only dangerous value.
  **Where a rung is VISIBLE**: four transcript event types (`oversight_dispatch`,
  `oversight_directive`, `oversight_skipped`, `oversight_no_directive` — in `EVENT_TYPES`, so the
  console and `run-once` both render them), the directive's prose as a `user_injection` carrying
  `via: "oversight"` (which is what lets a reader tell a rung from the operator), the sub-run tree's
  `↑` for the `oversight` child mode, and the run rail's **oversight strip**
  (`readmodels/ladder.py` → the run detail's `ladder` field → `components/ladderstrip.js`),
  a section added only for a run that HAS a rung.
  **Where a rung is SWITCHED ON**: the routine page, in the two groups its two authority classes
  belong to — `ladder` under **Limits & reach → Oversight** (config, so it saves through the
  page's one accept bar, and the section names `budgets.max_subrun_depth` when that already caps
  the depth being chosen), and the interval knobs under **Models → Rung intervals** beside
  Deliberation, the other tuning key (`components/ladder-settings.js`). All three are in the
  settings vocabulary (`patterns/fields`), so a PATTERN may carry them — a family of routines that
  should be supervised is exactly what a pattern says — and the page marks an override like any
  other field. An empty `oversight_turns` shows the number the engine will DERIVE rather than a
  blank that reads as zero; clearing it sends `0`, which the endpoint writes as a REMOVAL from
  `tuning.yaml`, because absence is that knob's one spelling of its derived default (the routine
  PATCH dumps with `exclude_none`, under which a null would read as "not sent").
  **The overseer's model is a ROLE** (D164, shipped 0.430.0): `supervisor`, the fourth of
  `config.base.MODEL_KINDS`, resolved by the one pure rule `config.base.supervisor_role()` —
  its own model once someone configures one, else the routine's `main`, so leaving it unset is
  exactly the behaviour `ladder.escalate` had when it pinned `"model": "main"` in code. The
  operator accepted what comes with it: an overseer weaker than the worker it reads becomes
  configurable. It falls back to `main` and NOT to the server's system model, because an
  overseer resolved to a model nobody chose for this routine judges with less context than the
  run it judges.
  **A blank picker says the default its own role resolves to** — the four roles do not share
  one (`static/views/routine-config-models.js`, `blankLabel`): main and tool_call fall back to
  the server's system model, `supervisor` to this routine's `main` (following the main picker
  live, before any accept), and `uncensored` to NOTHING AT ALL — an unset uncensored role means
  referral is off, there is no system-model fallback behind it (`endpoints.for_uncensored`).
  One template for all four is how the supervisor row shipped claiming the system model while
  its own description beside it said "blank = main".
- `tuning.yaml` — the routine's machine-tunable BEHAVIOR parameters, classed with the RECIPE
  (editable by a routine holding `write_recipe`; config stays sealed — the file boundary IS the
  permission boundary). Today: `deliberation:` (terse|standard|deliberate|think-on-paper — how
  much thinking lands on paper: words the say contract, `engine/deliberation.py`; creation-suggested
  per task, slider on the routine page / conversation header, mid-run via control.json
  `set_deliberation` from the run view); `ladder_turn_interval:` (`n`, default **50** — turns
  between escalations, a CEILING on the interval and never a metronome, since three events pull a
  rung forward) and `oversight_turns:` (a rung's OWN pinned turn cap; unset it is DERIVED as
  `n // 2 + 1`, floored at 4). The two ladder knobs are tuning rather than config exactly so a
  meta-routine can raise `n` for a routine whose supervisor keeps answering `continue`, on
  measured evidence. The derived cap is a sublinear read plus a constant judge: `= n` prices a
  rung's fixed judging as if it scaled (the 2×-the-worker ceiling the design calls its deciding
  objection), a fixed cap starves the read at large `n` into `oversight_no_directive` —
  supervision that silently does nothing. Absent file = defaults;
  `config.load_tuning`/`write_tuning` are the one reader/writer pair, and a key not in
  `config.base.TUNING_KEYS` is reported and dropped, never applied — by BOTH halves (F759):
  the writer holds its RESULT to the same vocabulary and returns what it dropped, because a
  writer that merges a renamed key forward makes the loader's report permanent and unreachable
  (a null clears a key someone can still name; a key outside the set cannot be named at all).
  The same rule covers the two partial-block mergers behind `PATCH /routines/{slug}`
  (`web/patch_merge.py`, `ladder` + `run_gate`): an unknown key in the PATCH stays a 400, a
  stale key in the STORED block is dropped from the merge, and the response names it under
  `repaired` so a repair is read from the observation. Without it a rename left a routine
  unrepairable through the API — and unsavable where the merge is `extra="forbid"`.
- `main.md` — the workflow **decomposed and materialized into this routine** (an entry state-machine that
  routes to `stages/<name>.md` modules, read on demand) with its `## Done when` — what one finished run
  leaves behind, accounted at every main finish — and, where the operator named prohibitions, `## Never`. The clarified instruction is only a transient compile SEED —
  decomposed into the stages at creation and NOT persisted (a routine carries no `instruction.md`);
  the stages are the sole source of truth. The rules themselves are NOT here: they live once in the
  library and the run reads them with `read_rule`.
- `tasks/<id>/` + `state/jobs.json` — only for a routine with the `tasks` SETTING on
  (docs/jobs.md): each task's WORKSPACE (often a whole former routine — its own `main.md` +
  `stages/`, sealed as recipe; its state files, scripts and `.memory/`) and the engine-owned
  store holding the tasks, their checkpoints and the current run's processing ledger. An OPEN
  task's workspace is the run's working directory (`run_context.work_dir`), and
  `engine/jobops.py` holds the run's finish until every task due in it has a checkpoint.
- `scripts/<name>.py` (`rsched/scripts.py`) — the routine's OWN persistent helper scripts:
  PEP 723 + the util docstring-header standard minus the catalog lines. There is no model
  channel — a judgment call belongs in the recipe — but the library IS reachable: the utils
  a script names on its `calls:` line resolve through `gu` exactly as a util's own siblings
  do, and `utils_run.util_needs` folds their `secrets:`, `net:` and `fs:` into the script's single
  jail and env (`scripts.needs`). Access is DECLARED-ONLY: a script naming no calls gets no
  `GLOBAL_UTILS_HOME` and no `gu` on PATH, and `scripts.call_problems` refuses one that
  execs an undeclared sibling or declares a util the library does not have, rather than let
  it run without the secrets and network that declaration would have carried. Deliberately
  TOOLING, not a
  co-equal interpreter: the recipe stays the single interpreter of the task and delegates
  judgment-free sub-steps (polling, parsing, calculations, fixed artifacts) so a repeating
  step is written once and re-run for free. Run via the `script` action — a BASE kind every
  routine holds since 0.369.0, with no capability and no approval dial. WHICH INTERPRETER runs it
  is `rsched/scriptenv.py`'s subject (F764): a script declaring no PEP 723 dependencies runs on
  the engine's own interpreter — nothing to install, so nothing to build, the precedent
  `daemon/gate_prepare.py` already set for a dependency-free `admit.py` — and one that declares
  any gets a persistent venv at `<routine>/scripts/.venv`, its deps installed into it on first
  use (net-open build step, R40's prewarm rationale), gitignored against the `git add -A`
  autocommit. The venv is INSIDE `scripts/` because D179's own-dir seal mounts the routine dir
  read-only and only its EXISTING unsealed children read-write (`ownseal.writable_children` — a
  Landlock rule needs an inode), so a subprocess could never create `<routine>/.venv` at all:
  that is why `script` had never worked for a routine without a hand-made venv (R2887, R2891,
  R2892), and why it was a placement bug and not a missing grant. The jail is the run's fs roots (recipe and script read and
  write the SAME files) PLUS the private stores its declared utils claim on their `fs:`
  lines (`scripts.callee_fs_paths`, 0.329.0 — `sandbox.wrap` subtracts every util's private
  store from the wholesale mount, and a script execing `gu whatsapp` got the session
  directory subtracted and never re-admitted, F465); the env carries ONLY the granted secrets the script's header
  `secrets:` line declares, plus whatever its declared utils declare (`NAME?` = optional,
  withheld when not granted and named in the observation's `[note]`, as for a util; a
  declared, present, still-undecided secret files the util call's blocking exposure ask, over
  the transitive set). Authored by
  the run itself with `write_file` (an own-dir write; the repo's autocommit versions it) or
  by the user; no approval dial — the blast radius is a subset of the routine's own
  sandboxed permissions. The capabilities digest lists the routine's scripts. A child's own
  dir is its workspace, which holds no `scripts/`, so the routine's helpers are the parent's
  to call.
- `.util_outputs/<run-ts>/t<turn>-<util>.out|.err` (`engine/outputs.py`) — util output too large for
  the observation that carried it, saved in full instead of destroyed. A util's stdout is captured up
  to 1 MB (`utils_lib.OUTPUT_CAP`) and then cut to its 8k head for the observation (stderr keeps
  head and tail), and the
  transcript records the TRUNCATED payload — so that band had no survivor, and re-running is not the
  same data for a fetch, a paid call, or a mailbox read. ONLY truncated output is kept: an output the
  observation carried whole is already in the transcript verbatim. The pointer rides the observation
  that lost text (so the store needs no index), earlier runs' spills reach the next run through
  the state digest, and reads are ordinary `read_file` — which pages by line window, making a big
  output cheaper on disk than it ever was in context. Engine-owned and read-only for the run (like
  `runs/`), gitignored on first use (the run-end autocommit is `git add -A` and util output can carry
  tokens), never search-indexed, pruned to the last `KEEP_RUNS` runs.
  The run-end autocommit (`engine/autocommit.py`) also leaves out any file at or over
  `OVERSIZE_BYTES` (20 MB) and files an `oversize_state_file` health event per file: the routine
  repo is mirrored into the library and PUSHED, and one 223 MB inventory a run wrote to `state/`
  blocked every library push for days (the mirror's history had to be rewritten to drop it). The
  file itself is untouched — only the commit skips it. A run-end commit that does not land files
  `commit_failed` against the run and leaves the files for the next run's commit (Git writes,
  below).
- `.doc_cache/<sha256>.md|.json` (`engine/docread.py`) — the documents `read_file` CONVERTED. A
  PDF, docx, pptx or xlsx (known by its magic bytes: `%PDF-`, or an OOXML zip carrying
  `[Content_Types].xml`) is no longer refused as a binary: the engine runs the library's
  `doc-read` util on it — in the run's own jail, `offline` (TCP denied although its `calls:
  vision` makes the tree outbound) and with no secret — which writes layout-aware Markdown
  (pymupdf4llm for a PDF, one `<!-- page N of M -->` line per page; markitdown for Office), and
  `read_file` pages that file exactly like a text file. The util does NOT write into this cache
  and cannot: the cache is inside the routine dir and `ownseal.SEALED_DIRS` withholds
  `.doc_cache/` from every jailed subprocess (D179), so the engine's own cache directory is
  invisible to the process it asks to fill it — and Landlock has no deny rule to make an
  exception with, nor does creating the file engine-side first help, because the util
  atomic-writes a temp file BESIDE its `--out` target and so needs CREATE in the target's
  PARENT. The util is therefore handed a private drop directory in the system temp dir (the one
  tree `sandbox._toolchain` mounts read-write for every util on every run,
  `docread.DROP_PREFIX`) and the ENGINE moves the result into the cache, where its own write
  was always legal; the drop dir goes in the call's `finally`, and one a killed conversion left
  behind is swept by the same `_prune` that sweeps a half-written cache dotfile. Until 0.462.0
  the util was handed the cache path itself, so every FIRST conversion of a document in a
  routine dir died with `[Errno 13] Permission denied` on a `.<digest>.part` sibling — silent
  for as long as it was because a cache HIT returns before the util is reached. The observation says
  it is a conversion, names the pages converted (at most `docread.MAX_PAGES`, 200, per conversion —
  the rest are named with the util call that reaches them) and the pages with NO TEXT LAYER (a scan
  is reported, never silently OCR'd; the util's `--ocr` sends only those pages to `vision`). The
  invariant that `read_file` never materialises a file holds: the util streams to disk, the engine
  streams from it, and the source is hashed in 1 MiB chunks — so a document over `READ_MAX_BYTES`
  is converted rather than refused (what is read is the converted text). Keyed by the sha256 of
  the BYTES, so a re-read or the next window converts nothing and a changed file converts again;
  a failed conversion (util missing, failing, timed out) is never cached and falls back to the
  binary refusal with what failed appended. Engine-owned and read-only for the run, gitignored on
  first use (`paths.ensure_gitignored`, the one helper every such store uses), never
  search-indexed, pruned to the `KEEP_DOCS` (32) most recently read.
- `state/`, `LEDGER.md`, `inbox/` (daemon/web drop messages + answers here; a message's **`via`**
  is a CLOSED set, `engine/inbox.VIAS`, validated by the one writer `inbox.file_message`, and it
  is not a label but the SWITCH that decides when the message is consumed, whether the post-finish
  reap may resume a finished run for it, whether the conversation surface offers it as the
  operator's own editable text, and whether it counts as the user having spoken —
  `inbox.MACHINE_VIAS` is the one answer to "did a PERSON write this", read by `ctx.user_replies`),
  `questions/pending/`
  (a message is written by ONE function, `engine.inbox.file_message`, with a deterministic
  `name=` stem per channel — `trig-<event>`, `once-<id>`, `branch-<slug>-<ts>`, `bg-<taskid>` —
  and every SCANNER selects `msg-*.json` rather than "every file that is not `answer-*`",
  because `paths.atomic_write` creates its temp file IN the target directory)
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
  automatically — routines never run git themselves. **Recipe health** (`run_health.py`, the
  routine's development view `#/changes/<slug>` + `GET /routines/{slug}/health`) carries three folds over the durable usage records:
  `regression`, keyed on recipe version, flagging the newest recipe change when the runs after it
  are clearly worse than the runs before; `trend`, the same thresholds keyed on TIME, which is the
  only way a shared library-rule revision can register at all (it reaches every holder at once and
  moves no recipe commit anywhere); and `endings`, how this routine's partial finishes actually
  ended — `budget_exhausted` versus `run_partial`, a split the usage stream cannot make because
  both land there as `partial`. Deterministic thresholds, each constant justified in the module —
  see docs/run-analytics.md. `trend` is also PUSHED at run end as the `cost_trend_degraded`
  health event, and WHOSE regression it is, is decided before that row names anyone
  (`readmodels/trend_attribution.py`, F568): the same window is read across the fleet, and
  `scope` carries host · routine · unattributable — a host-wide condition degraded everything
  that ran on 2026-09-27/28 and filed three findings naming three routines for one problem with
  the box, so a `host` row now carries no `routine` at all and says what it is about;
  `POST /routines/{slug}/recipe/revert` is the one-click rollback (recipe files only — never
  routine.yaml or state; queued while a run is active and applied when it ends, D78-A).
  Flag-first: the improver never auto-reverts.

## Child runs (spawn + subtask), questions, injection

- **There is ONE child-run concept**, defined once in `engine/child.py` (F338): an isolated run
  with its OWN directory, its OWN budget sliced from the parent's remainder, its own recipe or
  pattern, and a declared relationship to its parent. `spawn`, `subtask` and (F325) a
  conversation `branch` are three scheduling MODES of it — parallel, sequential, and
  forked-from-a-conversation — not three concepts, and NOT a fourth action kind: the existing
  kinds keep their names at the action surface, because each names a different scheduling intent
  a run genuinely chooses between, and share this contract underneath. Three names had invited
  three mental models, and the copy drifted between them until it claimed something false —
  that children share the parent's working directory (R409/R410). The contract is: isolation, a
  budget of its own, and a declared HAND-BACK (below). `engine/child.py` also owns the mode
  vocabulary the prompt copy renders (`mode_noun`) and the hand-back path (`handback_dirname`),
  so the three surfaces — kind copy, observations, docs — cannot drift apart again.
  The in-engine modes are materialized from a workflow pattern and run recursively
  (`engine/childrun.py` `build_child`, tree on disk under `runs/<ts>/sub/<n>/`), differing only
  in SCHEDULING and budget. A child's fs roots EXTEND the
  parent's — the parent routine dir and every configured/granted root stay reachable from
  `sub/<n>` (F185; capabilities stay off, resources inherit). Both are NON-BLOCKING background
  threads — the turn loop never monopolizes on a child, so the conversation stays responsive.
  **spawn** = PARALLEL (≤4 parallel; you keep working). **subtask** = SEQUENTIAL (start it, then
  `wait n=N` before the next so you can fold its result in; `turns` pins its budget, else half the
  parent's remainder). **The HAND-BACK**: a child returns its finish summary always, and returns
  FILES by writing them into its OWN `artifacts/` — the convention the Artifacts panel and
  detached tasks already use, so no action-schema field was added and a non-child run pays
  nothing. The engine copies those into the parent's `artifacts/from-sub-<n>/`
  (`child.collect_handback`, the one copy every hand-back shares) and NAMES the landed paths in the
  notification, so a parent never greps the runs tree for a child's output. Collection happens in
  `subruns._collect`, the child's single finalization point — two paths report an exit (`wait`, which consumes finished
  children directly, and the turn-boundary announcement), so collecting in either reporter was a
  race. Isolation is kept on purpose: a shared writable dir between concurrent children is a race
  the engine would have to arbitrate, and an isolated dir plus a declared hand-back gives the same
  result with none. A child's completion is delivered by the turn-boundary hook
  (`announce_finished_subruns` — one `CHILD RUN FINISHED (<mode>)` headline for every mode); `wait` is RESPONSIVE
  (it yields the moment a user message is pending — `inbox.has_pending_messages` — so the loop drains
  it and the parent replies, then waits again). Children are threads, so they die with the process
  (DELIBERATE — the subprocess alternative was evaluated and rejected, docs/child-runs.md § Process
  model): a resume marks any still-running child aborted and notes it (`history.orphaned_children`),
  and a subtask does NOT survive a conversation reply-finish — a job that must outlive a reply is
  the separate **`detach`** capability below, not a subtask.
  Decomposition is recursive (a child hits its own decompose gate; depth ≤ `max_subrun_depth`) and
  the `general-task` seed workflow carries a standardized `decompose_decision()` gate
  (inline | sequential | parallel); `converse` handles decomposition as inline prose.
  Children are killed at parent finish (never outlive it); exits fold usage into the parent. The
  recursive tree is visualized live in the run/conversation rail (`rsched/readmodels/subruntree.py` →
  `static/components/subruntree.js`). `subrun_start`/`subrun_end` events carry `mode` (sequential/parallel)
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
  window. The **global pause (D34) is enforced PER PHASE inside `tick`**, which is also the boot
  path's gate (`_boot` reconciles BEFORE the pause-checking `boot_catchup`): the two phases that
  START a run — intake's `fire` and the wake's `resume_terminal` — are HELD, while deliver/digest/gc
  proceed because a finished task's hand-back starts nothing. Neither hold drops anything (the
  request file stays unconsumed; the wake is state-driven), so neither files a health event. F721:
  the tick sat OUTSIDE the `if not is_paused:` guard, so a paused instance kept starting runs —
  silently, and worse at boot.
  A detached run HOLDS the restart's quiet gap like any other active run: `start_new_session`
  changes the session, not the container and not the cgroup, so it survives the daemon's exit on
  neither deployment (Docker ends tini and the kernel SIGKILLs the PID namespace; the systemd
  unit's default `KillMode=control-group` kills the cgroup). The wait it costs is bounded by the
  task's own 60-minute budget, and a background task uses deferred asks only, so it can never park
  on a user and the gap always comes. Structural, not a permission: a root conversation gets it at
  setup and nothing else does; action = `detach`. Monitor/cancel via `web/api_background.py`
  (`GET/POST …/background`, `…/background/{id}/cancel`); the rail renders the jobs. See
  docs/background-tasks.md.
- **A SINGLE ACTION in the background (`background: true`, D118)** is the third and smallest unit,
  and the one that keeps the SAME context: not a child run and not a detached job, just one slow
  step the run should not sit through. `engine/actions.BACKGROUNDABLE_KINDS` — `util`, `script`,
  `shell`, `llm`, `decide`, `read_file`, `view_image`, `memory_read`, `read_rule`, and (D118
  phase 3) the three mutations `write_file`, `edit_file`, `memory_write` — may carry the flag. A
  deferred mutation is ORDERED rather than trusted: `engine/background_order.py` records the one
  target it declared (`path`, or `name` for a memory note) and `actionroute.dispatch_action` holds
  any later action that touches it until the write has landed, refusing only a SECOND deferred
  write to the same target, which has no safe order. That barrier is what replaced phase 1's
  blanket refusal of every mutation, and it closes on the dependency, so a run that never calls
  `wait` cannot read stale state either.
  `actionroute.dispatch_action` runs the kind's CALL-TIME GATE first and synchronously
  (`_gate_for_background` → the D39 secret gate for `util`/`script`, which a thread cannot run
  because it files a blocking ask and a thread has no turn to block on — F633), then hands the
  action to `engine/background.py`, which runs the ordinary dispatch path for that kind in a daemon
  thread (`background._runner`: `executor.do_script` for `script`, `executor.dispatch` otherwise)
  and returns a *started* observation (a handle,
  the kind, a note) at once, so the turn ends and the conversation keeps its speaking turn. The
  real observation is appended at a later turn boundary by `background.collect`, beside
  `archival.collect` and `announce_finished_subruns`; `loopend` calls `background.settle`, which
  records a result that landed too late (`unread`) or a call still running (`abandoned`, its
  in-flight model calls closed via `instrument.abandon_open_calls`) and names BOTH losses in the
  run's summary — in two deliberately different sentences, because the abandoned call's result is
  gone while the landed one's sits unread in the transcript and need only be read
  (`engine/background_summary.settle_note`; F715/D195, the hidden-pending-call case a run cannot
  flag for itself because it was never shown it). A run that read everything gets no line. A run that no longer wants a result CANCELS it — `kill handle=bg1` (D176 option (a):
  `n` stays the sub-workflow namespace, `handle` addresses a background call) routes to
  `background.cancel`, which drops the Pending from the live list AT ONCE, so `collect` cannot
  deliver it and the cap slot frees immediately. **What it stops depends on the kind.** For
  `background.STOPPABLE_KINDS` — `util`, `script`, `shell`, the three whose thread is blocked on a
  SUBPROCESS — it is a real kill: `Pending.stop` is set before the cancel records, the thread
  dispatches against `_cancellable_ctx` (a shallow `__getattr__` proxy carrying `background_stop`,
  which `control.cancelled_for_turn` prefers over its turn-keyed file channel), and
  `utils_run.run_jailed` ends the command's whole process group through `procgroup.terminate`
  (F586/D160-C). A proxy and not a mutated context because the real one is shared with the live
  turn; a delegate and not a copy because transcript/meter/usage-lock IDENTITY carries the
  parked-spend booking (D167). For `llm`, `decide` and the reads — inside the interpreter, where
  Python has no interruption point — only the delivery stops and the call runs to its own end
  unread. Either way the spend books (D167), and `stopped_work` on both observations says which of
  the two happened; both promises are stated in the `handle` field's own schema description,
  because that is the only documentation a run meets at the moment it decides. A cancelled call is recorded with
  `cancelled: true` rather than `abandoned`, so a reader can tell a deliberate drop from a run
  that ran out of turns. All these records are `observation` events carrying
  `background`/`handle`/`started_turn` — payload EXTENSIONS, no new event types. The console
  turns all four endings into sentences in ONE module, `static/components/obs-background.js`
  (plus the `cancelled` row state in `static/obs.css`), which is also where the `kill`
  confirmation chooses its namespace: `handle` when the action carried one, `n` otherwise — F723
  was that choice missing, rendering "sub-workflow #undefined terminated" for every cancel. Built on the ARCHIVAL shape, deliberately not on
  `DetachedManager`: a daemon-ticked multi-process unit would not start until the next tick, by
  which time the reply's process is gone. A CONTROL kind may not carry the flag, nor may a mutation
  this model cannot order — `delete`/`move`/`mkdir` (nothing slow to win, and `move` has two ends
  rather than the one target a barrier keys on) or `write_util`/`write_rule` (no
  `executor.DISPATCH` entry to run in a thread); the three that DECLARE one target may, behind
  the barrier described above (D118 phase 3). Every refusal is given WITH THE REASON inside the
  schema-retry cycle. A `then_script` may not ride a deferred change either: the thread
  dispatches the kind, so the script would be dropped unrun with its secret gate never asked
  (`engine/thenscript.problems`). **Three things now share the word "background" and the distinction is
  load-bearing**: a within-reply `subtask`/`spawn` thread, a cross-reply `detach` task, and this
  per-action flag. Name the mechanism, never the adjective alone.
- **ask_user** is `blocking` (poll `inbox/answer-<qid>.json` up to `ask_timeout_min`, then the run
  CONTINUES on the action's stated `default` and the record stays open as deferred) or `deferred`
  (filed to `questions/pending/`, surfaced in a later run's state digest). It asks ONE question,
  or SEVERAL in one action via `questions:` — each entry carrying its own
  `question`/`options`/`default`/`request`/`config_patch`/`blocks_work`, filed as one decision
  record per question through the same single-question seam and sharing one `batch` id
  (`engine/askbatch.py`; D212). A batch is never blocking: `mode: "blocking"` parks the run on ONE
  answer, which cannot mean anything for several, so `askfields` refuses the pair. Every record
  carries its CLASS — `blocking` or `closing` (`engine/askclass.py`), ORTHOGONAL to `mode`: the
  class says whether the asking run could do its work without the answer, `mode` whether its
  process sat and waited for one. The SUBJECT sets the default (a typed access `request` is
  blocking, a plain question closing) and the run overrides per question with `blocks_work: true`.
  An ask may carry
  `request: "<entity-id>"` — a typed ACCESS REQUEST (entities.py; docs/rules-permissions.md's
  grant model): the record then settles ONLY on one of the typed allow/deny × now/forever
  decisions (plus *allow once* for the once-grantable classes, `entities.ONCE_CLASSES`: a
  turn-action class is spent by the next dispatched matching action (D65), a secret or an fs
  root by the next action that RECEIVES it (D76), then revoked; the Decisions page's buttons;
  free text is held, D38),
  forever-decisions are applied
  to routine.yaml by the WEB at click time (`web/grants_apply.py` — the engine never writes
  config), and every decision seeds the run's in-memory overlay (`engine/requests.py`) at
  whichever seam consumes it: the blocking answer, boot (decided between runs), or the live
  turn boundary (a deferred ask answered mid-run — the running run's policy, schema and util
  sandbox pick the grant up at once). The run view's **ask back** is the one reply to a
  blocking decision that decides nothing — on ANY of them: a plain question, a util, rule or
  curated-reminder approval, an access request, the secret-exposure request. It ends the wait
  at once (a plain non-settling reply is held instead, D38) and reaches the run on the
  observation of the action that asked, in one wording (`obs_admin.dialog_reply`): the
  operator's words, and the instruction to answer them and re-submit that action. Nothing is
  written, granted or run on it. The record stays open, and the re-submission SUPERSEDES it as
  the next decision filed for the same SUBJECT (`interact.handle_ask`; `loop.dialog_qids` keyed
  `(type, subject)` — the util, the rule, the reminder id, a request's entity ids); no
  unrelated ask can resolve it. The key outlives the leg: a resumed leg — a restart between the
  ask-back and the re-submission, a conversation whose next reply re-submits — re-keys every
  record still open from the transcript (`askback.rebuild_dialogs`; the `question` event carries
  the subject), across replies as well, because an open decision outlives the reply it began in. Blocking asks are durable records too, and the console is
  the ONLY surface that carries them: no channel mirrors a decision, and the engine and daemon
  make no implicit outbound send at all (0.230.0 — see docs/notifications.md). Browser push is
  the away-from-console tier, rendering the same open-decisions record. The web layer posts
  answers into `inbox/`. Decisions-page LIFECYCLE (fields on the one record shape, never a new
  type): a blocking ask can be **deferred to the next run** (a `{defer: true}` inbox marker —
  the engine unblocks on the stated default, the record stays open; stale markers are swept at
  boot), a non-blocking one **snoozed** (`snoozed_until` on the record → `snoozed: true` derived
  on read; hidden from the inbox + badge, still in the run's digest), and a routine with >5
  unanswered deferred asks gets a `decision_backlog` flag on its dashboard card.
  Any of them can also be **DECLINED** — the page's first-class "no"
  (`POST /api/questions/{qid}/decline`, `web/api_question_decline.py`). R2197: answering closed
  an item only by saying something, a snooze left it open in every run's digest and a defer
  released a parked run without deciding, so a refusal could only be prose. A decline writes the
  ORDINARY answer payload (`rsched/decline.py` owns the one payload shape, the one phrase and
  the digest line), so the item leaves the open set exactly as an answer does and
  `collect_deferred_answers` drains it unchanged — what it adds is the VALUE
  `decision: "declined"` (+ an optional `reason`), carried onto the answer pair both delivery
  seams read, so the asking routine matches a token instead of parsing a sentence. An ACCESS
  REQUEST is refused by the route (400): its "no" is `deny_now`/`deny_forever` — the values the
  engine reads to deny the grant and the only ones that settle a blocking request's wait
  (`engine/interact._held_not_settled`) — and the question-scoped word is kept separate from
  that grant vocabulary for exactly that reason.
  `~/routines/.control/reports.jsonl` is the append-only REPORT ledger every routine writes
  through the ungated `report` action (`R<n>` id, routine, title, detail, an optional `target`
  when the reporter can name the owner, plus event rows folded onto it: `delivered`, stamped
  when an addressed target's run drains the message, and `retracted`, appended when the user
  withdraws a not-yet-consumed delivery — the outbox's one write, docs/messages.md).
  Unaddressed rows are self-audit's triage queue; addressed
  ones are also delivered into the target's inbox for its next scheduled run — no run is
  started. The Messages page reads it, so a hand-off that carried is distinguishable from one that
  silently never arrived (docs/items.md).
  `~/routines/.control/hooks.jsonl` is the one HOOK store (`rsched/hooks`, D152): a report, a
  consequence reminder and a mid-run injection are three configurations of one five-field
  record — trigger (`send` · `match` · `moment` · `schedule`), addressee (`routine:<slug>` ·
  `run:<id>` · `child:<label>` · `parent` · `operator` · `triage` · `self`), payload, delivery
  (`next_prompt` · `turn_boundary` · `hold_before_action` · `decisions_page`) and disposition.
  Two rules fall out and bound what a hook can do: the run tree is the only addressing scheme,
  and `hold_before_action` is the only delivery moment that can change what runs. For a REPORT
  this stream is now the SOURCE, not a copy: `reports.file_report` writes the hook first and
  appends the `reports.jsonl` row as its PROJECTION (`rsched/hooks/reportproj`), lifecycle
  events included, so the two cannot drift and the ledger is rebuildable from the stream
  (`reportproj.rebuild`); a hook that cannot be written refuses the filing. `reports.jsonl`
  stays because it has readers outside the engine (the operator's backlog counts,
  self-audit's folds) and its bytes are unchanged. The reminder layer and the injection path
  still own their stores and MIRROR their writes here (`engine/remind_write`,
  `engine/control.inject_user_message` through `engine/hookmirror`), best-effort — including
  the target's disposition of a foreign hook, which is a later row about the same id.
  The FOURTH source is the one the store was built for: a RUN-TREE hook (`engine/runtree.py`,
  F695), which is the `report` action with `target: "parent"` or `"child:<label>"` and has no
  store of its own — delivered on the `hook` channel at the addressee's next turn boundary, with
  no `R<n>` because both runs are gone in minutes and an item nobody can close is exactly the
  backlog pathology the ledger exists to show. It is what the lifted `ctx.depth > 0` return in
  `control.drain_injections` opened: a child drains its own hooks, and nothing else. Two
  writers are deliberate for now: the report ledger and the reminders store have readers
  outside this engine (the Decisions page, self-audit's folds, the per-routine fire tallies),
  and the duplication ends when the surfaces ride the one mechanism. Every finished
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
  it is raised; a call the run's abort ended is not counted at all; subrun records carry their
  OWN counts, never folded into the parent), and
  `asks_deferred` (deferred-question churn). `util_stats.py` joins the stream with the library's
  git history (created/revised per util) and a stat-memoized transcript backfill for pre-stream
  runs — the Stats tab's "Global utils" table, computed live per request. Its PERSISTED snapshot
  (`util_stats.snapshot_path()`, read only by the `util-stats` util) has ONE writer: the daemon's
  `daemon/util_stats_watch.py`, debounced on `run_*` bus events, in a thread. Every engine used to
  write it at its own run end, paying all three source reads per run for a file no engine reads. The referral AUDIT (`ctx.referrals`: turns + llm
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
wall clock, tokens and subruns all reset — and fresh once-only guards, since each reply is a new
task; a reply interrupted and resumed keeps its own, `engine/guardscope.py`). A new conversation
has NO per-reply ceiling (`conversations.CONVERSATION_BUDGETS`, all -1; a person can set one) and
the routines' `standard` deliberation: what ends a reply is the work reaching a handover point
(a finished plan step, a verified deliverable, a decision for the user, a blocker), and what the
user asked for is kept as GOALS (`engine/goals.py`) that a reply declaring itself `final` must
meet. A conversation's spine is its own **working plan**
(`state/plan.md`, written and revised by the run, inlined at the top of every later reply by
`state_digest`) — the emergent counterpart to a routine's compiled `stages/` + `phase.json`.
- Runner: conversation replies draw from a **reserved interactive slot pool** (`INTERACTIVE_SLOTS`,
  3) — cron can't queue a chat reply and vice versa; `engine_cmd` targets `cfg.dir` (a path),
  which `_routine_dir` accepts, and names the config + homes the child must resolve to (F394). Run resolution in `api_runs`/`api_questions` is home-aware.
- **Slash commands**: the user can run the SAME effect actions/utils the model can, from the chat
  input (`/util …`, `/read_file …`, … — autocomplete + a reference panel fed by
  `GET …/commands`). A command-flagged inbox message EXECUTES at the turn boundary via
  `control.run_user_command` — parse (`engine/commands.py`) → the model action's exact
  validate_action gates → the model's own routing table (`actionroute.dispatch_action`, so a
  `/util` passes the D39 secret-exposure gate exactly as the model's call does) — costing NO
  model turn; the observation lands in the
  transcript (`user_injection {command}` + `observation {user_command}` payload extensions) and in
  the model's context as one USER COMMAND message. **The speaker turn stays with the user**: when
  the model has handed the turn back (an authored finish) and the resuming message ONLY runs
  commands (`loop.leg_commands` and not `leg_prose`, boot sets `leg_after_authored`), the loop's
  command-only gate ends the leg after boot with NO model turn and NO reply
  (`loopend.exit_commands_only` — no finish event, result.md untouched, status→finished); the next
  PROSE message hands the turn over and the model replies, seeing the command results replayed. A
  run with its OWN work (a scheduled routine fire, crash recovery mid-workflow) has no authored
  hand-back, so it always proceeds and a command there is injected context. Loop-control kinds are
  not commands.
- **Branching** (`rsched/branches.py` + `web/api_branches.py`, F325) — the `branch` MODE of the
  child-run contract (`engine/child.py`). `fork_conversation` copies a conversation at a chosen TURN
  into a new dir: the parent's routine.yaml config wholesale plus a `parent: {slug, turn, forked}`
  record, `main.md`/`instruction.md`/`tuning.yaml`, `state/` and `attachments/` (the files the
  inherited history refers to — a transcript naming a missing attachment is a broken history), and
  the transcript up to the fork point. NOT `artifacts/`: the branch produces its own and hands those
  back, so copying the parent's would make every hand-back return the parent its own files. The cut
  snaps to a clean turn boundary via `rewind.cut_index_for_turn`, the SAME cut the D69 rewind uses —
  a prefix ending mid-turn would replay as an assistant action with no result. Per-event `usage` is
  STRIPPED from the copy (the parent accounted for that spend; counting it again in the branch would
  double it instance-wide) and the header is rewritten to name the branch, since every read model
  keys off it. A terminal `status.json` beside the copied transcript is what makes a branch an
  ORDINARY continued conversation: the user's first message in it goes down `resume_terminal` and
  replays the inherited history, so the engine has no branch case at all. Because it is a copy, a
  branch **cannot mutate the original**.
  **Merging is deliberately a HAND-BACK, not a transcript merge** — two divergent histories cannot
  be interleaved into one coherent conversation. `hand_back` copies the branch's `artifacts/` into
  the parent's `artifacts/from-branch-<slug>/` and files ONE inbox message on the `branch` via —
  a LIVE via that the parent's next reply drains, and deliberately NOT a USER via, so nothing wakes
  the parent for it, the composer never offers it as the operator's own editable text, and it does
  not advance `ctx.user_replies`. The copy, the directory name and the wording are
  `engine/child.py`'s, shared with a subtask's and a detached task's hand-back
  (`handback_dirname` / `collect_handback` / `handback_text`): three landing places
  (`from-sub-<n>`, `from-branch-<slug>`, `from-bg-<id>`), one copy, one wording, each naming the
  landed paths rather than a count. The same delivery shape as a detached background task
  (`daemon/detached_delivery.deliver_one`),
  which is the point — a hand-back is the child-run result and the parent already knows how to read
  one. It does not wake the parent; its next reply drains the message. Both ends are explicit user
  actions (`POST …/branch`, `POST …/handback`, with `GET …/lineage` for the family in both
  directions): where two lines of work diverge, and whether a branch's result is worth the parent's
  attention, are decisions that cannot be inferred.
- **Refusal flag** (⚑, `web/api_refusal_flag.py`, operator decision 2026-10-01) — any reply of a
  settled conversation can be REDONE on its uncensored model, which then carries the conversation.
  One POST, validated whole before its first write: the reply and everything after it go through
  the D69 truncation (`rewind.rewind_transcript(before_reply=(turn, ts))` — the cut lands just
  before the message that OPENED the reply, `rewind.reply_opening`: what the user sent between the
  leg's start and its first turn, a first reply's message being instruction.md, so its cut keeps
  the header alone; the tail is archived and result.md set back to the last reply kept),
  `models.main` becomes the uncensored model (a pick, when there is none, lands as uncensored AND
  main) through `config_fields.validate_models`, and the message is re-sent verbatim on the
  `conversation` via carrying `refusal_flag` inbox metadata, which the engine records as the
  `operator` seam's `refusal` event as it delivers the message (`refusal.record_operator_flag`
  from `control.inject_user_message`) — the transcript keeps one writer. A reply is addressed by
  its turn AND its finish's `ts`, because one that ran no turn of its own (a classifier refusal
  that exhausted the chain) shares the previous reply's turn number. A GET on the same path is the
  warning gate's preview, so the browser never re-derives which message opened a reply. The one
  path on which the uncensored model answers and acts; since the 2026-10-16 takeover order the automatic path does too - a refused request is handed to the routine's uncensored model whole, with the exponential back-off (docs/endpoints.md).
- Web: `web/api_conversations.py` (create/message are multipart — **attachments** land in
  `<conv>/attachments/` and ride the message text as an `[attached files]` block; vision util for
  images, `read_file`'s Markdown conversion for a PDF or an Office document). **Artifacts**:
  deliverables the model `write_file`s into a DELIVERABLE DIR —
  `artifacts/`, `reports/` or `output/` (`web/artifacts.py` `ARTIFACT_DIRS`, one list governing
  listing, serving and deletion; R339/F336 — scanning `artifacts/` alone left the panel empty for
  a run that committed a verified `reports/*.pdf`, with no way to register it) — are listed/served
  here and rendered in the chat's side panel (html sandboxed, md/img/pdf/csv/json
  inline); routines get the SAME panel on the run view (`api_routine_files` `/artifacts` + `/artifact`,
  `components/artifacts.js` with `base: "routines"`), with the phase-graph card on top.
  A file fetched for the panel or the files card is a blob URL, and a blob URL carries the
  console's ORIGIN — so a new tab opens one as itself only when its type cannot carry script
  (raster image, audio, video, PDF, plain text, an untyped download) and frames everything else
  in the same `allow-scripts` sandbox (`components/blobtab.js`): a page a routine wrote never
  runs with the operator token in reach.
  The RAIL those cards sit in is one component too (`components/rail.js`, R341): the run view
  and the conversation view render the same collapsible sections, remembered per browser —
  they were divergent copies, and only the conversation one could collapse (F296/R340).
  WHICH sections the run rail has is `components/runrail.js` — artifacts, files, phases,
  sub-runs, goal, plus two added LAZILY only for a run that has one: `oversight`
  (`components/ladderstrip.js`) and `jobs` (`components/runjob.js`, F646 — which of the
  routine's jobs this run is working on, is owed, or checkpointed; docs/jobs.md).
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
- Defaults: routine default permissions+capabilities, the `detach` action (structural for a root
  conversation, since a finished task reports back into the chat), tuning.yaml
  `deliberation: deliberate` (chat is judgment-heavy; slider in the header panel), the `shell` action OFF (one-click grant;
  `scheduling` greyed — routine-only); rules = the routine defaults minus decision-record, plus **git-checkpoint**
  (checkpoint commits in external project repos — the conversation dir itself is unversioned).
  Conversations feed workflow-usage + health events; they are EXCLUDED from the dashboard,
  scheduler, and instance-export. `bootstrap.sync_seed_library_docs` (every boot) lands new seed
  workflows/rules/permissions/patterns + playbooks (subfolder-aware), skipping anything the
  library's git history shows was deleted — how `converse`/`git-checkpoint` and seed playbooks
  reach existing instances without un-deleting what an operator removed.

## Libraries & seeds

ONE git-backed library repo (`libraries_home`, default `~/.local/share/routine-scheduler-libraries`),
seedable from the repo and syncable to a remote, holding **workflows/** (control-flow patterns),
**rules/** (the general rules — ONE copy each, held by slug), **permissions/** (conduct
docs whose `requires:` frontmatter names the capabilities they presume), **playbooks/** (reusable
one-shot conversation briefs — the save/use-instruction analog), and **utils/** (the ONLY way
routines run code, with the `gu` dispatcher at the root). Repo seeds: `library-seed/` (workflows +
rules + permissions + playbooks),
`util-seed/` (utils). There is NO routine seed: a meta routine is authored like any other
routine, through the scaffold path, and a fresh instance starts with none — the dashboard shows
a notice while every meta routine it does have is disabled. `self-audit`
works LOOKUP-FIRST from `.codemap/` — a compact derived map of this repo (module API surface,
routes + JS callers, contract literals, mechanical audit flags — orphan candidates
pre-verified by a whole-repo reference scan) the library code-search util's `map` verb regenerates at orient
(gitignored; delete-and-rebuild, never hand-edited; `--since` prints the symbol-level change
set for the since-anchor review). Its companions: `sym` (syntax-aware surgery — read one
complete symbol with a content hash, compare-and-swap replace with a whole-file re-parse
gate, `diff` as a scoped per-symbol old→new review with signature-impact notes, `check` as
a syntax pre-gate before pytest). `token-lab` is
the token-efficiency R&D loop: measures real usage, tests methods via llm subcalls ONLY (never
integrates), publishes `artifacts/report.html`. The **library-sync** ROUTINE
(0.165.0; it was a daemon job from 0.29.0 and is a routine again — the two git operations were
never the work, noticing and REPORTING that a push stopped landing is, and a job whose only
outcome surface was a status file let 94 commits pile up unpushed)
syncs the WHOLE instance into that one repo: each routine's working tree (minus `runs/`, `.git`,
transient inbox/question state and whatever the routine's own `.gitignore` keeps out) into
`routines/<slug>/` and the server config — every `*token`/`*api_key`/`*secret`/`*password` value
AND the password of any URL redacted — into `config/`; then commit (`add -A` over the whole
tree, under the shared repo lock) → pull --rebase --autostash → push (never over a failed pull).
**No file reaches that repo carrying a credential the instance holds**, wherever a routine keeps
it (`rsched export` live, seeded as `util-seed/utils/instance-export`). Before writing anything
the export reads every credential stored beside the config — the config's own, the central and
routine-scoped Secrets stores, the OAuth connections — and every routine's webhook tokens. A
routine.yaml has its webhook tokens redacted; any other file containing one of those values,
verbatim or JSON-escaped, is WITHHELD: never written, pruned from the tree if an earlier export
put it there, and reported by NAME, never value — with `was_mirrored` when the credential has
therefore already left the machine and is the operator's to rotate. library-sync reports each
withheld file to the routine that keeps it, which owns the fix. Withholding rather than redacting
is deliberate: every file the mirror holds stays a byte-identical copy while a credential kept in
a routine's tree (a copy of the config in its state, a token pasted into a script) stays visible
as the defect it is instead of shipping doctored. A Secrets-store value counts when its name or
JSON-map field says credential (`*KEY`, `*TOKEN`, `*SECRET`, `*PASS`/`*PASSWORD`/`*PWD`, `*HASH`,
`*API`, `*AUTH`, `*CREDENTIAL(S)`) or spans lines — never when it is a path: the store also
keeps addresses, hosts and user names that sit in hundreds of routine files by design. A value
under 8 characters (the engine's redaction floor) is not matched. The export fails CLOSED — a
config or store it cannot read refuses the whole export, since it cannot vouch for a mirror whose
credentials it cannot see. **One store it cannot read any more is the point of
`GET /api/credential-names`** (D171, operator decision 2026-10-08): the sandbox hides the whole
config dir from every util and every shell by design and Landlock only subtracts, so from
2026-10-03 the export could not read `secrets.env` at all and the off-box mirror went stale
rather than shipping unvouched. The daemon is unsandboxed and already reads the stores, so it
serves the NAMES — the central store's (already in every run's prompt, D46), the routine-scoped
ones as one UNATTRIBUTED union (D103 keeps whose they are inside the daemon), the OAuth store's
credential-bearing field names and the config's credential keys — and never a value: the response
is identical on an instance holding real credentials and one holding placeholders
(`web/api_credential_names.py`, deliberately NOT under `/api/settings`, which the routine token
the export runs under is refused wholesale). A by-NAME scrub list cannot do everything a
by-VALUE one did — it cannot recognise a central-store credential someone pasted into an
unrelated file — so a redactor using it must say which sources it could still scan by value,
never let a shrinking `withheld` list read as a clean one. `bootstrap.py` seeds on
first boot; `deploy/install.sh` for host installs. Everything in the library is user-EDITABLE from
the Library tab, and DELETABLE except permission docs (the capability layer's conduct surface) and
the `converse` workflow (every conversation is materialized from it BY SLUG, so losing it
breaks conversation creation) — both guards are server-side.
A deletion STICKS, for every kind. The boot sync (`sync_seed_library_docs`, and `sync_seed_utils`
before it) tops the live library up with anything the seed carries and it lacks — but it asks
`libgit.path_was_deleted` first, so a doc the operator removed in the Library tab is not brought
back at the next restart and pushed. (It used to be: only utils had that guard, so a deleted seed
workflow or rule returned at every boot.) The sync covers the flat kinds — workflows, rules,
permissions, settings PATTERNS and the reminder store's README — plus whole playbook folders. It is ADD-ONLY and stays that way: these
files are user-editable, so overwriting one would discard an operator's edit silently. A seed doc
or util whose TEXT must change on a live instance is carried by a one-shot migration instead,
which replaces the live copy only while it is byte-identical to the seed it supersedes and names
every copy it left (`migrate_seed_utils` carries this release's four util fixes).
- **Workflows** are self-contained **Python pattern files** (`.py`) that DEPICT a routine's control flow —
  never executed, parsed statically with `ast` (`workflows/pyworkflow.py`). Each has a `META = {...}` dict
  (`slug / name / description / when_to_use / version / tags / includes`, optional `tools:`
  allowlist), a `PHASES` literal, a top-level `main()` whose body is the per-run control flow,
  one function per step, and dummy parameter imports (`from routine.params import …`) naming the routine's
  parameters by type+meaning. The runtime is
  unchanged — routines are still the markdown `main.md`+`stages/` the orchestrator interprets: `adapt.decompose`
  turns a Python pattern into that markdown at scaffold; `materialize` renders it whole (sub-routines/fallback).
  A `tools:` list restricts action kinds (`finish` always allowed), and must COVER the pattern's
  action-import line — the schema is narrowed to `tools:`, so an imported-but-excluded kind is prose
  for a channel the run cannot emit. A pattern's `DONE_WHEN` literal is the skeleton of what one finished
  run leaves behind, which decomposition turns into the routine's own `## Done when`; whether a ROUTINE
  ever finishes is its operator's finish line, never a pattern's. `workflows/lint.py` gates every change;
  `suggest`/`generate` rank/draft via the `system_model`. Routine creation is initiated from a CONVERSATION ONLY (D58/D59) — there is no standalone new-routine
  wizard page. The conversation agent clarifies the task WITH the user in normal chat, then emits the **`create_routine`**
  action (`engine/create_routine.py`) — valid ONLY from a root conversation — which materializes the routine
  SYNCHRONOUSLY through the SAME `workflows.scaffold` path the retired wizard build once called (decompose the chosen
  workflow into main.md + stages/, record its held rules, write routine.yaml, init the auto-push git repo; the
  daemon's `registry_rescan_s` timer picks the new dir up). There is no clarification ROUTINE behind
  any of this: the standalone clarification wizard was retired with D59; the clarifying now
  happens in the conversation's own chat.
  The intake is held to a WALKED clarification, and the walking is done in DECISIONS (F383): every point
  still open goes to the user as its own `ask_user` carrying `options`, which the console renders as
  numbered picks — prose in the reply makes the user compose an answer the agent already knew how to
  offer. What cannot be a gate is whether the answers are REAL: a machine check cannot tell a settled
  answer from a plausible one, and the cause of a thin intake is the generation copy, not a missing
  validator. Three answers must be SETTLED before a draft is presented as decided: what the routine
  PRODUCES each run (the artefact by name and where it lands), what DONE looks like for ONE run, and which
  pattern it is built on — that last one chosen from a catalog that always ends in `generate`, drafting a
  NEW pattern fitted to this task, so the workflow question is never a closed list — and that catalog
  EXCLUDES harness patterns (`meta`, e.g. `converse`), which assume a present reader a scheduled routine
  never has. The `create_routine` kind surface states the three as a precondition with a checkable test
  (could you QUOTE the user's own answer? if not, ask rather than draft), and the draft observation tells
  the relay to name any of the three that is still the agent's inference rather than present it as the
  user's decision. That observation ALSO carries `design_checks` — the operator's standing intake rules
  (SHAPE: offer the ingest/outbound split into two routines in one lane · MECHANISM: mark the
  judgment-free repeated steps for the routine's own `scripts/` · OWNERSHIP: the instruction is the
  TASK, conduct is rules and capability is permissions · SCOPE: schedule/budgets/workdir/models are
  config, never the instruction). This is the ONE live copy of the intake contract. A second copy
  lived in a `clarify-instruction` pattern that nothing ever executed, so it went stale unnoticed
  — it still described conduct as per-routine "traits" long after rules became one shared library
  doc. It was deleted with its content lifted here.
  **A run without a user in the loop QUEUES instead of creating** (F328, `rsched/pending.py` +
  `web/api_pending.py`). The restriction to conversations was right — a scheduled run has nobody to
  design WITH — but its consequence was wrong: routine-improver reached a run holding a fully
  designed, user-approved routine plus the lane it belonged in and could not materialize either,
  so the design was hand-carried back to the operator to paste in (R353). The missing piece was
  never permission, it was a QUEUE. Both kinds are now surfaced to every run and the HANDLER
  decides: a root conversation materializes through D92's preview→confirm as before — whose
  confirm gate asks whether the USER has spoken since the draft, not whether the engine PROCESS
  changed: a later leg answers that by existing, and a blocking `ask_user` answered inside the
  drafting leg answers it too, which the older pid-only test read as silence and charged the user a
  second, content-free "go" after they had already said yes (R1310, `RunContext.user_replies`);
  anywhere else
  the same call writes a proposal to `.control/pending-creations/<id>.json` and returns an
  observation saying plainly that nothing was created and not to re-issue it. The Decisions page
  carries a band of proposals — what would be created, from which routine and run, with the full
  instruction — and one click materializes through the SAME `workflows.scaffold` / `rsched.lanes`
  calls, or discards. **The engine still never writes `routine.yaml`**: the web layer materializes,
  exactly as it applies forever-grants, and that is why the materializer lives there. The proposing
  routine learns the outcome the ordinary way, from an inbox message its NEXT run drains — nothing
  is woken, because a creation is not urgent and a queue that started runs would be a scheduler in
  disguise. Ungated like `report`: a proposal nobody approved creates nothing and reaches nobody but
  the operator's own page, so the approval IS the gate and it is a human. `manage_lane`'s `list`
  still answers directly (it writes nothing — a run that cannot read the lane store cannot
  propose a correct change to it); every mutating verb queues. A within-reply CHILD is still
  refused outright at every depth > 0 and the kinds are not even surfaced to it — a sub-workflow
  must not create routines or reshape lanes as a side effect, since a proposal from one traces
  back to nothing the user reasoned about. The queue is for a run that HAS a user, just not now.
- **Rules** (`library-seed/rules/`, `# rule:` heading, NO requires — lint-enforced): GENERAL
  rules — principle prose a run applies to its own case. ONE copy each, in the library: a routine
  holds SLUGS (`routine.yaml` `rules:`), named in the state digest with the moment each applies
  (`rules.when_lines`, from each rule's own `effect:`) and read on demand with `read_rule`, so revising the
  library text reaches every holder at its next run with no migration and no fork to drift. Nothing
  is copied into a routine dir and no LLM adapts anything at creation — a rule is general by
  construction, so the decompose pipeline only receives the held slugs as an index.
  The SET a new routine starts with comes from its PATTERN (`META.includes`, falling back to
  `DEFAULT_RULES`), not from a judgement made at creation: nothing has read the recipe yet,
  because the recipe is what creation is about to write. Judging a routine's setup is the
  recommender's job (`patterns/recommend.py`, *Recommend for this routine* on the routine page),
  once there is a recipe to judge — and it proposes rather than flips: its answer is a set of
  pending changes, each with its reason, that the user keeps or drops before one accept (D108). The set is changed afterwards ONLY by the user (`rules.py`, `POST /{routines,conversations}/{slug}/rules` — one
  shared impl, which the conversation header uses; the routine page's rules panel rides that page's
  one accept instead, which waits for an active run like every routine config edit): the config
  list is the state, the tail is DERIVED and rebuilt from it. The rules endpoint is deliberately
  not 409-guarded — no run writes routine.yaml, so the web layer is the sole writer; through it a
  newly bound rule even reaches a LIVE run via control.json `add_rules` → `engine/switches.apply_rule_additions` (an
  engine note read from the library, since the prompt is immutable), and an unbind does too, via
  `drop_rules` → `engine/switches.apply_rule_drop` (a note that the rule no longer binds; its
  assists stop with it).
  The TEXT is a separate ownership: `read_rule` is UNGATED (a routine must be able to read what
  binds it, and library prose has no side effect; reading one it does not hold applies for that run
  only) and takes a catalog SLUG, never a path — the name is joined onto the library dir, so a
  path there once read any `.md` on the host past the run's fs jail — while `write_rule` is gated by the **rule-authoring** permission under its own approval
  dial `rule_confirm` — a revision lands on every holder, which is not the decision write_util's
  `confirm` governs. There is deliberately no `remove_rule`: deleting a rule silently un-binds every
  holder with nothing to catch it, so a run reports it and the user deletes it.
  The routine defaults (`DEFAULT_RULES`): `evidence-discipline / ask-policy / fix-the-cause /
  problem-routing / decision-record / web-research` — the conduct every kind of work meets;
  conversations drop decision-record and add `git-checkpoint` (external-repo undo points). The
  rules of a KIND of work are bound by its settings pattern. The shipped inventory of 16 rules is
  the table in docs/rules-permissions.md; where each came from is docs/curated-rules.md.
  Holding a rule IS its on/off switch — an unheld rule contributes nothing — and
  docs/curated-rules.md records each one's provenance, its evidence strength, and the candidates
  REJECTED on evidence (self-critique, anti-sycophancy prose, numeric confidence) so the set grows
  on observed failures rather than folklore. The **rules-review** meta routine owns the layer: it
  reads how runs actually interpreted each rule and revises the shared text from that evidence.
  The five **after-run improvement passes** (bugfix / research / features / UI / efficiency) are NOT
  rules — the **routine-improver** meta routine owns them and sweeps every routine (honoring
  `improve: false`). `DEFAULT_RULES` (config) is the no-LLM fallback selection.
- **Permissions** (`library-seed/permissions/`, `# permission:` heading + machine-read
  `requires:` — the capability keys a doc may presume, no approval dial among them): CONDUCT docs
  of the two-layer permission set. The routine's enforced surface is its own routine.yaml
  `capabilities:` — `actions`, `utils` (an entry is `name` or `name:verb`), the dials
  `confirm` / `rule_confirm` / `remind_confirm` / `config_confirm`, and the levels `runs` /
  `reminders` (the full
  key list with each one's meaning is docs/rules-permissions.md, not restated here) —
  grants.py builds the run policy from it alone, so a doc-without-capability config fails closed;
  a doc's `requires:` names what its instructions presume and drives the UI's activation cascade
  (activating a doc switches its requirements on; on the ability cards a required capability has
  no switch of its own — the doc is the switch — and the server runs the SAME raise-then-floor on
  every path that persists a mapping: save AND
  creation (scaffold, conversation create, the composer's ⚙ payload, the /defaults preview), so a
  mapping never expresses a capability its held docs don't require — from birth, not first edit).
  Both layers user-changeable ONLY; routines can't self-grant. The shipped doc set is 17 files
  in `library-seed/permissions/`, tabled with what each requires in docs/rules-permissions.md —
  ONE list, there. `util-authoring` is the default; the rest arrive with a settings pattern or a
  click. Reservable utils and verbs = the union of all docs' `requires.utils` (library-defined);
  gateable kinds = GATED_KINDS (engine-defined); `runs`, `reminders` and the approval dials are
  SETTINGS, never required by a doc. Permission bodies are SHORT (≤14 lines reach the prompt's CAPABILITIES section
  when held); the Library tab's permission editor has a prefilled, authoritative `requires:` panel.
  Any future permission-ish lever becomes a capability + a `requires:` entry, not a new yaml key.
  See docs/rules-permissions.md. `DEFAULT_PERMISSIONS`/`DEFAULT_CAPABILITIES` (config) are what a
  routine with no list or mapping of its own means; one that holds its own takes on a later
  default as a pending change, never a boot-time write. Historical data migrations are NOT kept:
  each runs once on the production instance and is deleted after convergence — a pre-0.8
  backup converts by booting the matching older tag first. MACHINE-CHECKED: migration code
  must carry a `MIGRATION(expires=YYYY-MM-DD)` marker comment; `tests/test_policy.py` fails
  once the date passes (and on migration-shaped code, or a back-compat alias table, without a
  marker). The grammar and the one reader behind all of it are `rsched.migration_markers`, where
  a marker counts as a DECLARATION only when it opens its sentence — narration about one is
  prose, after a tombstone comment quoting its own marker reddened `main` and blocked every
  release on the host (R2596). It also WARNS — never
  fails — for every marker inside `MIGRATION_WARN_DAYS` (21 days, F628), naming the file, the date
  and the days left: the hard fail is correct enforcement but a binary signal arriving on the
  morning it is already too late, in front of whoever happens to ship that day rather than whoever
  wrote the migration. Six markers in four modules once came due on one single date. Because a
  warning on a passing test reaches nobody, `daemon/marker_watch` files the same window into the
  health stream once a day — `migration_due_soon` / `migration_expired`, both on the blocked-work
  surface, so a deadline is visible without running a gate (F702).
- **Playbooks** (`library-seed/playbooks/<slug>/`, `MAIN.md` + optional on-demand detail files):
  reusable, generalized **conversation briefs** — the in-app analog of the save-instruction /
  use-instruction pattern. A playbook is NOT a workflow (the `converse` workflow stays the harness);
  it only varies the *instruction*. `MAIN.md` front matter is `slug/title/when/tags/axis/updated`
  (`when` = the one-line catalog entry; `axis` = the generalization axis — what varies vs. stays
  fixed); the body is `## Parameters` (with `{{named}}` placeholders) + `## Instructions` + optional
  `## Detailed references` / `## Notes`. Storage + the live catalog are `playbooks.py` (a dedicated
  subfolder reader — NOT single-file `library_docs.py`/`DOC_RE`); git is the library ROOT
  (`libgit.commit(…, paths=["playbooks/<slug>"])`, C1.10). A conversation is SEEDED from a picked playbook (its brief
  becomes `instruction.md`, the first message specializes it, `playbook: {slug, commit}` in
  `routine.yaml` records the binding → `cfg.playbook_slug`). The conversation's **Save as playbook**
  distils a NEW one and **Update playbook** revises the bound one, both from the transcript via the
  `system_model` (`playbook_distill.py` — `PLAYBOOK_SCHEMA`, mirroring `adapt.decompose` and
  the same refuse-to-degrade discipline). `workflows/lint.py` `lint_playbook_text` gates edits; the Library
  tab has a Playbooks section (`web/api_playbooks.py`). Reaches existing instances at boot via
  `bootstrap.sync_seed_library_docs` (subfolder-aware). See docs/playbooks.md.
- **Utils** are self-contained PEP 723 scripts: a docstring header (`<name> — summary`, `usage:`,
  `calls:` (sibling utils exec'd via `gu` — drives transitive secret/net resolution), `tags:`,
  `secrets: NAME,…`, `net: outbound|none`, `fs: roots|none|rw <path>|ro <path>`,
  `informational-exits: 2` (optional — a documented non-zero exit that is an ANSWER, not a failure:
  the declared codes stop counting toward the repeat-failure detector that fires `fix-the-cause`,
  every other non-zero still counts. `engine/exitgate.py`, F711; a routine's own `scripts/` helper
  declares it the same way) — the docstring is the ONLY machine-read surface;
  comment-form declarations above it are invisible), and a `--selftest` the engine runs before
  saving. `write_util` is gated twice: `utils_header.header_problems` rejects a missing `tags:`/`net:`
  line or a credential env var the code reads but `secrets:` doesn't declare (the Settings page can
  only prompt for declared secrets), then the selftest; approval rides the routine's write_util
  `confirm:` capability level. The selftest runs on the candidate OUT of the live path
  (`utils_stage.write_selftested`, F584/D161 — the one transaction both authors share): the
  script is staged in the library under a throwaway `staging--<name>-<pid>-<token>` slug and
  `utils/<name>` is written only on green, so no routine's `gu <name>` can reach an untested
  script while the selftest runs, and no failure path needs a rollback. Staged INSIDE the
  library because a selftest may call `gu` siblings and its declared secrets/net/fs/verb scope
  are read from its own header at that library; hidden from `list_utils` and `gu list`, and
  the prefix is refused as a util name. A header rejection is reported AS one — its own observation head
  naming each violated line — never as a selftest failure (R93), and a failed selftest surfaces
  exit code + both streams head+tail so the traceback's end survives. EDIT MODE (`anchor`/
  `replacement` instead of `content`) patches an existing util's source engine-side — a surgical
  fix never re-emits the whole script (`util name=show --full` returns the complete source) — and
  rides the same approval + staged-selftest gates. The selftest runner prewarms a net:outbound
  util's PEP 723 deps before the timed run (`run_util` prewarms net:none/undeclared ones itself,
  R40/R20), so a heavy dependency tree never spends the selftest timeout on toolchain install.
  A slug with a DELETION in the library's git history is a user
  decision: `write_util` on it is rejected inside the schema-retry cycle until the user allows the
  `recreate:<slug>` access request this run (`authoring.recreate_denial` / `utils_lib.was_deleted`;
  no allow-forever — a fresh deletion outranks any old grant), and the boot
  seed-sync never resurrects a deleted seed util. Discover with the `util` action `name: list`.
- **Every util subprocess is SANDBOXED** (docs/sandboxing.md): `utils_run.run_util` takes a
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
  wrapper itself never degrades — it exits 97).
- **Secrets** are one central, write-only KEY→VALUE store (one `KEY=VALUE` line each;
  a value with newlines — an SSH private key — is JSON-quoted onto its line, so PEMs round-trip); a util subprocess receives ONLY the vars
  it (or a `calls:` sibling) declares — undeclared store keys are scrubbed even from inherited
  daemon env, in every sandbox mode. Endpoints and the subscription read the store as before; the
  UI flags unset declared vars.

## Ownership, concurrency, restart (daemon/)

- The **engine subprocess** owns `runs/<ts>/*`, `status.json` (atomic, single writer), and git commits in
  its routine dir. The web's own routine-file editor (`web/api_routine_files.py`,
  `PUT /api/routines/{slug}/file`) edits the RECIPE and the routine's own notes and REFUSES
  `routine.yaml`, `state/finish-line.json`, `.memory/INDEX.md`, `.git/`, `runs/`, `inbox/` and
  `questions/` by name, each refusal naming the endpoint or
  owner that holds the file (`api_routine_files.NOT_EDITABLE_HERE`); the shared config-field validators (budgets, models with the
  window-fit refusal, connections, machines, folder roots, tags) are `web/config_fields.py`,
  called by both the routine and the conversation PATCH.
  The **daemon** writes `inbox/` plus the closeout of orphaned runs, retention
  (delete/gzip old run dirs), detached-task delivery (artifacts + `state/background.json` on the
  owner), and the `.control/` spools/ledgers. The **web layer** edits routine config only when no
  run is active (409 otherwise) — deliberate live-edit exceptions: conversation settings and rule
  bind/unbind through the rules endpoint (control.json `add_rules` / `drop_rules` tell the live
  run; the conversation header uses it, the routine page's one accept does not); web-side routine-dir commits take the
  engine's per-repo commit lock. The routine page's accept keeps that 409 (it writes three
  owners at once, so half of it is neither the old settings nor the new) but no longer DROPS the
  attempt: it is held as one record per routine carrying the active run's expected end — its
  start plus `max_wall_clock_min` — the settings page renders it as waiting and says when it can
  land (in the banner's own `role="status"`, never the error toast: a held accept is a state and
  not a failure, F761), and at run end a background drain replays it through the accept endpoint itself or
  EXPIRES it because one of its fields was decided elsewhere meanwhile (D178/F668,
  `pending_accepts.py` + `web/held_accepts.py`).
- The daemon (`scheduler.py` + `runner.py`) fires cron through **`rsched/firetimes.py`** — croniter
  underneath, and the ONE place a fire instant is computed: the fire table, both catch-up paths and
  the week view read it, so the strip draws what fires. It adds the rule croniter lacks (Vixie
  cron's): a cron whose minute and hour fields are both fixed names WALL-CLOCK times and fires
  once when daylight saving ends repeats the hour; a wildcard minute or hour is an INTERVAL and
  fires through both passes. The daemon spawns one `engine-run` subprocess
  per routine (never two of the same at once) under `max_concurrent_runs`; a run that blocks on a user
  question **releases its slot** (a PAUSED run too). **One place decides what refuses a fire and
  names it** — `Runner.fire_blocker`, the shape `resume_blocker` already had: `fire` consults it
  and so does the console's Run-now route, so a refusal can never be reported with a cause that is
  not the real one (it told the operator "already has an active run" whatever had actually
  happened). Its causes are `disabled`, `draining`, `overrun` and — F643 — **`stale_role`: a
  routine whose `models.main` names a model the catalog no longer serves is refused BEFORE a run
  dir is claimed.** `trials.roles_problem` is the predicate, shared with `rsched validate` and the
  config write edge — but the fire guard asks it only about `FIRE_BLOCKING_ROLES` (`main` today),
  **deliberately narrower than what `validate` reports**: the loop resolves `main` on turn one, so
  a stale name there means the run cannot reach its first turn, while `tool_call` and `uncensored`
  resolve only if the run makes such a call (`uncensored` only when a refusal is referred).
  `folder-reorg` carried a stale `uncensored` for weeks while every one of its runs finished fine;
  a fire guard over all roles would have taken a working routine dark to prevent a death that was
  not happening — a refusal must not cost more than the failure it prevents.
  **A name that resolves is not yet a chain that can fail over** (R2196, `rsched/conformance.py`,
  reported by `rsched validate`). `roles_problem` asks whether the catalog still carries the name;
  conformance asks the next question, offline and with no network call: does the role's chain have
  a reachable fallback at all, and do its members sit in more than one FAILURE DOMAIN? A domain
  has two independent axes, and sharing either one is fatal — the endpoint's `base_url` (one host,
  one outage) and its `key_var` (one credential, one expiry). The second axis is what happened on
  2026-09-14: `Astra high`'s only fallback was `Opus high`, both transported by `cliproxy`, so the
  chain read like a failover and was not one, and eight routines died at turn 0 within two minutes
  on a single expired subscription token. 0.344.0 then made resolution transitive so "the
  reachable set finally matches what a reader of `config.yaml` would already assume" — this is the
  same gap measured one layer out, over the instance rather than the code. The walk mirrors
  `resolve_chain`'s (breadth-first, declaration order, skipping self-references, duplicates and
  unresolvable names) but over NAMES alone, so it needs no registry and survives a broken config
  rather than raising on it. Unlike `stale_role` it blocks nothing and fails no command: whether a
  single-domain chain is acceptable depends on how many providers this instance has, so it is a
  `NOTE  failover ·` line and the operator decides. The failure it ends: `library-sync` and `tv-show-tracker-seedbox-manager` each
  carried a bare `Sonnet` stored when that was a catalog name, and after the rename each run died
  on its first turn resolving it (`EndpointError`, rc=1, no finish, no summary, an `orphaned_run`
  and nothing saying why). A run that cannot reach its first turn is now never born, and the
  refusal is recorded as `fire_refused` for EVERY fire reason — unlike an overrun or a drain, which
  are normal outside the cron path and recorded for it alone. **The spawn is an explicit contract, not an
  inheritance** (F394): the child is a fresh interpreter that gets none of the parent's
  configuration, so `runner_state.engine_cmd` passes `--config <the config the daemon loaded>` and
  `--homes <registry.homes_fingerprint>`, and `engine-run` — which defaults NEITHER — refuses the
  run when the named config resolves to different homes. A MODEL TRIAL's run is told its models
  the same way, `--model role=name`, read from the run dir's own `trial.json` on every leg
  (`rsched/trials.py` arms it at the fire, so a resume keeps the trial its run started under). Without that, a caller holding a config
  it built rather than loaded (every test) spawned an engine that read `~/.config/routine-scheduler/config.yaml`
  and executed against the live instance's homes, endpoints and money. A spawner whose config was
  never loaded from a file is refused in `engine_cmd`, before a process exists. **`rsched/registry.py`** (a shared read-model,
  NOT daemon-owned) derives the catalog and run-index live from the filesystem every rescan — no
  database, no cache files; parsing is memoized per file behind a stat() fingerprint
  (inode+mtime+size, so atomic rewrites always miss), pruned for deleted dirs, copies returned —
  the disk stays the source of truth on every lookup. Every other derived view lives in
  **`rsched/readmodels/`** (stats, run_health, util_stats, statemap, fileactivity, subruntree,
  items — the maintenance index of findings/decisions/bug reports, docs/items.md — summaries —
  each routine's latest finish message, shaped as a fourth item type and merged into the same
  page — and messages — a routine's four message folders, docs/messages.md) on the
  same discipline: `readmodels/memo` fingerprint-caches per input file and makes every miss
  SINGLE-FLIGHT per key (0.340.0: a burst of identical requests — every open tab refetching one
  endpoint on a bus event — computes once while the rest wait and re-read), `readmodels/usage_stream`
  is the ONE parser of workflow-usage.jsonl and `readmodels/health_stream` the ONE parser of
  health-events.jsonl (plus the blocked-fleet fold behind `GET /api/health/blocked`: the 18
  events that mean WORK THAT WAS DUE DID NOT HAPPEN — `fire_refused`, `lane_fire_refused`,
  `fire_paused`, `routine_catchup_declined`, `lane_fire_paused`, `lane_catchup_declined`,
  `lane_chain_stopped`, `lane_chain_member_skipped`, `scheduler_tick_error`,
  `finish_gate_failed`, `trigger_capped`, `commit_failed`, `backup_stale`,
  `migration_expired`, `migration_due_soon`, `obligation_overdue`, `wrong_turn_detected`,
  `model_chain_exhausted` — one row per (event, subject) with a count and the newest
  detail, because none of them can reach a run page, Items row or Stats slice; the count and
  the names are pinned by `tests/test_chain_exhausted_blocked.py`) — a read-model is a pure
  derivation, deletable state, never a writer. The Decisions read model (`web/decisions_read.py` — what `/api/questions`, the
  badge, the tab-open notifier and Web Push all read) is memoized per home on the same fingerprint
  over the catalog's own sources (the home listing, every `routine.yaml`, every `questions/pending`
  and `inbox` dir, every `runs/` dir and run `status.json`), so a warm call is a stat pass and never
  a `registry.scan`. `registry.info(server, home, slug)` answers for ONE directory off the same
  four memos — exactly what `scan(server, home).get(slug)` answers, without walking the home to
  answer it. A row for an ANSWERED question whose record carried a `config_patch` also states
  whether the patch was applied (`config_patch_applied` — `rsched/patchreceipt.py` owns the field
  names and the wording). That one fact has three readers and one writer: the Decisions page's
  settled card, `config-optimizer` (which reads the API rather than the prompt), and the asking
  RUN, which gets it on its answer pair (`engine/inbox_questions.collect_deferred_answers`) and
  reads it as a line of its state digest (`engine/composer`). It is true in both directions
  because the `approve & apply` button is what declares it — `config_patch_applied` on the answer
  POST, or `POST /api/questions/{qid}/patch-applied` (`web/api_patch_applied.py`) when the patch
  is applied to a card that was already answered. A question still OPEN that carries a patch says
  so too, and names the control that would apply it (`patchreceipt.pending_suffix`, on the
  digest's open-questions line) — the operator's ruling of 2026-10-23: a prose answer never
  applies, but what the run reads names the one-click action that would. Deliberately not a
  verdict: APPLIED/NOT APPLIED judge an answer that exists and a pending patch is neither.
  Answering still never applies a patch (R2189);
  R2778 is only about the asking run being TOLD which happened, after one read an affirmative
  prose answer as approval and planned 600 turns against a live 400-turn ceiling. A row for a
  DECLINED decision says so the same way — `declined: true` + `decision: "declined"` (+ `reason`),
  derived from the answer file by the same `_mark_answered` and owned by `rsched/decline.py` —
  with the same three readers: the settled card renders a red `declined` chip IN PLACE of the
  "answered" one, and the asking run gets the value on its answer pair and a digest line that
  says REFUSED in words it cannot read as a go-ahead. Every
  `/routines/{slug}/*` and `/conversations/{slug}/*` handler takes that
  path; a name the directory does not answer to (a routine.yaml whose `slug` disagrees with its
  dir, a name that is not a slug at all) falls back to the full walk, which is also what keeps a
  URL segment from being joined onto a path.
- **One-shot time triggers (schedule-once, docs/schedule-once.md)**: a spool
  (`.control/schedule-once/<slug>/req-*.json`) armed from the routine page or by a run holding
  `scheduling` (the `schedule_run` action); the daemon's `OneShotManager` fires each due request
  ONCE then CONSUMES the file (auto-deactivate = deletion). A conversation's self-armed one-shot
  is namespaced `conv--<slug>` and wakes the conversation by RESUMING its run ("remind me in 3
  days"). Cooldowns/expiry per request; corrupt requests are dropped, not rescanned.
- **How routines relate to each other — three axes** (docs/lanes-tags.md has the full
  argument). A **LANE** decides WHEN a set of routines fires and in what order; a **SHARED
  STORE** is which files a set of routines reads and writes together, plus the notes channel
  between them; **`tags:`** says what a routine is about and carries no behaviour. They are three
  objects rather than one because they have different cardinalities and different owners. The
  temporal axis is the strictest — cron exclusivity is a hard fact, since a routine in two
  scheduled lanes fires twice — so anything sharing a record with it inherits that cardinality:
  "these five routines share a store" could not be said without also saying "and they fire
  together"; moving a routine to another clock would silently change what it may reach. A
  timing decision would be a permissions decision, with nothing to say so. What a routine may DO
  is none of the three: it is the routine's own routine.yaml, read against the settings pattern
  it follows (docs/patterns.md).
- **Lanes — the temporal axis (D53/D61/D71/D80/D90)**: a lane is an ORDERED list of member
  records — `{"slug"}` — plus a mid-chain-failure policy and, optionally, a cron of its own,
  stored instance-level in `.control/lanes.json` (`rsched/lanes.py`). Ownership mirrors
  `rsched/triggers.py` and `rsched/schedule_once.py`: the order several routines fire in belongs
  to no single one of them, so a lane is daemon-owned instance state — **the web RECORDS, the
  daemon FIRES** — and it CANNOT live in a routine.yaml. CRUD is web-written via
  `web/api_lanes.py` and the Routines page's lane rows (D80: there is no standalone subpage —
  the rows carry run-now/pause/edit, the toolbar above the list creates lanes and holds
  the instance default, and the editors are overlays in `static/components/lanemanage.js`), or
  the `manage_lane` action from a root conversation — including the lane's own cron (R312), so
  lane scheduling needs no operator round-trip. **A routine belongs to AT MOST ONE lane and that
  is ENFORCED**; a lane carries no config and no store, so DELETING one returns its members to
  their own crons and changes nothing else about them.
  A chain fires ONCE, every member in order. A flow with an inbound and an outbound end BRACKETS
  the lane (D90): a dedicated inbound-router member placed first in the order and a dedicated
  outbound-sender member placed last, two single-purpose routines rather than one routine run
  twice.
  "Run lane now" — or the lane's OWN cron (below) — ARMS a
  sequential chain (`rsched/lane_runs.py`, one in-flight chain per lane, snapshot of member
  records + resolved policy at arm time); the scheduler-ticked **`LaneRunManager`**
  (`daemon/lane_runs.py`) advances it one transition per tick: fire the member at the cursor, wait
  for a terminal state, then per the outcome and `on_failure` (`stop` aborts the rest — any
  non-`ok` outcome counts as failure, a missing/disabled/crashed member too; `continue` fires on)
  fire the next. The chain's end emits a `lane_chain_done` / `lane_chain_stopped` health event;
  a member it cannot fire emits a `lane_chain_member_skipped`.
  - **The lane schedule (D71)**: a lane may carry its own `cron` (+ the server `tz`, written by
    the web beside it — the lane editor has the same friendly editor a routine's schedule uses;
    web RECORDS, daemon FIRES). The scheduler arms the chain on the lane's cron — member 0 fires,
    the rest chain on completion — and while a routine belongs to a SCHEDULED lane its OWN cron is
    SUPPRESSED from the fire table and boot catch-up (one fire path, no double-firing); its
    Schedule dropdown renders a locked "lane managed" state linking to the lane, the routines
    overview shows the LANE's schedule on the member's row instead of the suppressed cron
    (R313 — `/api/lanes` ships each lane's `schedule_desc` for it). The routine card says the
    same to every other reader: `/api/routines` and the detail payload report a member's
    `schedule_desc` as `Lane “<name>” — <the lane's schedule>` and its `next_fire` as the
    lane's (`lanes.scheduled_lane_by_member`), none while the lane is paused or the member is
    switched off — the routine page's status tile and a routine reading the API both see when
    it actually runs. Clearing the lane's schedule (or leaving the lane) restores the member's
    own cron at the next rescan.
    A lane fire due while its chain is still in flight is REFUSED (`lane_fire_refused`, the chain
    analog of `overrun_skipped`). Manual "Run now" on a member is unaffected.
    **Lane catch-up (0.329.0).** The fire table is process memory, so a fire that came due while
    the daemon was down, restarting or draining was lost outright — and with every member cron
    suppressed, nothing else fired those routines (a Tue/Thu lane lost both of one week's fires
    that way, unnoticed). Every `lane_runs.arm` stamps a per-lane watermark
    (`rsched/lane_fires.py`, `.control/lane-fires.json`, daemon-owned derived state), and so does
    the scheduler when the operator's global pause skips a due lane fire on purpose: every path
    that HANDLES a fire moves the watermark, so what boot finds unstamped is a fire nobody was
    there for. Boot
    (`daemon/lane_catchup.py`, beside the routine `boot_catchup`) arms ONE make-up chain
    (`armed_by=catchup`, health event `lane_fire_catchup`, whose detail asserts no cause — only
    what the watermark shows) for each scheduled lane whose last due
    fire is newer than its watermark — one, never a backlog, the same bound as a routine's
    `catchup: run_once`. The policy is the lane's `catchup` field (`run_once` by default, `skip`
    to let a missed fire go; the lane editor and `PATCH /api/lanes/<id>` set it). A lane with no
    watermark yet is stamped and left alone, so the first boot after the upgrade fires nothing.
    A fire the operator's PAUSE dropped is made up for a ROUTINE too, by the same ratio
    (D183, `daemon/routine_catchup.py` + `rsched/routine_fires.py`): the skip stamps the fire
    as owed, the resume and the next boot make up at most one per routine while less than a
    quarter of that routine's own cadence has passed, armed as a one-shot so the daemon still
    owns spawning, and a decline files `routine_catchup_declined` carrying both numbers. It is
    NOT gated on the routine's boot `catchup` field: that field is about a daemon that was
    down, where many fires may have been missed, while a pause-skipped fire is one the daemon
    was up for and the operator's own pause dropped — every routine on this instance reads
    `catchup: skip`, so gating on it would make the rule a no-op. Measured 2026-10-14: four
    consecutive dropped fires of `scheduler-builder` (`0 6,14`, in no lane) with nothing in the
    health stream to say so, while the six lane fires the same pause ate were each on record.
    The dashboard's week strip renders the same rule: `/api/schedule/week` withholds a
    suppressed member's cron fires (drawing them would show runs the daemon never fires) and
    ships the lane's own fire times instead; the strip draws a scheduled lane as ONE labelled
    row whose members chain end-to-end at each lane fire (member order), each segment sized by
    that member's average runtime.
    The strip's bars also DRAG (weekgrid-drag.js): onto a sibling bar to reorder the lane,
    onto another lane's row to join it, onto the remove strip to leave it, and along their
    own row to re-time the schedule — the lane's cron on a scheduled-lane row, the
    routine's own otherwise, both through the same `schedule.friendly` PATCH the editors use
    (custom crons have no draggable shape and are refused with a pointer to their editor).
- **Shared stores and the notes between them (D67/F335, `rsched/sharedstores.py`)**: a shared
  store is a directory directly under `.control/group-stores/`; a routine SHARES it by naming
  that directory among its own `fs_write_roots` — an ordinary per-routine setting, user-only like
  every other key there and written by no run. Nothing else records the sharing:
  `sharedstores.sharers()` reads the routines' own files (memoized per file on its stat
  fingerprint), so it cannot disagree with itself and a routine deleted from disk stops sharing by
  construction. A root ABOVE the stores (a meta routine that may write the whole routines home)
  covers every store and shares none. The store reaches a run as an ORDINARY write root —
  nothing injects it; children inherit it with the parent's roots. The harness contract
  lists each store the routine shares with the OTHER routines sharing it; it also states the
  collision semantics: writes are whole-file atomic and LAST WRITE WINS PER FILE, so the sharers exchange
  files under per-routine names (`<slug>-<topic>.md`) and treat shared files as read-mostly. A
  store is also the natural home for what an inbound-end member stages for the outbound-end
  member that follows it in a lane chain (its own `state/` works too). The directory name and
  the `grp-` store names are DELIBERATE: routines address these paths in their own memory — one
  live routine holds `READ /control/group-stores/grp-8bfd2aa6/…` as a standing prevention rule it
  wrote for itself after an incident; several more name a store in a ledger — so moving a store
  would silently falsify agent-authored notes instead of failing loudly. A store's name is an
  OPAQUE handle nothing parses.
  - **Notes (F335)**: the light channel between routines sharing a store. A NOTE is
    coordination; a REPORT is work somebody must ACT on, tracked until answered — and `report`
    keeps that meaning unchanged. A routine writes `<store>/notes/<to-slug>/note-*.json` with an
    ordinary file write (**no new action kind**); the engine renders the waiting notes — from
    every store the addressee shares — into its state digest at boot and DELETES them as it
    reads, mirroring how `inbox/` drains, so a note is delivered exactly once and never becomes a
    backlog someone has to clear by hand. The harness contract names the convention beside the
    stores and LISTS the other sharers — a channel a run does not know about is a channel that
    does not exist. **No approval, no ledger row, no Messages-page item**: a note lives in a
    store only its sharers can write. A note addressed to a routine that does NOT share the
    store would never be read, so the engine's write gate refuses it (`engine/fileops._write_gate`
    → `recipients.note_refusal` → `sharedstores.note_refusal`, for write_file / edit_file / mkdir
    / a move's destination), naming the routines that do share it and an addressed `report` as
    the channel that reaches the one it meant — and so is a note to a sharer that starts no run,
    with the sharers that do read suggested instead; deleting a stranded note stays allowed. Sharing is read LIVE from the
    routines' own files, so a routine that drops the root loses the channel in both directions at
    once. Delivery never starts a run: the addressee picks its notes up when it next runs, which
    for a lane chain is the same chain or the next.
- **Config changed while a run is LIVE** (`rsched/configflow.py`, F337). A run reads
  `routine.yaml` at boot and composes its prompt once, so a mid-run config edit lands on disk with
  the run unaware — except for the ad-hoc live paths the system had grown (an access-request
  decision bridges into the live policy via `engine/requests.py`; the `/rules` picker pushes an
  added rule through `control.json`). So "I changed it while it was running" meant two different
  things depending on the field, and the run was never told either way. The fix is not more live
  paths: it is ONE classification table, `configflow.CLASSIFICATION`, mapping every `RoutinePatch`
  and `ConversationPatch` field to **LIVE** (the engine adopts it at a turn boundary — `budgets`,
  `deliberation`, `grants`) or **NEXT_RUN**, each with the reason the operator is shown.
  `tests/test_configflow.py` fails on a patch field the table does not declare, so a new config
  field cannot be added without deciding which half it is in — that guard IS the anti-drift
  mechanism. The table is the UNION of both models, so it is not what a decision's
  `config_patch` is checked against: `configflow.ROUTINE_PATCH_FIELDS` and
  `CONVERSATION_PATCH_FIELDS` are each model's exact field set (pinned one-to-one by the same
  test file); `engine/config_bridge.patch_shape` refuses, on the turn the ask is filed,
  any key outside the surface the apply will PATCH — a conversation's own proposal (the record
  lands in a conversation) against `ConversationPatch`, everything else against `RoutinePatch`.
  **A THIRD surface is the INSTANCE's own config** (operator, 2026-10-19), named by the patch
  as `{"server": {...}}` and applied by `PATCH /api/settings/server`
  (`web/settings/serverpatch.py`). Its vocabulary, `configflow.SERVER_PATCH_FIELDS`, is an
  ALLOW-LIST rather than a model's field set: it names only fields with a validated runtime
  writer behind them (the sandbox mode and the other runtime knobs, and the single-name
  POINTERS into the catalogs), so the auth tokens, the bind address, the homes and the
  credential-carrying catalogs are sealed by omission — and a field added to `ServerConfig`
  tomorrow is sealed on the day it lands. `config_bridge.server_shape` refuses a stray key, an
  empty body, and a body MIXING `server` with routine fields (one patch, one apply route: a
  mixed body would half-land and settle the decision as handled). The allow-list is enforced
  again IN the apply route, which is the half that makes it a boundary rather than a caller's
  restraint. Before this surface existed a run holding the operator's explicit approval for an
  instance-wide change had no mechanism to land it at all, and two approved changes sat 10 and
  3 days unapplied (R2702).
  **A patch the routine's OWN `config_confirm` dial permits is APPLIED without a click**
  (D191's third step). The engine still writes no config: the ask seam
  (`engine/autoask.taken`) judges the dial, QUEUES the request
  (`rsched/autoapply.py`, the spool idiom of `pending.py`/`pending_edits.py`) and tells the
  asking run on that turn, so it never waits for an answer that is not coming; the WEB layer
  drains the spool in a background loop of its own lifespan (`web/autoapply_drain.maintain`)
  and applies through `api_routine_patch.apply_updates`, the one validated writer — so
  `daemon/` still imports nothing from `web/`. The dial is read AGAIN at that boundary from
  the routine's current config: a request queued while the dial permitted it does not apply
  once the dial is down. Four things are never autonomous whatever the dial says: the SEALED
  set, a patch naming another routine, the `{"server": …}` surface (a per-routine dial cannot
  speak for the instance), and an ask that also needs an ANSWER (options, blocking, or a typed
  access request) — each keeps its decision card. A refusal at the boundary, a failed apply
  and a full spool all fall back to the ordinary card, because a config change that neither
  applied nor asked is the one state this surface must never reach.
  One VALUE is judged there too: a `trial` (`config_bridge.trial_value` — its shape and its
  models' catalog membership), because a model trial is proposed for routines the proposer does
  not run, and its refusal teaches the shape. `"trial": null` clears one: `patch_routine` keeps
  that one explicit null, which its `exclude_none` dump would otherwise read as "not sent".
  Both PATCH handlers call `routines_common.signal_config_change`, which writes a
  `config_change` signal into the live run's `control.json` (the seam that already exists for
  reaching a running run); `engine/switches.apply_config_change` adopts the live half at the next
  turn boundary and appends ONE `ENGINE NOTE` naming EVERY changed field and which half it is in,
  with a `user_injection` transcript event beside it — so the change is in the conversation the
  model can see, never a second invisible mutation path. Edge-triggered through the same
  applied-ts ledger as the model/deliberation/rule switches (a resumed leg never re-fires a stale
  signal), and per-field best-effort: a value the run cannot use is logged and left for the next
  run rather than ending a live run, and the note still says the field changed.
- **What DONE means — three questions, three owners** (reports/goal.md; operator orders
  2026-08-14 and 2026-09-05). A run should stop on a MEANING-level condition, not only on budget
  walls; a routine should be able to finish for good. The two questions have different
  owners; a third one — what a run must never do — belongs before the action:
  - **What one finished run leaves behind** is the recipe's `## Done when`
    (`engine/donewhen.py`): one `- d<n> · <stage> — <outcome>` line each, in `main.md`, where the
    design of a run already lives and changes (the improver, a recipe revision, `write_recipe`).
    It is re-asked every run and never "already met". A run the operator starts by hand with a
    one-line BRIEF (`engine/brief.py`) answers for its GOALS instead (next item).
  - **What a person asked of THIS run** is its GOALS (`engine/goals.py`, the `goal` action in
    `engine/goalops.py`): `b1`, `b2`, … — the brief seeds `b1`, and the run transcribes anything
    a person asks for while it runs. ONE mechanism for routines and conversations, because the
    rule that kept a run from setting its own goals is the one it enforces: the run is the
    SCRIBE, never the author. Every goal carries `quote`, the person's own words, and the engine
    finds them in what the person wrote to the run (the brief, a conversation's first message,
    every user-channel message and answer — never a report or another machine `via`, never the
    routine's recipe) or refuses the goal; rewording or dropping one needs words written AFTER
    the ones it came from. `check` meets the finish's claim judge. The ledger lives in the
    transcript (each goal observation's `ledger`, a finish's `stopping_update.goals`), so a
    resume, a rewind and a branch read it back exactly. What differs by mode is only WHEN a goal
    must be met: a routine run's main finish accounts every open goal met or unmet; a
    conversation is one continuous run whose goals outlive each reply, and a reply that declares
    `final: true` must meet every open one (`final: false` hands back with them open).
  - **When the ROUTINE is done for good** is the operator's FINISH LINE
    (`state/finish-line.json`, `engine/finishline.py`): zero or more OUTCOMES, all of which must
    be reached, plus an optional `until` date after which it stops either way. Every outcome
    names its JUDGE — `date` (the calendar decides, no run involved), `run` (a run claims `met`
    with evidence, checked against its transcript) or `you` (a run reports the distance and may
    never claim it). The web (`web/api_finishline.py`, the settings page's one accept) is its only
    writer for what a person sets; no run writes the file (`fileops` seals it by path). The
    engine stamps only what a run's finish reported: a distance per open outcome, `met` on an
    outcome the run proves.
  - **What a run must never do** is held before the action by permissions, rules and reminders;
    what none of them can hold is a line of the recipe's `## Never`.
  THE ACCOUNTING (`engine/accounting.py`): a depth-0 main finish carries an `accounting` FIELD —
  one entry per Done-when line (`d<n> met|unmet|not due: <why>`) and per open outcome
  (`g<n> distance: <what remains>`, or `g<n> met: <evidence>` where the run is the judge). The
  finish gate sets aside a finish whose accounting is missing a line, bare, a PLACEHOLDER, or
  claims what its
  line cannot be (the R108 one-extra-turn shape; the reserved-finish turn and a follow-up reply
  after the run ended owe nothing). It checks presence, shape and a content FLOOR, never
  semantics — a field, not
  prose in the summary, because verdicts read back out of prose were lost one time in six.
  The floor (`accounting.is_placeholder`) refuses an evidence clause of fewer than two words,
  or one made only of words a real account never consists of alone (`test`, `tbd`, `n/a`,
  `ok`, …), naming the offending entry verbatim so the model sees what it wrote; it is
  calibrated on the fleet's own entries — the shortest LEGITIMATE ones are two-word `not due`
  accounts like `no filing`, which is why the rule counts words and not characters (F748:
  `birthday-admin:20261022-080004` finished `ok` carrying `d1 met: test`). Whether a `met` is
  TRUE is the claim verifier's separate question. The
  accounting lands in the run's `status.json` (the runs table, the dashboard), the finish line's
  distances and one `stopping_update` transcript event; the next run's digest carries the finish
  line and what the last run left `unmet`, said ONCE at boot and never per turn
  (`engine/finish_digest.py`).
- **Retirement — a routine that finishes for good** (`engine/goalreached.py`, operator order
  2026-09-05: routines want "the ability to disable themselves once they think they reached it").
  Reaching the finish line has a consequence, arranged so that nothing writes config.
  `registry.RoutineInfo.retired` is DERIVED from the finish line (memoized on its file and the
  date); the scheduler builds no fire-table entry and makes up no missed fire for a retired
  routine; a lane chain records the member `outcome: "skipped"` and moves on. That is the whole
  of "disabling itself" — editing the finish line puts the routine back on the next rescan;
  `enabled` is untouched. Making it permanent is a CLICK: ONE `goal-reached` card on the
  `pending.py` bridge, queued by whichever party completed the line — the finish that proved the
  last run-judged outcome, the scheduler's tick when a date or `until` passed, or the operator's
  own save of an outcome only they judge (deduped, because a reached line stays reached).
  Approving writes `enabled: false` through `patch_routine`, the one config writer; declining
  reopens the met outcomes so the routine resumes — except for a line the CALENDAR reached, which
  cannot be declined (reopening changes nothing a date decides): its date is changed on the
  routine page; that save withdraws the card. Doing nothing leaves it stopped with the card
  standing, which is the honest third state. A retired routine reads as `finished` on the
  dashboard and the routine page — distinct from `disabled` — and its setup surface carries a
  NOTE saying so, with no remedy: a diagnosis offering to undo a finished job reads as a defect
  report on a routine that did exactly what it was for. Lane membership and roots are left
  alone, so reopening costs nothing.
- **Recipe revisions — a run proposing a change to its OWN recipe** (`engine/recipeproposal.py`,
  D200, operator requirement 2026-10-19: *"I want recipes to suggest a change in their recipes
  themselves when they finish"*). The home is the ENGINE FINISH SEAM, not a general rule (which
  reaches only routines whose config holds it) and not per-recipe text: every finishing routine
  run is ASKED, in the digest's FINISH block beside what its accounting owes
  (`engine/finish_digest.py`) — modelled on D195's known-flaw prompt, which likewise asks for
  something a run would otherwise never volunteer. The ask is NOT in the harness contract: that
  body is the always-on engine contract held to a shrink-only budget
  (`tests/test_prompt_budget.py`), where new prose is paid for by cutting prose.
  The ANSWER rides the run's own summary, one `RECIPE REVISION: <file> — <change>` line per
  revision, exactly as D195's flaw answer rides prose rather than a new action field. Because a
  scheduled run holds no `write_recipe` (the decision row's constraint: vodafone-outage-claims'
  own `main.md` d17 asked this of itself on 2026-10-20 and the engine refused the edit), the
  lines become ONE `recipe-revision` card on the `pending.py` bridge, deduped on the lines
  themselves — the same gap noticed next run is one card, new reasoning is its own. Approving
  (`web/api_recipe_revision.py`) does not write the recipe from out here: the proposal is prose,
  the reasoning is in that run's transcript, so it ARMS the unlock that already exists
  (`engine/revise.py`) and resumes the proposing run with its recipe writable — the "grant the
  capability at that moment" half of the same constraint, through one unlock path rather than a
  second that can drift. A run that ALREADY holds recipe self-write is asked the same question
  and files no card: it makes the edit, and the autocommit carries it. The card's lines are
  EDITABLE before approval (F790, operator requirement 2026-10-27: *"we need an edit option for
  the proposed revisions to a routine's own recipe at the end of its run"*) — prose has no patch
  to adjust, so rewording the lines IS editing the proposal, and the reworded lines ride the
  approval as a body (`Edited`, the optional-body shape of `api_pending.materialize`'s `Choice`),
  capped server-side at `MAX_LINES` because a cap the card enforces is not a cap, and an edit
  cleared to nothing is refused rather than falling back to wording the operator deleted. The
  instruction has a SECOND framing for that branch: a leg told his sentence is its own cannot see
  where the two differ, so it would implement and defend wording it never chose. Discarding asks
  WHICH of two true things happened — not wanted, or *already made by hand* — because
  `pending.notify_proposer` quotes the reason verbatim to the routine, and the single old reason
  ("the revision was not wanted") was a lie in the second case, from which its next run then
  reasoned.
- **Checking the claims** (`engine/verifier.py`). The accounting proves a run ANSWERED for every
  line; it cannot prove the answer TRUE, so a run could write `d3 met: PDF verified` having never
  opened the PDF. Each `met` is therefore checked once, by a second call on the `tool_call` role —
  a fresh context, and a second MODEL only when that role resolves to a different one (an unset
  role runs the system model, as an unset `main` does; `rsched validate` names a routine whose
  verifier shares its main model as `NOTE verifier`) — asked whether the run's own transcript
  tail supports each claim. A line
  whose producing stage this run never entered carries that as a HINT the judge reads under the
  claim (`verifier.hinted`, F620) — never a verdict of its own: stage entry is detected from
  module reads and the run's phase cursor, which a run that did the work in full can lack. The design
  is dominated by the two ways this could be worse than the problem:
  - **False blocks** — a judge that blocks on doubt strands finished jobs over evidence outside
    the tail it was shown. So it is FAIL-OPEN at every level: an unavailable endpoint, an
    unparseable answer, a claim the judge did not mention, and anything short of an explicit
    `supported: false` all ACCEPT; the prompt says absence of evidence is not evidence of absence
    and to be generous. `unmet` and distances are never judged; a finish that claims nothing
    met pays no subcall at all.
  - **A livelock** — a stubborn model and a stubborn judge would trade refutations until the
    budget dies. So a line is challenged AT MOST ONCE per run (`loop._challenged` — per reply in
    a conversation, and rebuilt on a resume from the deferrals' `claims_unsupported`, so a
    restart does not reopen the argument): the finish is
    set aside one turn with the objection and how to overrule it — CITE THE ACTIONS, which turns
    produced the outcome and the identifiers that match, and `do not concede a line you did the
    work for`, because a run that read the objection as unanswerable filed a false `unmet` about
    its own finished work and a false `unmet` is worse than a missing one (F620); if the model
    re-asserts the same verdict it STANDS. The disagreement is then recorded — `disputed` on a finish-line
    outcome and in the `stopping_update` event. The engine gets one intervention, the model keeps
    the last word, and the operator gets the audit trail.
- **Event triggers fire through the same seam** (docs/triggers.md): the webhook route
  (`web/api_hooks.py`, POST `/api/webhooks/<slug>/<token>`, with `/api/hooks/...` served as a
  deprecated alias whose accepted calls file a `deprecated_route` event — the ONE
  unauthenticated API route:
  constant-time token compare, generic 404, 64 KiB cap, rate limit + spool cap, rejections logged,
  payload never echoed) only RECORDS events durably in the `.control/triggers/<slug>/` spool
  (request-file idiom, like restart.request); the scheduler-ticked **`TriggerManager`**
  (`daemon/triggers.py`) turns them into fires — the trigger analog of overrun is QUEUE, not skip:
  N events while a run is active/queued/cooling coalesce into ONE fire, each event still landing as
  its own inbox message for that fire (deterministic filenames → exactly-once across crashes).
  `cooldown_s` per trigger (default 60; 900 for a report trigger, `triggers.default_cooldown`)
  bounds trigger-fire frequency, so a leaked URL can't burn
  budget; `state.json` in the spool is the daemon-written fire ledger the Triggers card renders.
- **API auth is two-tier (R94; operator decision 2026-08-05: ENFORCE — superseding D68's
  earlier "leave as-is")**: the PRIMARY bearer (`config.yaml token:`) is the human/web
  credential and authorizes everything; the ROUTINE bearer (`routine_token:`, generated by
  `bootstrap.ensure_config` when absent — an existing config gains it at the next daemon
  boot) authorizes read-only methods plus an explicit non-config allowlist
  (`web/app.py ROUTINE_TOKEN_MUTATIONS`; empty today — the wild-usage survey found only
  reads plus the config writes this seal stops). The
  engine injects the routine token into util subprocesses as `RSCHED_API_TOKEN`, and the
  calling routine's slug as `RSCHED_ROUTINE` — both declared-only like every other injected
  name. `RSCHED_ROUTINE` is WHO is calling, the one thing a util cannot be told by its own
  caller: a routine cannot forge its environment, so a util handed it can scope itself to the
  caller rather than to an argument (the `ftp` util resolves `{routine}` in a source's base
  directory with it, which is how one shared credential serves many routines with nothing
  configured per routine)
  (`engine/exec_env._extra_secrets`, overriding any secrets-store value for that reserved
  name — UNCONDITIONALLY, because a guard written `if routine_token:` fails OPEN on a blank
  value and then hands the util whatever the central store holds under that name, which on this
  instance was byte-for-byte the primary console token; an empty routine token now reaches the
  util as an empty string and fails visibly against a 401). So a run holding the API secret can
  READ the daemon API but every config-mutating
  route (routine/conversation PATCH, permissions PUT, grant decisions, settings, triggers,
  lanes, schedule spools) answers 403 with a pointer to `ask_user config_patch` — the
  HTTP flank of "config is the user's" is sealed, mutating routes are primary-only BY
  DEFAULT, and opening one to routines is an explicit allowlist edit with its reason.
  **"Read-only" is not "may read anything"**: `ROUTINE_TOKEN_DENIED_READS`
  (`/api/fs`, `/api/debug`, `/api/settings`, `/api/search`, `/api/routines/*/secrets`,
  `/api/desktops`, `/browser-view`, `/desktop-view`, matched as SUBTREES segment by segment,
  `*` standing for any one segment) is refused to the routine token with a 403 carrying
  `WWW-Authenticate: Bearer error="insufficient_scope"` and a detail naming `read_file` as how a
  run reaches a file it may read. The first five hand a jailed util exactly what its Landlock
  roots forbid — any directory listing on the host, every central secret NAME with the utils
  declaring it, every routine's OWN secret names with the store's host path, the daemon's own
  stacks, and full-text search over every routine's transcripts and notes. `/api/desktops` is
  the agent-desktop fleet WITH each desktop's screen token — a keyboard on another routine's
  computer, which the broker itself keeps behind `DESKTOP_OPERATOR_TOKEN`, a secret no routine
  is granted — and the two relayed screens are the operator's for the same reason. Every tier decision reads the path the ROUTER dispatches on (`app._route_path`), never `request.url.path`, which Starlette re-parses from the decoded
  path so that an encoded `?` or `#` in a segment ends it early. No HTTP route accepts a
  stream ticket at all: the console's two live streams — the global bus (`/api/events`) and a
  run's tail (`/api/runs/{id}/events`) — are WebSockets (`web/streams.py`), each admitted by ONE
  single-use, 60 s ticket spent on its own handshake (`streams.admits`). They are WebSockets
  because an EventSource holds one of the ~6 HTTP/1.1 connections a browser keeps per origin,
  and the bus plus a tail per live view filled that pool by the fourth tab (F606).
  Being WebSockets also removed the only thing that had bounded their LOAD: a tail polls the
  I/O pool twice every `POLL_S` (0.4 s) for as long as it is open, and until 0.376.0 the
  browser's connection ceiling plus `stream.js`'s three-tail ration capped how many could be
  open at once. The server now carries that bound itself (`web/ioload.py`): a socket holds one
  of `MAX_LIVE_STREAMS` slots for its life, derived as `POOL_WORKERS - SLOW_IO_RESERVE` so the
  SSH and provider-probe callers — which hold a pool thread for SECONDS — always have threads a
  stream poll cannot be holding; past the ceiling a socket is closed 4429 (`CLOSE_BUSY`) and
  `stream.js` backs off and catches up over REST. `GET /api/debug/threads` reports
  `streams.live / ceiling / refused` beside the pool itself.
  Cross-routine FILE reads (`/api/routines/{slug}/file`, `/api/runs/{id}/file`) are the same
  class and deliberately NOT denied yet: one routine was granted a sibling's transcripts on
  purpose, and that grant has to
  be re-expressed as an fs-read root before the door closes. What they reach stays inside the
  run homes, though: every web file read (and an artifact delete) proves containment on the file
  as OPENED (`web/artifacts.open_within`, the kernel's `/proc/self/fd` name for the descriptor),
  because a check made on a path before the open can be raced — a util swapping a directory of
  its own for a symlink between the two had the console read, for it, a file its jail forbids.
  **Every http response carries `Content-Security-Policy: object-src 'none'; frame-ancestors
  'self'`** (`web/security_headers.py`, added outermost in `create_app` so the 401/403 refusals
  carry it too; a WebSocket scope never reaches an http middleware, like the timing one). Two
  directives and no more, deliberately: a `blob:` document INHERITS the policy of the page that
  made it, and the console renders every artifact from a blob, so a `script-src` would stop the
  inline scripts of every HTML artifact a run writes — those are safe because the frame is
  `sandbox="allow-scripts"` with an opaque origin (`components/blobtab.js`), not because a policy
  stops them running. `index.html` has one inline theme script and `el()` sets `style=` all over
  the frontend, so `default-src`/`style-src` need that script hashed and `'unsafe-inline'` first.
  The wider policy (hashed theme script, `style-src`, a `frame-src` naming the browser-view
  relay) waits on artifacts being served from their own route. `object-src` costs nothing: no
  viewer builds an `<embed>` or `<object>`, and a PDF renders in an `<iframe>`. `frame-ancestors
  'self'` costs nothing either: every frame the console builds is this origin or a blob of it,
  which is exactly why the noVNC screens are RELAYED through `web/screen_proxy.py`.
- **Search** (`rsched/search/`, `web/api_search.py`, the console's
  `components/searchbox.js`, focused by `/` or Ctrl-K): SQLite FTS5 over both homes' PROSE —
  transcript say/note/finish/questions/answers/user messages (gz + subrun trees included),
  result.md, history/ archives, LEDGER.md, `.memory/`, pending decision records, recipe
  files; NEVER config, state/, inbox, artifacts, `.util_outputs/`, `.doc_cache/`, or tool observations
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
- **Self-update restart** (`daemon/restart.py`, `restart_action` is the whole state machine):
  dropping the sentinel is NOT a drain and calling it one misleads. A pending restart never
  blocks a start — the scheduler keeps firing runs and conversations normally — and the exit
  comes only once `runner.active_states()` has been empty for `RESTART_IDLE_S` (10 s), at which
  point the `draining` gate goes up against a fire racing the SIGTERM window. It never kills a
  run, and it DEFERS with no deadline while any run is parked (`waiting_user`/`paused`): never
  restart out from under a dialogue. A LANE CHAIN would otherwise starve the gap — the boundary
  between two members is 5 s wide against a 10 s window — so `LaneRunManager._fire_next` HOLDS
  the next member while a restart is pending and nothing else is active; the member fires on the
  new code at the first tick after boot. Under systemd `Restart=always` relaunches on the
  committed code (`uv run` re-syncs deps); under Docker `restart: unless-stopped` does, and its
  restart manager gives up after ONE failed start — so verify every bind source exists before
  dropping the sentinel.
- **An EXTERNAL restart** (a deploy, `systemctl restart`, a container replacement) runs none of
  that state machine: SIGTERM arrives from outside and uvicorn bounds the graceful shutdown to
  10 s, so the exit cannot wait for runs that last hours (F746). `rsched drain`
  (`cli_runctl.py`) is the before-half the deployer runs — it pauses scheduling, waits for the
  in-flight runs and exits non-zero naming what is still running, so `&&` stops a deploy instead
  of eating a run. The after-half is two things: the app lifespan's shutdown half writes the
  deliberate-shutdown mark on ANY graceful SIGTERM (`restart.mark_graceful_exit`, leaving a more
  specific self-update reason alone), and the boot RE-OPENS a run that mark attributes to a
  restart (`daemon/reap_resume.py`) rather than closing it `aborted`. Capped at
  `RESTART_RESUMES`, held by a global pause, never applied to a parked run (its question is
  durable) nor to a crash or an OOM — nothing is restarted on a cause nobody established.
  Orphaned runs claiming to be alive are still closed out at boot;
  what changed is that a restart-caused one does not stay closed.
  **Where no mark exists, the boot reap still answers a narrower question than "why"**
  (`daemon/containerbirth.py`, R2928): WAS this process tree replaced while the run was alive?
  pid 1's start time (`/proc/stat`'s `btime` plus `/proc/1/stat`'s `starttime`) against the run's
  last status write decides it — YOUNGER means the whole CONTAINER was recreated, which is why no
  mark was written, since nothing in this tree performed the stop; OLDER means the container
  lived and the daemon alone died, a different investigation that should not wear the container's
  verdict. So `unknown` splits into `container_recreated`, `daemon_died` and a residual `unknown`
  that now means /proc itself could not answer. The measurement is what three runs across two
  routines had called unreachable: on 2026-10-26 pid 1 was 54 s YOUNGER than the orphan rows, and
  three kills in two days were all container recreates. WHAT recreated it — a compose restart
  policy, a healthcheck, a deploy, an auto-updater — is outside the namespace and is the
  operator's to find; the inference never overrides a mark, and never claims an OOM (the reap's
  own `classify_cause` owns that question from the dead engine's peak RSS). What none of that answers is
  whether the instance that came back is HEALTHY and serving the build it was deployed from, and
  until R2428 nothing did: `rsched postdeploy` (`cli_postdeploy.py` over the pure checks in
  `postdeploy.py`) is the operator's own after-half — served version vs the checkout plus
  `/api/status`'s `build_skew`, backup freshness through `daemon/backup_watch.staleness`,
  state-path ownership against the uid that owns `routines_home`, and which instance answered —
  one line per check, exit 1 only on a FAIL, one row per session in `.control/postdeploy.jsonl`.
  Every judgement is a pure function over passed-in inputs, so the checklist is provable without a
  live instance; the I/O (one GET, the stats, the append) is the CLI module's alone.
- **Aborting a run** (`runner.abort` → `runner_state.abort_process`, behind the run page's abort,
  a conversation's stop, a background task's cancel and `rsched abort`): SIGTERM to the engine's
  process group, SIGKILL `KILL_GRACE_S` (10 s) later if it is still there. A pid read from a
  status.json can outlive its process, so it is signalled only while it still names a process
  (not a thread the kernel gave that id since) outside the caller's own process group — an
  engine leads a session of its own, and a stale pid once aimed both signals at the daemon. The
  engine's handler
  records WHO the signal is and then raises the abort flag (`rsched/abortmark.signalled` →
  `engine/control.request_abort`), which the loop reads at every turn
  boundary and right after a model call — no signal interrupts a model call, so a stop that lands
  in one waits for the call or for the SIGKILL. A util, script or `shell` command in flight is
  the one thing the flag must reach mid-turn: it leads a process group in a session of its own,
  which neither of the daemon's signals touches — the engine's wait on it was its only clock.
  `utils_run.run_jailed` therefore asks the run's abort check (`RunContext.aborted` — the flag,
  or a child run's own kill event) every quarter second and ends the group through
  `procgroup.terminate` the moment it says yes. The observation carries `aborted`, exit 130 and
  the note `… was ended by the run's abort after Ns`; the run writes its own `aborted`
  close-out. Until 0.370.2 the wait did not look: the engine was SIGKILLed while it waited, and
  the command ran on with no deadline (a `shell` `sleep` reparented to PID 1, its status.json
  left `running`). `KILL_GRACE_S` stays shorter than `procgroup.TERM_GRACE_S` (30 s) on purpose:
  an engine killed while a SIGTERM-ignoring group is still inside its grace leaves that group's
  SIGKILL to the backstop `terminate` armed, so a stop during a model call is never made to wait
  out a grace sized to the disk. Lengthening the daemon's grace to "fix" the order would cost
  exactly that.
- **WHO aborted it** (`rsched/abortmark.py`, F751): a signal carries no sender, so the close-out
  used to read "Run aborted by the user/daemon." — a disjunction attributing the stop to nobody
  (fau:20261016-125013, aborted at turn 0 while carrying six jobs forward). F480's breadcrumb
  pattern, one layer down: THE ACTOR SAYS WHO IT IS BEFORE IT SIGNALS, because afterwards there
  is no moment left in which to say it. Three carriers for the three kinds of door — a door
  outside the process (the run page's abort, a conversation's stop, a background task's cancel,
  `rsched abort`) writes `runs/<ts>/abort.json` `{actor, reason, ts, pid}` and then signals, all
  of them through the one shared sequence (`web/api_run_control.abort_with_fallback`); a signal
  handler inside the engine records what it knows (`abortmark.signalled`) before raising the
  flag, the only witness for a stop no door wrote (a `systemctl restart`, whose control-group
  SIGTERM reaches engine and daemon in the same instant); and a parent's kill of a child sets
  that child's own `AbortEvent`, which carries the reason, since the kill never leaves the
  process. The engine resolves them in that order — parent, mark, signal record — and finishes
  "Run aborted by the user — aborted from the run page.", "… by the parent run — …", "… by a
  signal to the engine process — SIGTERM to the engine process.", or, when nothing was recorded,
  "Run aborted by an unrecorded signal (no actor left a record)." The status stays `aborted`, and
  the actor rides the finish event as `actor` + `cause` plus the health row's `cause`
  (`user_abort` · `parent_kill` · `signal_kill` · `unknown` — `runner_reap.classify_cause`'s
  vocabulary), so "count the aborts by actor" is a filter rather than a read of result.md. The
  mark is PID-STAMPED, because a resume reuses the run dir: a mark naming another process is not
  about this leg and reads `unknown`.
  **HOW LONG it went unseen** rides both surfaces too, as `unseen_s` (F597): the door stamps its
  own moment in the mark and the engine resolves the abort at close-out, so the interval that
  made an abort unseen for 30-60 seconds unreproducible is now one subtraction in
  `abortmark._unseen_s`. Only a door that left a stamp has one — a signal handler writes no file
  and a parent's kill never leaves the process — and the field is DROPPED rather than filed as
  `0`, because nothing measured is not the same claim as "the engine saw it instantly". It is
  out of `Abort.__eq__` on purpose: the class is one resolved attribution, and two aborts naming
  the same actor for the same reason are the same attribution however long each took to land.
- **Cancelling the one CALL that is running**, without ending the run (F586, decided as D160-C):
  `POST /api/runs/{id}/cancel-action` with the turn, which the web records as control.json
  `cancel_action` — the one intent in that file the engine reads MID-turn, because it is blocked
  inside the very call the cancel must stop and would otherwise apply it at a boundary reached
  only after that call had ended. `engine/control.cancelled_for_turn` turns it into the
  `cancelled` callback `run_jailed` polls on the SAME quarter-second look as the abort check, so
  the hottest process runner in the system grows no second wait. It is KEYED BY TURN and that is
  the correctness: a bare flag is read by whichever call is in flight when the engine next looks,
  which for a cancel clicked as a long call ends is the NEXT call, one nobody asked to stop. A
  turn number the run has passed can never match again, so nothing has to clear the flag. The
  outcome is its own, never the deadline's: exit `CANCEL_EXIT` (125, positive so
  `executor._note_if_killed` does not file a `util_killed` health event for a human act), the
  observation flag `cancelled`, and a note saying a person stopped this call deliberately — a
  run told "timed out" sensibly retries, and retrying the call somebody just stopped is the one
  behaviour the feature exists to prevent. Cancel outranks abort outranks timeout when more than
  one is true at a single look. A cancelled call is no more a util failure than an aborted one:
  no reliability tick, no `usage` block, no repair route (`executor._ended_by_a_person`). The
  intervention also leaves its own transcript record, `action_cancelled` (written by
  `loop._observe` off that flag), because an observation says what the RUN was told while an
  audit afterwards needs to see that somebody outside it reached in — a cancelled call and a
  call that failed fast are otherwise indistinguishable in a record of outcomes. The control is
  the ✕ on the running action's row (`components/actiontime.js`, which is where the two facts it
  needs already live: the view is live, and this call has not ended). It is offered only for
  `util` / `script` / `shell` — the kinds that run a jailed child the engine polls — and takes
  itself away the moment the observation lands, since its turn is then in the past and the
  endpoint keys by turn.

## Git writes (libgit.py, gitlock.py)

Every repo the instance versions — the library and each routine's own — is written through
`libgit`: `commit()` for one stage-and-commit, `writing()` for a multi-step write (the recipe
revert's `rm` + `checkout` + `commit`). Git takes `<gitdir>/index.lock` for every write of the
index. A process that dies holding it leaves the file behind; from then on every WRITE in
that repo fails while every read keeps working, so nothing notices. On 2026-09-30 the `/home` disk
stalled two I/O commands during the settings-patterns migration; the commits in `self-audit` and
`sprind` ran past their timeout. Both repos were left with an empty lock: the edits staged,
the record saying `failed: {}`, and every later autocommit in either repo bound to fail the same
silent way. Three rules close that, each where it happened:

- **A timeout TERMINATES git and never kills it outright.** Git deletes its lockfiles in its own
  SIGTERM handler and cannot after SIGKILL, which is what `subprocess.run` sends at its timeout.
  `libgit.git` runs git in its own process group; a call that outlives its timeout (30 s) is
  ended by `procgroup.terminate`: SIGTERM to the group, then SIGKILL only for what is still
  running `procgroup.TERM_GRACE_S` (30 s) later. The grace is sized to the disk: `/home` is a
  USB-attached SSD whose bridge aborts a stalled command at the 30 s SCSI timeout. A git blocked
  in that I/O runs its handler only once the I/O returns. The wait ends as soon as the group is
  empty, so it costs nothing unless a member outlives SIGTERM. It covers EVERY member of the
  group, not its leader alone, because `utils_run.run_jailed` ends a timed-out util, script or
  `shell` command — and one whose run is aborted — through the same helper. There git is rarely
  the leader: under `uv run` → python → git the leader exits within milliseconds of SIGTERM, so
  a wait for it alone SIGKILLed git mid-cleanup (measured on the host: leader gone in 0.01 s, the
  grandchild's lock left).
- **A read never takes the index lock.** Every call runs with `GIT_OPTIONAL_LOCKS=0`, so a
  `git status` stops writing a refreshed index back: a reader can neither leave a lock behind
  nor make a concurrent writer fail on its live one. The hold check's `status` in the operator's
  own project repos (`engine/assist_predicates`) goes through `libgit.git` for the same reason.
- **A commit that does not land says so.** `commit()` returns a `Commit` — `committed`, `clean`
  (nothing to commit), `unversioned` (no repo), or `failed` with the step, git's own words and
  the index lock in the way — and files a failure as a `commit_failed` health event, which the
  blocked-work fold shows (docs/run-analytics.md). Clean and failed are told apart by what in
  the commit's scope still differs from HEAD once git has answered, never by parsing git's
  localized messages. `routines_home` — whose health stream hears about it — is a REQUIRED
  argument, so every call site decides; only install-time seeding and a repo's first commit pass
  None. A migration that commits per routine records a commit that did not land under `failed`
  (`migrate_enabled`).

**A stale lock is removed automatically — only when PROVABLY stale** (`gitlock`). The other
choice was to leave every lock to a person behind a one-click remedy. It was rejected because
the person would judge with less than the code knows. Inside the container the code sees every
process that could hold the lock except a git run outside it, which the person clicking cannot
see either — and waiting costs a failed commit per run in that repo until someone looks. The
proof is four conditions, all required, checked while `writing()` holds the repo's commit lock:

1. the commit lock is held, so no cooperative writer (daemon, engine run, web layer, the
   library `git` util's `sync`) is between its `add` and its `commit`;
2. no git process visible here works in the repo — none has its working directory, a `-C`, a
   `--git-dir`, a `--work-tree` or a `GIT_DIR` inside it — and a git whose working directory
   cannot be read (another uid's) counts as one that does;
3. the lock is EMPTY. An interactive `git commit -a` writes the new index into its lock and
   keeps it while its editor is open, the one legitimate long hold; a lock with content is a
   person's to judge and is never removed here;
4. it is older than `gitlock.STALE_AFTER_S`, ten minutes: the margin for the holder no scan can
   see, a git on the host outside the container or over the sshfs mount from another machine.
   Each git call of an rsched commit ends within its timeout plus its grace, one minute.

A lock that fails any condition stays. The commit that met it fails visibly with the reason it
was kept; the next commit after the lock turns ten minutes old removes it. The removal
re-checks the file's inode, mtime and size, so a lock replaced after its inspection is never the
one deleted. A removal is filed as `git_lock_cleared` — a run of those is something killing git
mid-write (a stalling disk, the OOM killer, a container stopped mid-commit). A repo no rsched
commit writes — the scheduler's own checkout, a worktree a session made, a repo a routine's
script manages — is outside this path. A util, a script or a `shell` command that runs git there
is still terminated at its deadline, or when its run is aborted, rather than killed
(`run_jailed`) — and its SIGKILL still comes if the engine dies inside the grace (the backstop
`procgroup.terminate` arms). The library's `git` util holds itself to the same rule INSIDE that
deadline: each git call it makes leads a process group of its own and, past the call's own
timeout, is ended the `procgroup.terminate` way — SIGTERM to git, its hook and the hook's
children, SIGKILL 30 s later — while a SIGTERM the util itself receives is passed on to that
group. Its `subprocess.run` timeouts had SIGKILLed git, a lock-holding `commit -a` included. A
lock that a kill from elsewhere leaves is found by whoever next writes there, as before.

## Observing the daemon

The web process records its own slowness (0.332.0): a timing middleware in `web/app.py` counts
requests in flight and keeps the last 50 that exceeded `SLOW_REQUEST_S` (2 s), each logged as a
`slow request` WARNING, and `web/api_debug.py` serves `GET /api/debug/slow` (that ring) and
`GET /api/debug/threads` (every Python thread's stack, the in-flight count, and the two
thread pools) to the operator's primary token only. The two pools are reported together
because confusing them costs a diagnosis: anyio's limiter (40 tokens, its own default) runs
`def` handlers through starlette's `run_in_threadpool`, while every `asyncio.to_thread` call —
the run tails' polls, the machines' SSH, the endpoint probes, the `rmtree`s and registry scans
— runs on the loop's DEFAULT executor, which `web/ioload.py` installs as a named pool of
exactly `POOL_WORKERS` (32) threads (asyncio's own `min(32, cpu_count + 4)` would make the
live-stream ceiling derived from it depend on the host). `io_pool.queued` is the number of
those jobs waiting for a thread: non-zero means QUEUED, not slow. The live streams never reach the timing:
they are WebSockets, which an http middleware does not see. The container also carries `SYS_PTRACE` so `py-spy dump` works inside it
(deploy/DOCKER.md). All of it exists because on 2026-09-12 every sync handler took 20-50 s for
an hour with one worker thread at 70% CPU, and the daemon could name neither the thread nor the
requests it had starved. It named them the same afternoon (0.334.0): the dashboard reloading five
endpoints on every bus event, one of them re-parsing every routine.yaml per request (a scan of
the routine files is memoized per file on its stat fingerprint since), and a trivial sync handler
queueing for a threadpool token. The
standing rule for the console follows from it: **a live refresh on a bus event fetches only what
that event can change** — routine cards and status — never config-shaped or expensive data. The
ring named the next one two days later (2026-09-14): `/api/questions`, fetched by five surfaces
per tab on every bus event and walking three catalogs from disk per call, at 10-20 in flight for
7-25 s each while three runs booted — now memoized per home with single-flight misses (0.340.0),
so the server-side half of the rule is: **a read model on a bus-event path is memoized on a stat
fingerprint of its sources**, and a burst of identical requests costs one compute.

