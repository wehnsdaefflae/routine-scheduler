# Rule assists — surfacing a curated rule at the moment it applies

A general rule is prose the model has to notice the moment for. Most of a rule's length is
that noticing — the "when" scaffolding it holds and matches against the situation in front of
it — and the realistic failure is not a run that refuses a rule. It is a run that means to
follow one and forgets it at the moment it applies.

An **assist** externalizes the noticing. It is a `(moment, predicate) → line` declaration in a
rule's own frontmatter: a deterministic check over the SITUATION that surfaces the rule's
operative line exactly when the rule becomes relevant.

This is the library-curated half of the same relevance-trigger layer the
[consequence reminders](reminders.md) are the self-authored half of. Same idea, different
author: there the model learns a pattern from its own surprise, here the library states one
deliberately. They are not two mechanisms.

## Why timing and not enforcement

It is tempting to CHECK a rule instead — decide mechanically whether a run complied, and flag
it when it did not. For a few rules that works. For most it is impossible, and the reason is
worth stating precisely, because it is what makes assistance the primary mechanic rather than
a consolation prize.

**A mechanical check is a function of the trace a run leaves behind.** It can separate
compliance from violation only when the two leave DIFFERENT traces. For most rules they do
not. Take `root-cause-fix` — "repair the cause, never the symptom". Two runs face the same
failing test: one traces the cause and installs a general prevention, the other patches the
symptom. Both produce the same observable trace — a diff that turns the check green, a passing
test, a plausible ledger entry. Whether the fix addressed the cause is a fact about the space
of FUTURE inputs the diff will face, and it is not observable now. No predicate over the trace
can tell those two runs apart; the evidence a checker would need does not exist.

Enforcement has to grade the REASONING, and reasoning is never in the trace. Assistance only
has to detect **the moment the rule becomes relevant** — and relevance is a property of the
SITUATION, which is in the trace. So: *the rule you can never check, you can still time.* You
cannot decide whether a fix hit the cause; you can detect with certainty the moment it matters
— code being edited right after a failed check — and say "trace it to the cause" right then.

That is why every rule has a usable assist, including the judgment-tier rules that have no
compliance check at all. The impossibility result blocked grading, not timing.

## The rule factors; it does not shrink

A rule's prose does double duty today: teach the model to notice the moment, and say what to
do once it applies. An assist takes the first duty off the model, so the message at the moment
condenses to the second — one pointed line. The rule itself keeps its full rationale and
caveats, read on demand with `read_rule`, and the surfaced line always names that route.

| part | was | becomes |
|---|---|---|
| the trigger | "when…" scaffolding the model held and matched | a deterministic predicate — off the model entirely |
| the operative line | buried in the body | the surfaced payload, short, shown only when relevant |
| the full rationale | permanently resident in the run's attention | on-demand reference behind the trigger |

Two caveats, or the condensation misfires. **Terseness is earned by trigger precision**: a
short line fired at the wrong moment is worse than the full rule read deliberately, because a
run trusts a fired assist to be relevant. And the payload line is **authored deliberately as
its own field** — never auto-truncated from the body — because the caveat-heavy rules
(`ai-writing-tells`: detectors are not ground truth) are exactly the ones a machine-made
excerpt would misrepresent.

## The declaration

In a rule's frontmatter, beside `effect:` and `tags:`:

```yaml
assists:
  - id: after-a-failed-call
    moment: observation
    predicate: observation-failed
    payload: remind
    line: >-
      Read this failure before reacting to it — the message, the exit code, the usage line.
      The same call with the same arguments returns the same outcome.
```

Named `assists:` and not `triggers:` because `routine.yaml` already has a `triggers:` key —
the events that FIRE a run (`rsched/triggers.py`). Different concept, same word; one name per
concept is worth more than matching the note this was designed from.

A rule declares a check by NAME. The predicate itself lives in
`engine/assist_predicates.py`, because a rule is prose in a git-synced multi-writer
directory and must never be able to ship code. The registry is what the linter validates
against, so a predicate removed from the engine turns every rule declaring it into a loud lint
error rather than a silently dead assist. Each predicate declares the moment it answers at,
and a rule asking a pre-finish question at an observation is refused: the situation it wants
to read is not there yet.

Validation runs in `lint_rule_text`, which means one call covers all four authoring surfaces —
the `write_rule` action (before its approval ask), the Library tab's PUT, `rsched lint`, and
the per-rule `problems` the Library page shows.

## The three moments

| moment | fires | delivery | payload | cost |
|---|---|---|---|---|
| `pre-action` | on the action about to run | the action is HELD | `hold` | one turn |
| `observation` | on the observation that just came back | a tail on that observation | `remind` | none |
| `boundary` | at a turn boundary | an appended `ENGINE NOTE` | `remind` | none |
| `pre-finish` | as the run tries to end | a finish-gate deferral | `remind` | one turn |

**The moment and the payload are coupled, not free to combine**, and the linter enforces it.
A chosen action can only be reached by STOPPING it — "remind and let it run" is not
expressible once the action is emitted — so `pre-action` can only hold. And the free moments
have no action in hand, so they have nothing to stop. A rule that pairs them wrongly is
refused at authoring time rather than silently never firing.

`observation` and `boundary` are free: they append to a message the run was getting anyway.
`boundary` uses the same carrier a mid-run rule binding already does
(`switches.apply_rule_additions`), and both are append-only, because the composed prompt is a
caching contract.

**`pre-finish` costs a turn, and has to.** A line surfaced as the run ends is a line nobody
can act on, so the finish is set aside and the model gets one more turn — the shape the finish
gate's five other rungs already use. It carries their two guards (never a child run, never the
reserved finish turn, which would force-finish the run with an engine string) plus one of its
own: a run may be held at its finish by an assist **at most once, ever**. A rule may ask for
an ending to be reconsidered; it may not negotiate over it.

### The shared hold seam

`pre-action` feeds the SAME interception the [consequence reminders](reminders.md) use —
`engine/hold.py`, extracted when this second caller appeared. Two things go wrong if each
layer owns its own:

**The ledger key.** A hold is remembered so re-emitting the action is the confirmation to
proceed. Keyed on the bare action string, a reminder holding `util:fs-ops mv a b` would
silently spend the rule layer's only hold on that same string, and the rule's caution would
never be seen. The key carries the SOURCE, so each layer has its own budget.

**One interruption per action.** However many sources match, the model is stopped once —
the anti-livelock reasoning applies to the pair, not to each layer — so precedence resolves
it rather than queueing a second hold behind the first. Precedence is
specific-before-general: a reminder is evidence THIS routine gathered about THIS action, a
rule is a standing principle that applies to everyone, so when both fire the run hears the one
it learned itself.

`hold.is_hold(obs)` is the predicate two counters depend on: a held action grounds no finish
(live and rebuilt on resume) and spends no allow-once grant. It was a string literal tested in
three modules, one of them negatively — exactly the check a second hold kind walks past.

## Guards

An assist fires **at most once per run**. That is the rule `reminder_held` (one hold per
action string) and the stopping verifier's `_challenged` set (one challenge per condition)
already apply to their own interventions, and it exists for the same reason: a trigger that
can fire twice on one situation livelocks a stubborn model into a dead budget.

A predicate that raises is **inert, never fatal**. A library document names the check; the
run's work is not this layer's to lose.

Every fire is counted, per assist, in `state/assists.json`. Deliberately just a counter and
deliberately engine-written: at the `remind` rung an assist costs no turn, so there is no
confusion matrix to fill in and no reason to spend a model's attention labelling one. What the
count does answer is the question precision is reviewed by — which assists fire, and how often
— so a trigger that fires constantly is visible before anyone promotes it to a rung that costs
turns. The four-way label lands with the hold payload, where a turn is actually spent.

## Not a capability

An assist is part of the rule, not a permission of its own. The user already decided this
routine practises this rule — `effect.when` is exactly that decision — and an assist changes
only WHEN its line is read, never what the routine may do. Nothing here can reach a routine
that does not hold the rule.

`DEFAULT_RULES` is not empty, so this layer is live in most routines from the day it ships —
which is why precision, not coverage, is the budget, and why only one of the six assists costs
a turn.

## The payload axis, and what exists

`remind → scaffold → do → hold`, ascending. `remind` and `hold` are built — `hold` once the
pre-action seam existed (`engine/hold.py`, shared with the reminder layer). `scaffold` and
`do` are not, and they are not stubs in the schema either: both need a helper channel the
engine does not have, and an enum value it cannot honour would be a lie in the library's own
contract. `PAYLOADS` names what works.

## Reaching a live instance

The seed sync is ADD-ONLY: it installs a rule the live library is missing and never overwrites
one, so a local edit always wins. All 26 rules already exist live, which means **a frontmatter
block added to `library-seed/rules/*.md` reaches zero instances on its own.** Each batch of
assists needs a one-shot `MIGRATION(expires=…)` that carries the block across — the first is
`migrate_rule_assists.py`. It is idempotent, it skips a rule an operator has edited (a local
edit outranks the seed there too), and it names everything it skips rather than passing over
it quietly.

## The six

| rule | moment | predicate | why this moment |
|---|---|---|---|
| `git-checkpoint` | pre-action | `uncheckpointed-repo-write` | the first edit into a git repo the ENGINE does not version — it commits its own working directory at run end, a granted project repo has no undo point unless the run makes one |
| `error-recovery` | observation | `observation-failed` | the run has just been told something did not work, and the next action either reads the failure or repeats it |
| `intent-inference` | boundary | `user-corrected` | an intervention has just landed, and "what standing preference does this imply" is answerable now and stale later |
| `ask-policy` | boundary | `asks-piling-up` | several decisions are waiting on the user, which is the shape the rule is about |
| `decision-record` | pre-finish | `ledger-untouched` | the reasoning behind the artefacts is lost at exactly this moment, and only here can the run still write it down |
| `unexamined-is-not-clean` | pre-finish | `clean-claim-without-a-denominator` | an all-clear is only meaningful beside what was examined, and the summary is where it is claimed |

`git-checkpoint` is the rung the design note reserves for HOLD: a crisp pre-action predicate
AND an irreversible cost to skipping. It fires on the FIRST such write only, which is what
makes "no checkpoint yet" true without having to DETECT a checkpoint commit — that happens
inside a util or a shell command, where the engine sees a command string and an exit code and
nothing more. It is overridable like every payload: re-emit the action and it runs.

Two predicates read signals the engine already keeps, which is why they are cheap:
`asks-piling-up` reads `ctx.asks_deferred` (the churn telemetry for a decision thrown over the
wall), and `ledger-untouched` reads `turn_records`, the run history that SURVIVES compaction —
a predicate that greps the message list silently stops working on exactly the long runs that
need it most.

`clean-claim-without-a-denominator` is deliberately crude: it asks whether the summary claims
cleanliness and carries no number at all, so a summary that quantifies ANYTHING passes. One
that tried to judge whether the denominator was the RIGHT one would be grading the reasoning
again, which is the thing that cannot be done.

## What is still deferred

The four-way outcome label (`could_not` / `would_have` / `did` / `didnt`) that tunes a
consequence reminder is NOT wired to assists yet. At the `remind` rung there is nothing to
weigh — no turn is spent — and the fire counter already answers which triggers fire and how
often. The `hold` rung does spend a turn, and its wording asks the run to say in its next
`say` when a rule turned out not to apply; a structured label there is the next increment, not
a gap left by accident. `scaffold` and `do` need a helper channel that does not exist.

`ledger-untouched` reads `turn_records`, the run history that SURVIVES compaction — a
predicate that greps the message list silently stops working on exactly the long runs that
need it most.
