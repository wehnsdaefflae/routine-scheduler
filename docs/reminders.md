# Consequence reminders — a caution that fires at the action, not at the boot

A **reminder** is `(regex → consequence)`: a pattern over the canonical one-line rendering of an
action, plus the short caution that pattern is worth interrupting for. Before a matching action
executes, the engine HOLDS it — it does not run — shows the model the caution, and lets it decide
again.

The layer exists because the framework's other "learn from surprise" surfaces are all
*just-in-case*. `.memory/` puts its INDEX in the boot digest and asks the model to recall the right
note at the right moment out of a large, always-present context. `note` files a line to
`state/notes.md` that the NEXT run's digest carries forward. The curated rules are advisory prose
in the prompt. All three depend on the model self-detecting relevance, and the common failure is
not that the caution is missing — it is that the caution is somewhere in context and does not come
to bear on the turn where it matters. Nothing said "you are about to do the thing that burned you
last time."

A reminder says exactly that, and it says it *before* the effect. That timing is the whole feature:
a caution delivered with the observation arrives after the consequence, when nothing can be
avoided. There is deliberately no cheaper passive tier.

## The match target

Everything rests on `engine/actionschema.canon(action)` — THE canonical one-line rendering of an
action, and the documented thing a pattern matches:

```
util:fs-ops mv a b            a util call carries its ARGUMENTS (`util:fs-ops` alone
                              cannot tell `mv` from `rm`)
shell: rm -rf build/          the command IS the action
read_file paths=a.md,b.md     the kind that carries a LIST names it
write_file path=state/x.json  every other kind names its identifying field
wait                          a kind with no identifying field is just itself
```

It is untruncated on purpose: matching a pre-truncated string would silently change what a regex
can see as arguments grow. Matching happens with `re.search` against the first
`MATCH_TARGET_CHARS` (2 000) of it, so a pattern says where it anchors (`^util:fs-ops mv `) rather
than describing a whole line. Precision and recall are only tunable if the match target is stable
and legible — which is why `canon` has ONE implementation, shared by the interceptor and by every
surface that shows a person or a model what matched.

## The two stores

Split by BLAST RADIUS, and the routing heuristic follows from it: *if another routine made this
exact call, would the same consequence apply?*

| | local | global |
|---|---|---|
| lives in | `<routine>/state/reminders.json` | `<library>/reminders/<id>.json` |
| reaches | this routine | every routine at `reminders: global` |
| written | autonomously | approval-gated (`remind_confirm`) |
| about | this routine's files, task, state | the util or the action itself |

Reminders are **born local; global is earned.** A bad local reminder taxes one routine's turns; a
bad global one taxes every capable routine at its next run, silently. Unsure is local. The match
target usually settles it on its own: a pattern over a util invocation is probably universal, a
pattern over a `path=` into this routine's artifacts is local by nature.

With both stores active the live set is their **union, deduped by regex, local winning**. Same
regex = the same match class, which is the only "same consequence" test a machine can make;
different regexes are different classes and both are shown — inside ONE hold. Precedence never
multiplies turns.

**The tally is per-routine and lives only in the local file** — for a global reminder too, under
`global_stats`. A global reminder's DEFINITION is curated and shared; the evidence about it is
local, because "did this fire uselessly here" is a question about one routine's work. It also keeps
the library from taking a git commit on every fire, from every routine, concurrently.

## The four-way label — what makes a pattern tunable

Every hold is counted as a `fires`, and the model labels how it turned out. Riding an action that
was happening anyway, the label costs no turn:

| label | meaning | classifier reading |
|---|---|---|
| `could_not` | the consequence was impossible for THIS action | **false positive** — narrow the regex |
| `would_have` | it was on track and the hold avoided it | **true positive, prevented** — the value |
| `did` | the run went ahead and the consequence happened | true positive, realised — necessary, or the caution is not landing |
| `didnt` | the run went ahead and nothing bad happened | a benign instance, or a soft false positive |

Read off them with no extra instrumentation: false-positive rate ≈ `could_not / fires`; value ≈ the
`would_have` count; cost ≈ `fires`, one turn each. Many fires and almost all `could_not`/`didnt` →
narrow or delete. Recurring `did` → the reminder is not changing behaviour: sharpen the caution or
accept the action and drop it. Recurring `would_have` with low `could_not`, over a match target
that is about the action itself → a global candidate. And `fires - Σlabels` is the count of holds
the model never labelled, which is itself the signal that the layer is being paid for and not read.

Labelling is **not** enforced. `remind_feedback` rides every kind, so rejecting an action for
omitting bookkeeping would put the layer in the way of the work — and the schema-storm guard fails
a run whose turns keep needing retries. So the hold demands the label, the engine asks once more
two turns later if it is still owed (`did`/`didnt` can only be known a turn after the action ran),
and what stays unlabelled is visible in the tally.

## The runtime

`rsched/reminders.py` is the store — the record, the two homes, the union, the tally.
`engine/remind.py` is the runtime — interception, the ops, the approval gate. The two side fields
are `remind` (`{op: add|revise|delete, id?, regex?, description?, scope?}`) and `remind_feedback`
(`{id, label}`), both modelled on `note`: optional on ANY kind, no turn cost, filed by the engine.

One turn, in order:

1. the model emits action A;
2. **interception** — `canon(A)` is tested against the live set; a match holds A, records a `fires`
   on every matching reminder, and returns the `reminder_hold` observation instead of A's result;
3. **the ops** — `remind` / `remind_feedback` are applied AFTER that check, so a reminder authored
   this turn can never hold the very action it rode on, and the engine note naming what happened
   (`[REMINDERS: added rem-… ]`) rides A's observation.

A `finish` is never held: it does not reach the dispatch path at all (the finish gate is its own
seam, and pre-finish is where the sibling rule-assistance triggers land). Its side fields ARE
applied, before that gate — the last turn of a run is very often the one the engine asked for a
`did`/`didnt` label on, and dropping it there would throw away the evidence the layer exists to
collect. If a finish guard sets the finish aside, the engine note rides the guard's own message.

**Who owns the tally.** `reminders.record` is the only writer of a stat and works off DISK, doing
its own read-modify-write; the engine's in-memory set owns the DEFINITIONS, because this run's ops
are what changed them. `engine/remind._save_local` keeps the halves apart when it rewrites the
file. Collapsing them either way loses data: writing memory's stats back rolls every fire this run
recorded back to its boot-time value, and reading definitions from disk would discard the op that
prompted the write.

Two rules keep the layer from eating the run:

- **One hold per action string per run.** A held `canon` string is remembered, so re-emitting the
  SAME action IS the confirmation to proceed and cannot be held again. This is the shape the
  stopping VERIFIER already uses (at most one challenge per condition per run) and for the same
  reason: a model and a gate that both refuse to yield would livelock a run into a dead budget.
- **One hold per action**, however many reminders match.

The live set is read ONCE, at construction, and kept in step with the run's own ops — the composed
prompt is append-only under the caching contract, so a store that changes between runs never
rewrites a within-run prefix.

A held action executed nothing, and two counters follow from that. It does not count toward
`executed_actions`, so the fabrication guard that rejects a `finish(ok)` before anything ran is not
satisfied by a hold — on a resumed leg too, where the counter is rebuilt from the transcript. And
it does not spend an `allow once` grant: those are spent by USE, not by attempt (D65/D76), and
spending one on a hold would deny the re-emitted action that the hold's own contract calls the
confirmation to proceed.

## The capability

Two dials in `capabilities:`, both user-set like every other:

```yaml
capabilities:
  reminders: none | local | global   # which stores this run reads and may write
  remind_confirm: always | creations | never   # who approves a GLOBAL write
```

`global` means BOTH stores (the union), not the library alone — a routine curating shared cautions
still keeps its own. The layer is OFF by default and has no baseline: unlike run history (whose
`last` depth is always on), a reminder costs a TURN, so it stays off until switched on. When it is
off the two side fields are **projected out of the action schema entirely** (`kindsurface`), so a
run that cannot use them cannot generate them, and `validate_action` refuses one that arrives
anyway — including on an always-available kind like `report`, because the gate rides the FIELD, not
the kind.

`remind_confirm` is its own dial rather than sharing `confirm` (write_util) or `rule_confirm`
(write_rule), for the same reason those two are separate: a new global reminder starts interrupting
routines that never asked for it. `creations` splits the ladder where the blast radius does — a NEW
global reminder asks, revising or deleting one only changes something already approved. A
sub-workflow cannot touch the global store at all (it binds every routine, so it is a top-level
decision), and a global write is committed to the library repo like any other library write.

The conduct prose is the `reminders` permission doc, whose `requires: {reminders: local}` is what
the activation cascade raises and the floor keeps. A denied scope routes to an access request
(`reminders:local` / `reminders:global` are grant entities), so a run that keeps needing the shared
store can ask for it instead of going quiet.

## Guardrails on what a run may store

Two tiers, because they can be checked at different times. At the **write gate** — inside the
schema-retry cycle, so a malformed op is corrected before it becomes a turn rather than dropped
silently afterwards:

- the pattern must compile, be at most `MAX_REGEX_CHARS` (200) long, and must NOT match the empty
  string — `.*` and `(a?)*` would hold every action the run ever takes;
- the caution must say what the consequence IS, at most `MAX_DESCRIPTION_CHARS` (400).

And at **apply time**, reported back as the engine note on that action's observation, because each
needs the live store the validator does not have:

- the local store is capped at `MAX_LOCAL` (40) — a runaway backstop, not a quota: every live
  reminder is tested against every action, so an unbounded store taxes every turn forever;
- a duplicate pattern is refused **within the same store** (revise the one that is there). Across
  stores it is allowed, because a local reminder shadowing a global one with the same pattern IS
  the union's precedence — and promotion passes through exactly that overlap;
- an unknown id, and a `revise` that tries to move a reminder between scopes.

Two more hold regardless of when a reminder arrived: a pattern that stops compiling — a hand-edited
file — never fires rather than raising, and an id that is not a plain `rem-…` id is refused
wherever it would become a filename. The store must not be able to break a run, and the library is
a git-synced multi-writer directory, so a record's own `id` field is untrusted input.

The regex is matched against at most `MATCH_TARGET_CHARS` (2 000) of the canonical string. That
bounds the SUBJECT, not the time: a pathological model-authored pattern can still burn its own
run's turn on backtracking. Python's `re` has no timeout, and the blast radius is the routine that
wrote the pattern, so the bound is deliberately a cheap one rather than a new dependency.

Promotion from local to global is an `add` at global scope, then a `delete` of the local one: the
evidence is per-routine and starts fresh in the new store, which is honest about what the tally
means. A `revise` cannot move a reminder between scopes.

## Where this sits

One rung of the ladder `note → reminder → memory → rule`, each raising ownership and blast radius:
a note is this run's, a reminder is this routine's (or the library's) and fires at a moment, a
memory note is recalled by the routine, a rule binds every holder. It is also one half of a shared
mechanism — a deterministic predicate over the situation that surfaces a caution through the
between-turn feed and lets the model re-decide — whose other authorship faces are library-curated
triggers and the run's own archived history feeding the same interception seam.
