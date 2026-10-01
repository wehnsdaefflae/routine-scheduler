# Consequence reminders — a caution that fires at the action, not at the boot

A **reminder** is `(regex → consequence)`: a pattern over the canonical one-line rendering of an
action, plus the short caution that pattern is worth interrupting for. Before a matching action
executes, the engine HOLDS it — it does not run — shows the model the caution, and lets it decide
again.

The layer exists because the framework's other "learn from surprise" surfaces are all
*just-in-case*. `.memory/` puts its INDEX in the boot digest and asks the model to recall the right
note at the right moment out of a large, always-present context. `note` files a line to
`state/notes.md` that the NEXT run's digest carries forward. The curated rules are advisory prose
in the prompt. All three depend on the model self-detecting relevance; the common failure is
not that the caution is missing — it is that the caution is somewhere in context and does not come
to bear on the turn where it matters. Nothing said "you are about to do the thing that burned you
last time."

A reminder says exactly that — *before* the effect. That timing is the whole feature:
a caution delivered with the observation arrives after the consequence, when nothing can be
avoided. There is deliberately no cheaper passive tier.

## The match target

Everything rests on `engine/actionschema.canon(action)` — THE canonical one-line rendering of an
action, the documented thing a pattern matches:

```
util:fs-ops mv a b            a util call carries its ARGUMENTS (`util:fs-ops` alone
                              cannot tell `mv` from `rm`)
script:store stage --note x   so does a call of the routine's own script
shell: rm -rf build/          the command IS the action
read_file paths=a.md,b.md     the kind that carries a LIST names it
write_file path=state/x.json  every other kind names its identifying field
subruns                       a kind with no identifying field is just itself
```

Nothing else is in it: a file's content, an edit's anchor, a report's other fields never are.
A pattern anchored on a rendering no action has can never fire — 41 of 131 live reminders once
were — so the write gate refuses a literal lead that no form produces (`^script name=…`,
`^edit_file anchor=…`) and names the form that exists (`reminder_checks.canon_problem`).

It is untruncated on purpose: matching a pre-truncated string would silently change what a regex
can see as arguments grow. Matching happens with `re.search` against the first
`MATCH_TARGET_CHARS` (2 000) of it, so a pattern says where it anchors (`^util:fs-ops mv `) rather
than describing a whole line. Precision and recall are only tunable if the match target is stable
and legible — which is why `canon` has ONE implementation, shared by the interceptor and by every
surface that shows a person or a model what matched.

## The two stores

Split by BLAST RADIUS; the routing heuristic follows from it: *if another routine made this
exact call, would the same consequence apply?*

| | local | global (curated) |
|---|---|---|
| lives in | `<routine>/state/reminders.json` | `<library>/reminders/<id>.json` |
| reaches | this routine | its REACH: `universal` — every routine whose action matches; `listed` — the routines whose settings list it (`shared_reminders`) |
| written | autonomously | by the curator, each write approved (`remind_confirm`) |
| about | this routine's files, task, state | the util or the action itself (`universal`), or one kind of work (`listed`) |

A curated reminder declares its reach; a record that does not reaches nobody.
**Universal** is for a consequence any caller of that util or action form meets; its anchor
limits it, because a routine that never makes the call never pays. **Listed** is for a caution
that belongs to one kind of work — a push in a published repository, a job on a shared GPU —
which would tax routines doing unrelated work if it reached everyone; a settings pattern
carries the list for its kind (`docs/patterns.md`) and each follower's own `shared_reminders`
holds it.

The global store is a SEEDED library directory like `rules/` and `permissions/`, so it is in the
repo from its first commit rather than appearing on the first write. The Library tab lists
what is in it — and can remove one. That removal is not a nicety: an approval decides what
gets IN; without the removal nothing could take an entry out again short of editing the repo by
hand. Each routine's own tally survives the removal, because it is that routine's evidence.

### Reading the tallies back

The stats are the whole point of keeping them, yet for one release nothing read them: both
stores are engine-written JSON under `state/`, so reviewing a pattern's precision meant opening
a file on the server. The routine page's **Recipe health** section shows them beside the recipe-version
table, because they answer the same question it does — is this routine's behaviour getting
better — and what changed? One row per reminder in force with this routine's own tally, one row
per rule assist that has fired with its count, and a **delete** button on each LOCAL reminder.

That button is the user's only lever over a local reminder: a run writes one with no approval,
which is what the local rung is for, so without it a bad pattern would keep costing a turn on
every match and the only remedy would be hand-editing `state/reminders.json`. Deleting takes
the tally with it — the tally is about a definition that is going. A curated reminder is the
library's copy and is removed on the Library tab; the row here shows only what this routine
learned about it.

A reminder or a feedback label also renders **on the turn that wrote it**, next to the `note`
pin it rides beside — ⚑ for the reminder, ⚐ for the label. They cost the same nothing and ride
the same any-action seam, so hiding them inside the action-json fold made a run teaching itself
something invisible unless you already suspected it.

Reminders are **born local; shared once earned.** A bad local reminder taxes one routine's
turns; a bad curated one taxes every routine it reaches at its next run, silently. Unsure is
local. The match target usually settles it on its own: a pattern over a util invocation is
probably universal, a pattern over a `path=` into this routine's artifacts is local by nature.

### Curation: how a caution becomes shared

Nothing else looks across the routines' own stores, so the process has an owner: **rules-review**,
the library curator, holds `reminders: global` and runs a `reminders` stage once per pass. It
takes a CENSUS of every reminder on the instance — every routine's store and the curated one,
recounted from the retained transcripts (a util: holds and labels one label per hold, the actions
of its own routine and of every other one its pattern matches, a pattern matching nothing, an
anchor on a util the library no longer holds, a util revised since the reminder was written, and
CLUSTERS of one caution written independently by several routines). Then it triages, fixing the
cause first:

| the census shows | goes to |
|---|---|
| a defect or a trap in one util | an addressed report to global-utils-review; the reminders stay local until the fix ships |
| the engine or an API misbehaving | a report to self-audit |
| a principle of conduct rather than a call | the curator's own rule queue |
| a surprising behaviour whose consequence follows for ANY caller | a curated reminder, reach `universal` |
| a caution for one kind of work | a curated reminder with reach `listed`, plus a report naming the patterns whose routines should list it |
| a dead pattern | a report asking its owner to delete or re-anchor it |

It promotes only on evidence — at least three hold-anchored `would_have`, or two routines that
wrote the same class independently; `could_not` on at most a quarter of the anchored holds; a
match target that is a util or an action form; replayed matches in other routines below about one
per routine-week — and afterwards asks each routine whose local copy the promotion replaces to
delete it (a routine's store is its own). Each pass also reads every curated reminder's outcomes
across the routines it reached: a `could_not` plurality means narrow or delete, a fixed util means
delete.

With both stores active the live set is their **union, deduped by regex, local winning**. Same
regex = the same match class, which is the only "same consequence" test a machine can make;
different regexes are different classes and both are shown — inside ONE hold. Precedence never
multiplies turns.

**The tally is per-routine and lives only in the local file** — for a global reminder too, under
`global_stats`. A global reminder's DEFINITION is curated and shared; the evidence about it is
local, because "did this fire uselessly here" is a question about one routine's work. It also keeps
the library from taking a git commit on every fire, from every routine, concurrently.

## The four-way label — what makes a pattern tunable

Every hold is counted as a `fires`; the model labels how it turned out. Riding an action that
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

**The tally has exactly one automatic reader — and it decides nothing.**
`reminders.looks_too_broad` asks one question of a reminder's own record — after at least
`PRUNE_MIN_FIRES` (5) fires, is `could_not` the plurality? — and when the answer is yes the HOLD
itself carries the evidence: the fire count, the `could_not` count, and the line "revise its regex
or delete it with a `remind` op on this turn". Nothing demotes, retires or deletes a reminder
behind the run's back.

This is not the passive tier the layer refuses. It costs no turn and adds no store: the run is
already stopped, already re-deciding this exact action, and `remind` rides the turn it is about to
take anyway — so the moment the pattern is under review is the moment the evidence about it is
readable. The alternative was what ran for 21 days: 198 holds, 67 of them labelled `could_not` by
the runs themselves, and no code anywhere reading a single one, so the only prune path was the
model spontaneously remembering to go and look. `could_not` is the one label that indicts the
PATTERN rather than the action — `would_have` and `didnt` both describe a reminder doing its job,
which is why a plurality of `could_not`, not a majority of "unhelpful", is the test.

**A label answers one hold, once.** The engine counts the holds of each reminder this run has not
yet had labelled and records a label only against one of them; a label with no hold behind it is
refused in the engine note and changes nothing. Before this, labels were accepted for any live
reminder any number of times: one run labelled a single reminder 75 times, 565 labels sat on 437
fires, and the inflation fell on `would_have`, the very label promotion reads. "This run" is the
hold ledger's own scope (below): a hold from before a restart is still owed its label after it,
because a resumed leg replays the holds and labels in its transcript, in the order the live
turns applied them, before it accepts another.

Labelling is **not** enforced. `remind_feedback` rides every kind, so rejecting an action for
omitting bookkeeping would put the layer in the way of the work — and the schema-storm guard fails
a run whose turns keep needing retries. So the hold demands the label, the engine asks once more
two turns later if it is still owed (`did`/`didnt` can only be known a turn after the action ran),
and what stays unlabelled is visible in the tally.

## The runtime

`rsched/reminders.py` is the store — the record, the two homes, the union, the tally.
`engine/remind.py` is the runtime — interception, the ops, the approval gate. The two side fields
are `remind` (`{op: add|revise|delete, id?, regex?, description?, scope?, reach?}`) and `remind_feedback`
(`{id, label}`), both modelled on `note`: optional on ANY kind, no turn cost, filed by the engine.

One turn, in order:

1. the model emits action A;
2. **interception** — `canon(A)` is tested against the live set; a match holds A, records a `fires`
   on every matching reminder, and returns the `reminder_hold` observation instead of A's result;
3. **the ops** — `remind` / `remind_feedback` are applied AFTER that check, so a reminder authored
   this turn can never hold the very action it rode on; the engine note naming what happened
   (`[REMINDERS: added rem-… ]`) rides A's observation.

A `finish` is never held: it does not reach the dispatch path at all (the finish gate is its
own seam; pre-finish is where a [rule assist](rule-assists.md) lands instead).

The interception itself is shared. `engine/hold.py` computes the canonical string once, asks
each source in precedence order — reminders first, because a pattern this routine learned
about this action is more specific than a standing principle — and returns the FIRST hold, so
the model is stopped once per action however many sources match. The per-run ledger is keyed
`(source, canon)`: on the bare string the two layers would cannibalise each other's single
allowed hold. Its side fields ARE
applied, before that gate — the last turn of a run is very often the one the engine asked for a
`did`/`didnt` label on; dropping it there would throw away the evidence the layer exists to
collect. If a finish guard sets the finish aside, the engine note rides the guard's own message.
The model re-emits that finish with its side fields intact, so each field's payload is applied
at most once per run (`remind.apply_ops`, keyed per FIELD) — except an op whose curated approval
got an ask-back (below): nothing was applied, so the finish that carries it again re-submits it.
And such a finish never STANDS over the operator's question: a finish that stands carries no
observation for their words to ride, so the finish gate sets it aside like a user message that
arrived while finishing (`finishgate.check_finish`, `asked_back`) — except on the spent reserved
finish turn, which ends the run with the approval left open as deferred.

**Who owns the tally.** `reminders.record` is the only writer of a stat and works off DISK, doing
its own read-modify-write; the engine's in-memory set owns the DEFINITIONS, because this run's ops
are what changed them. `engine/remind._save_local` keeps the halves apart when it rewrites the
file. Collapsing them either way loses data: writing memory's stats back rolls every fire this run
recorded back to its boot-time value, while reading definitions from disk would discard the op that
prompted the write.

Two rules keep the layer from eating the run:

- **One hold per action string per run.** A held `canon` string is remembered, so re-emitting the
  SAME action IS the confirmation to proceed and cannot be held again. This is the shape the
  claim VERIFIER already uses (at most one challenge per claimed line per run) and for the same
  reason: a model and a gate that both refuse to yield would livelock a run into a dead budget.
- **One hold per action**, however many reminders match.

"Per run" is the GUARD SCOPE every once-only guard shares (`engine/guardscope.py`): a routine's
whole run, every leg of it — and a conversation's current REPLY, because each reply is a new task.
The ledger lives in memory but is not reset when a run resumes: a resumed leg rebuilds it from the
holds its transcript recorded (a hold's observation needs no extra field — its kind names the
source, `action` the string), so a confirmation given before a restart still stands after it.
Before this, every resumed leg started the ledger empty and held the confirmed action again. In a
conversation the leg after an authored finish — the reply the user read — opens the next reply,
and its holds start fresh; a reply interrupted and resumed is still that reply.

The live set is read ONCE, at construction, then kept in step with the run's own ops — the composed
prompt is append-only under the caching contract, so a store that changes between runs never
rewrites a within-run prefix.

A held action executed nothing; two counters follow from that. It does not count toward
`executed_actions`, so the fabrication guard that rejects a `finish(ok)` before anything ran is not
satisfied by a hold — on a resumed leg too, where the counter is rebuilt from the transcript. And
it does not spend an `allow once` grant: those are spent by USE, not by attempt (D65/D76) —
spending one on a hold would deny the re-emitted action that the hold's own contract calls the
confirmation to proceed.

## The capability

Two settings in `capabilities:`, both the person's per routine — no permission stands behind
either:

```yaml
capabilities:
  reminders: none | local | global   # none: off · local: applies its own and the curated ones
                                     # that reach it, writes its own · global: also writes the
                                     # curated store (the curator's setting)
  remind_confirm: always | creations | never   # who approves a curated write
```

The dial governs AUTHORING. Reading a curated reminder needs no dial of its own: the operator
approved every one of them; its reach says whom it holds.

**`local` is ON by default** (`DEFAULT_CAPABILITIES` and every settings pattern): a caution a
run leaves itself about its own actions is ordinary conduct rather than a privilege — a layer
nobody switches on is a layer that never learns anything. `global` is the curator's — rules-review
holds it — and even there every write needs the operator's approval.

Where the layer is switched off entirely, the two side fields are **projected out of the action schema entirely** (`kindsurface`), so a
run that cannot use them cannot generate them; `validate_action` refuses one that arrives
anyway — including on an always-available kind like `report`, because the gate rides the FIELD, not
the kind.

`remind_confirm` is its own dial rather than sharing `confirm` (write_util) or `rule_confirm`
(write_rule), for the same reason those two are separate: a new curated reminder starts
interrupting routines that never asked for it. The approval question names its reach. `creations` splits the ladder where the blast radius does — a NEW
global reminder asks, revising or deleting one only changes something already approved. A
sub-workflow cannot touch the global store at all (it binds every routine, so it is a top-level
decision); a curated write is committed to the library repo like any other library write.

An **ask-back** on that approval — the run view's reply that decides nothing ("is that pattern
not too broad?") — reaches the run at once, as the `[REMINDERS: …]` note on the action that
carried the op: the operator's words, and the instruction to carry the same op again (as it was
or revised), answering them in that action's `say`. Nothing reaches the curated store on it. The
re-submission replaces the open approval because its subject is the reminder id the question
names (`interact.handle_ask`); no unrelated ask can resolve it. A plain reply that is neither
approve nor decline is still held as a delayed message while the run waits (D38).

A denied scope routes to an access request (`reminders:local` / `reminders:global` are grant
entities), so a run that keeps needing the curated store can ask for it instead of going quiet.

## Guardrails on what a run may store

Two tiers, because they can be checked at different times. At the **write gate** — inside the
schema-retry cycle, so a malformed op is corrected before it becomes a turn rather than dropped
silently afterwards:

- the pattern must compile, be at most `MAX_REGEX_CHARS` (200) long, and must NOT match the empty
  string — `.*` and `(a?)*` would hold every action the run ever takes;
- a literal lead must be one the rendering produces (the match target above);
- the caution must say what the consequence IS, at most `MAX_DESCRIPTION_CHARS` (400);
- a curated `add` names its reach; a local op carries none.

`reminder_checks.py` holds these; the library linter applies the same ones to what is
already in the curated store — an approval is not a syntax check. The Library tab lists every
record there, a record the engine skips included, so a bad one can always be taken out.

And at **apply time**, reported back as the engine note on that action's observation, because each
needs the live store the validator does not have:

- the local store is capped at `MAX_LOCAL` (40) — a runaway backstop, not a quota: every live
  reminder is tested against every action, so an unbounded store taxes every turn forever;
- a duplicate pattern is refused **within the same store** (revise the one that is there). Across
  stores it is allowed, because a local reminder shadowing a global one with the same pattern IS
  the union's precedence — and promotion passes through exactly that overlap. For the curated
  store "the store" is every record in it, not only the ones that reach the writing routine: a
  `listed` reminder it does not list still owns its pattern there, and its id — which a new
  entry once took, overwriting it;
- an unknown id, or a `revise` that tries to move a reminder between scopes;
- a `revise` or `delete` of a CURATED reminder by a routine whose dial stops at `local`. The
  write gate can only ask about the scope an op names, and a revise or delete need not name
  one — so the dial is asked again about the store the target lives in. Without it a routine
  that merely reads the curated store could rewrite or remove an entry every routine reads.

Two more hold regardless of when a reminder arrived: a pattern that stops compiling — a hand-edited
file — never fires rather than raising; an id that is not a plain `rem-…` id is refused
wherever it would become a filename. The store must not be able to break a run; the library is
a git-synced multi-writer directory, so a record's own `id` field is untrusted input.

The regex is matched against at most `MATCH_TARGET_CHARS` (2 000) of the canonical string. That
bounds the SUBJECT, not the time: a pathological model-authored pattern can still burn its own
run's turn on backtracking. Python's `re` has no timeout; the blast radius is the routine that
wrote the pattern, so the bound is deliberately a cheap one rather than a new dependency.

Promotion is an `add` at global scope with its reach, then a `delete` of each local copy: the
evidence is per-routine and starts fresh in the new store, which is honest about what the tally
means. A `revise` cannot move a reminder between scopes.

## Where this sits

One rung of the ladder `note → reminder → memory → rule`, each raising ownership and blast radius:
a note is this run's, a reminder is this routine's (or the library's) and fires at a moment, a
memory note is recalled by the routine, a rule binds every holder. It is also one half of a shared
mechanism — a deterministic predicate over the situation that surfaces a caution through the
between-turn feed and lets the model re-decide — whose other authorship faces are library-curated
triggers and the run's own archived history feeding the same interception seam.
