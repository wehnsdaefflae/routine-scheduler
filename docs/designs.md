# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

Started 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. An entry headed by an item id answers that Messages-page finding; an entry
decided in conversation before any finding exists says so and carries none.

---

## Structured outputs instead of forced tool use (decided 2026-09-11, BLOCKED on verification)

**Problem.** `endpoints/anthropic_api.py` enforces the action schema with a single forced
tool call — `tools=[{name: "action", input_schema: <schema>}]` plus
`tool_choice: {type: "tool", name: "action"}`. That shape is being retired upstream: forced
tool use (`tool_choice` `any`/`tool`) returns a **400** on Claude Fable 5.1 / Mythos 5.1, so
this transport simply cannot reach that model tier. The modern equivalent is structured
outputs — `output_config: {format: {type: "json_schema", schema: <schema>}}` — where the
reply comes back as a TEXT block containing schema-valid JSON rather than a `tool_use` block.

Second prize, and arguably the bigger one: the adapter sends no `thinking` field at all, so
on `claude-opus-4-8` (today's main model behind claude-proxy) runs execute with extended
thinking OFF while three catalog entries pay for an `effort` knob. Whether to turn thinking
ON is a separate decision — it touches the deliberation doctrine ("deliberation is ink,
effort is scratch paper") and raises a replay question, since the engine's message list is
text-only and would drop thinking blocks between turns.

**Verified 2026-09-11 against the live transports** (probe:
`~/.config/routine-scheduler/so-probe.py`, run inside the container — `cliproxy` resolves
only on the compose network):

| | forced tool use | structured outputs | strict tool + auto | adaptive thinking |
|---|---|---|---|---|
| claude-proxy / claude-opus-4-8 | ok | **ok** (`blocks=['text']`, valid JSON) | ok | ok |
| codex-proxy / gpt-6-astra | *429 — quota* | *429 — quota* | *429 — quota* | *429 — quota* |

**Why it is blocked.** `codex-proxy` serves `gpt-6-astra`, the system model for 28 of the 33
live routines, and it was rate-limited (`usage_limit_reached`) at every probe attempt that
day. It fronts an OpenAI model over the anthropic wire, so there is no reason to assume it
maps `output_config.format` the way the Claude-backed proxy does — it has to be measured.
The house rule forbids shipping a tolerant dual path, so this is a HARD swap or nothing, and
a hard swap that 400s on the system model breaks the fleet at the next scheduled run.

**First increment.** Re-run the probe when `gpt-6-astra` quota has reset. If structured
outputs works there too: swap `tools`/`tool_choice` for `output_config.format` in
`AnthropicEndpoint.complete`, change `_parse` to read the JSON out of the text block instead
of the `tool_use` block, and make sure `actionschema` emits `additionalProperties: false`
(structured outputs wants it). The existing 400-degradation path needs rethinking too: it
currently strips the whole of `output_config` on an effort-related 400, which would take the
FORMAT with it and leave a turn with no schema enforcement at all. If structured outputs
does NOT work on codex-proxy, the swap stays unbuilt until that transport changes — and the
Fable tier stays unreachable, which is the cost of keeping the fleet up.

---

## F363 — failover support: a per-stage distillate

**Problem.** A catalog model may declare `fallbacks:` (endpoints/failover.py). When the
primary fails hard the run continues on a weaker model — with the *same* prompt. The recipe
was written for the strong model: it says WHAT to do and leaves the how to judgment, which
is exactly what the weaker model does not have. So failover keeps the run alive and quietly
degrades the work.

**Decision (D95, operator 2026-08-20, free text; recommendation A).** Support the fallback
model with a per-STAGE *distillate* — a short, concrete crib for that one stage — injected
**only when failover has engaged**. Not a second recipe, and never in the normal prompt.

**Shape.**

- A distillate is one file per stage: `<routine>/stages/<name>.distillate.md`, ≤ ~25 lines.
  It holds what the strong model actually DID in that stage, reduced to instructions a
  weaker model can follow: the concrete tool calls in order, the shape of the output, the
  two or three traps previous runs hit. It names capabilities, never utils — the
  recipe rule (CLAUDE.md) applies to it unchanged.
- **Derivation is a meta-routine's job, not the engine's.** routine-improver already reads a
  routine's own finished runs; it writes the distillate from runs that (a) completed on the
  routine's `main` model and (b) reached that stage. A stage with fewer than N such runs gets
  no distillate — an invented crib is worse than none.
- **Injection.** `engine/completion.py` already logs the failover switch as a transcript
  `error` event carrying a `failover` payload. That is the trigger: from the moment a run
  switches down the chain, the composer appends the CURRENT stage's distillate to the turn's
  context, under a heading that says plainly what it is ("You are running on a fallback
  model. Here is how this stage was carried out on the primary."). It is dropped again if
  the run climbs back to the primary.
- **The caching contract holds** (CLAUDE.md: the message list is appended-to, never mutated).
  Failover already invalidates the provider cache by switching model, so the append costs
  nothing extra — but it must be an APPEND at the switch, not a per-turn re-render.

**Why not the alternatives.** A whole second recipe doubles what routine-improver maintains
and drifts silently. Injecting the distillate always makes every strong-model run read a crib
written for a weaker one — the failure mode that made `deliberation` a knob in the first
place.

**First increment.** The injection seam only, with distillates written by hand for one
routine: teach the composer to append `stages/<current>.distillate.md` when the run's
transcript carries a `failover` event. That is testable with `ScriptedEndpoint` and proves
the trigger before any generation work exists.

---

## Progressive ossification — the run history as the routine's own corpus

**Decision** (operator, 2026-08-27, in conversation). No item id yet: this entry precedes
its finding rather than answering one. F363 is the same operation at a different
compression level — take the corpus of prior runs of ONE stage, emit an artifact that
reduces what the next run must work out. F363 emits prose for a weaker model; this emits
code, or nothing at all, for no model.

**Problem.** A routine re-pays the same deliberation every run for steps whose answer never
changes — which files to read, which parameters to fetch with, which capability to reach
for. That is not one turn: it is the several turns of context-acquisition that precede the
action, all to arrive at a conclusion the last twenty runs already reached. The system
already wants this fixed — `scripts/` exists as the ossification target, and
routine-improver scouts recipes for deterministic prose responsibilities — but nothing
MEASURES whether a step has converged. Today an ossification is the improver's taste, and
a wrong one degrades the work silently, because a mature routine is the one nobody reads.

**The direction, which is the load-bearing decision.** Exploration is the STARTING state
and exploitation is earned; never the reverse. A scheme with reuse as the default and a
sampling rate for fresh deliberation makes exploration permanent rent — the cost curve
flattens instead of falling. Here the unit of progress is the STEP, not the run: every step
begins in prose, evidence accrues from ordinary runs at zero extra cost (that deliberation
was happening anyway), and a step is promoted only when its own history earns it. A routine's
cost is then monotonically decreasing over its lifetime.

**Shape.**

- **One corpus, many consumers.** Per routine, per stage: the cases `(preceding
  observation → action taken, with what it cost)` across all runs. Derived, never written.
  Everything wants it — F363's derivation half, ossification into `scripts/`, per-stage
  model right-sizing, budget and cadence calibration from measured distributions, an
  anomaly alarm, convergence detection. Build the corpus, not N features.
- **A promotion ladder, each rung demanding more evidence:** prose → the run reads what
  previous runs did here and re-issues it itself → a guarded reuse → a script. The first
  rung needs no engine change and no new action kind: the model reads the cases and
  chooses, so it is the guard on every use, and its disagreement is exploration for free.
- **The promotion test is that the corpus is a FUNCTION** — same input class, same action,
  across N cases. Variance in the decision IS the judgment, and an unformalizable step must
  stay in prose. This is what makes "formalizable" checkable rather than a vibe. A run that
  freely re-issues an identical action, having been able to choose otherwise, is the
  strongest evidence available that the step is constant — and it is generated during
  normal operation, so no shadow-execution machinery is needed.
- **Rank candidates by DELIBERATION cost, not execution cost.** There is no reasoning-token
  field (usage is `{in, out, cost}` per event), but reasoning bills as output: a turn with a
  two-line action and a large `out` is a heavy-deliberation turn. `out ÷ (say + action size)`
  ranks candidates with no new instrumentation. Multiply by frequency and by constancy.
- **Demotion, not sampling.** A reused decision still EXECUTES, so its observation returns
  to the run, which reads it fresh: a fetch that returns garbage, a parse that fails, a file
  that is gone. Drift is caught by failure, not by a schedule. `scripts.py` already returns
  `(exit, out, err)` into the observation, so a script that raises on unrecognized input
  hands the step back to prose by itself — the discipline is writing scripts that raise
  rather than guess.
- **A step whose failure would be SILENT is the only residual risk.** The fix there is to
  make the failure loud (an assertion over the output shape, which prior runs hand you for
  free), never to re-deliberate periodically.
- **What this dissolves:** cache-invalidation fingerprints. A changed stage is not a stale
  cache, it is a NEW step with no accumulated evidence, which starts in prose like anything
  else. Do not build a scheme that tracks every input to a decision (stage text, permissions,
  rules, model, util catalog): it is bookkeeping that still cannot see the outside world
  change, which is the common case.

**Why not the alternatives.** A global exploration rate charges rent forever and never lets
a converged step settle. A `replay`/`reuse` action kind FIRST inverts the value: the actions
safe to replay are cheap to redo anyway, and the ones worth replaying carry effects — while
the corpus-as-read version needs no 26th kind at all. A dedicated kind earns its place only
once the corpus shows a stage where the run always agrees AND the arguments are bulky enough
that emitting them is the cost.

**First increment.** The per-stage, cross-run CASE INDEX — nothing that consumes it. Half of
it exists: every `assistant_action` is stamped with the active phase, and `statemap.phase_stats`
already derives per-phase turns / tokens / cost from one transcript. What is missing is the
cross-run aggregation keyed by stage, holding cases rather than totals. Build it on the
`readmodels/util_stats.py` pattern — retained transcripts, both homes, root + sub, memoized
behind an (inode, mtime, size) fingerprint, no database.

**The one trap that shape carries:** transcripts are PRUNED. `util_stats` needed a durable
usage stream precisely because retention would otherwise erase its history, and a corpus that
forgets is worst exactly where it matters — a step converges over the long tail, which is the
part retention deletes first. So the case index needs its own durable record from day one, or
it will confidently report convergence over whatever window happens to survive.

---

---

## `fs:` narrowing review — the candidate set is five utils, not a hundred

**Decided in conversation 2026-08-30; no finding.** 0.256.0 gave every util an `fs:` header and
narrowed each subprocess jail to `grant ∩ declaration`. The ~110 already-existing utils were
migrated MECHANICALLY to `fs: roots`, which preserved their previous exposure exactly and was the
only safe move at the time — but nobody has since asked which of them declare more than they use.

**What the review found (static pass over the live library, 136 utils).** There is no bulk edit
to make; the intuition that "utils touching only caller-supplied paths could be narrowed" does
not survive contact with the header vocabulary: `roots` MEANS the run's granted roots, so a util
that opens whatever path its caller names needs exactly `roots` and cannot be narrowed without a
new declaration form (`fs: args`, only the paths on the command line) that does not exist and
would be its own decision.

- 113 of the 123 `fs: roots` utils perform a filesystem operation in their own source.
- 10 do not. Four of those spawn a child that plausibly does and must keep `roots`:
  `captcha-fetch`, `job-scrape`, `rsched-lint`, `surface-captcha-to-user`. (`shell` was a fifth
  until 0.287.0 retired the util for the `shell` action, whose jail is composed on the same
  `fs: roots` terms in code.)
- **The candidate set is the other five** — no filesystem call and no subprocess anywhere in
  their source, i.e. pure network clients that could declare `fs: none`:
  `darknet`, `proemion`, `remote`, `rutorrent-rpc`, `uncensored-model-list`.

**First increment.** Read those five and change the ones that are genuinely path-free, one at a
time, each with its `--selftest` still green. `remote` needs the closest look: the engine mounts a
machine's sshfs share under `<routine>/mnt/<name>/`, so the question is whether the util itself
ever reaches into that mount or only ever execs over SSH.

**Not a linter.** The signal is "declares more than it uses", which no check can assert without
knowing what a util is for; a rule that fires on 113 correct declarations would be turned off in a
week. This is a review somebody does once, then re-does when the count of `fs: roots` utils has
grown enough to be worth it.

---

## A third stopping SCOPE: work that outlives one run but does not retire the routine

**Decided in conversation 2026-09-17; no finding.** The survey that produced it compared this
engine against four spec-driven-development toolkits — [spec-kit](https://github.com/github/spec-kit)
(MIT), [OpenSpec](https://github.com/Fission-AI/OpenSpec) (MIT),
[spekk-cli](https://github.com/spekk-ai/spekk-cli) (Apache 2.0) and
[shipsmooth](https://github.com/bitkentech/shipsmooth) (Apache 2.0). Almost everything in them
already exists here under different names (a constitution is the rules library, phases are
`stages/` + `phase.json`, a change folder is `docs/designs.md` plus the Items ledger, an observer
is self-audit, a planning store is a domain's shared store), which is why none of the four is
being integrated: they deliver their value as slash commands inside an interactive assistant, and
a second agent loop in the path is banned. Two ideas survived the comparison. One shipped in
0.350.0 as the `unmet` residual. This is the other.

**Problem.** `state/stopping.json` has exactly two scopes and there is nothing between them.
`scope: "run"` bounds ONE run and never transitions — correctly, since a per-run bound cannot be
"already met", and making it sticky is the defect the split undid. `scope: "goal"` is the state
after which the ROUTINE is finished, and it is sticky *because* it retires the routine. A unit of
work that legitimately spans several runs — a migration with eleven steps, a backlog of twelve
sites to convert, a document with five sections each needing its own research pass — fits neither.
Today it is carried in recipe prose, in `state/notes.md`, or in the `unmet` residual, none of which
can say "this piece is finished and the next run should not re-derive it", and none of which two
runs can hold without colliding.

**Shape (spekk's assertion model, mapped onto what exists).** A `scope: "work"` condition: sticky
like a goal, so it transitions to `met` and stays there — but it retires the ITEM, never the
routine, so `evaluate()` keeps answering about goals alone and nothing about retirement changes.
Ordering is the existing `requires` connective, which already expresses a dependency chain and
already renders as `waiting on s1`; spekk's one-dependency limit is the same restraint this
document's two-level nesting applies for the same reason. What is genuinely new is the LEASE:
a condition claimed by a run (or by a `spawn`ed child) while it is worked, released on exit, and
ignored once stale — so a parent and its children can share one queue instead of the parent
re-deriving what a child already took. spekk keys its lease `builder-<host>-<pid>-<ts>` and
treats an old lock as absent, which is the right shape here too: the engine already reaps runs
whose process is gone, and a lease that outlived its holder must never block the queue.

**Why it is not built yet, and what would settle it.** The honest case against is that this is
precisely the feature that grows a second planning system inside a scheduler — and three
mechanisms already cover much of the ground (the `unmet` residual carries what a run left, the
digest tail carries notes forward, `.memory/` carries curated findings across runs). The question
it hangs on is empirical and answerable: **do live routines actually lose or re-derive work
between runs today?** A self-audit pass over the run transcripts of the multi-run routines can
answer it — a run re-doing a step a previous run completed, or a child and its parent doing the
same piece, is the evidence. Build it if that evidence exists; delete this entry if it does not.

**First increment, if it goes ahead.** The scope alone, with no lease: add `"work"` to
`stopping.SCOPES`, make `record_accounting` transition it on `met` the way it transitions a goal
(and nothing else — `evaluate` must keep ignoring it), render it in `stopping_digest` as its own
block beside the other two, and let `api_stopping` write it. That is testable on its own and
leaves the lease — the part with real concurrency in it — as a second decision.
