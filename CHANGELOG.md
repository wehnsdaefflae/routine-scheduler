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

## [0.312.0] — 2026-09-06

### Added — an unmet row on the effective surface names the act and lands you on the control

The panel listed every dependency a routine's setup resolves to, diagnosed each one precisely,
and then abandoned the reader. `FAILS permission:run-history — fails closed: runs=last` told an
operator exactly what was wrong and left them to know, unaided, that `runs=last` is a dial inside
a panel further down the same page.

Each unmet row now carries a `fix` — `{kind, …params}` on the read model, words on the terminal
(`readmodels/remedies.py`, so `rsched validate` and the engine's boot note say the same remedy),
a link in the console. It scrolls to the owning panel, flashes it, and where the row names one
thing to change — a dial, a secret's exposure select — lands on that control rather than the top
of the section. The panel still edits nothing: what changed is that "fixed in the panel that owns
it" stopped being an instruction the reader had to follow alone.

The affordance rides the setup STRIP above the hero too, which is the copy of the row an operator
reads first.

### Added — two controls the surface pointed at and did not have

Three fix kinds offered "switch it off", and nothing anywhere switched a capability off. Dropping
an uncovered capability happened only as an invisible side effect of saving the permissions panel
(the server's raise-then-floor). It is a control now, in the card that reports the row.

`lane_schedule` reports that a routine's own file records a cron its lane will never let it fire
at — and the Schedule panel is disabled in exactly that state, correctly, because the lane decides
when it fires. Clearing the stale cron is a different act from editing a suppressed schedule, and
it has its own control now.

### Fixed — the diagnosis outlived the fix

Nothing re-read the surface after a change, so performing a fix left the row on screen with a live
button aiming at a control the repaint had removed: press drop, press save, read the toast, and be
told you had not done it. Every surface-altering save on the routine page now re-reads once and
paints all three readers.

### Fixed — a capability a DOMAIN hands down is not the routine's to drop

The routine's own save counts inherited permissions for the floor, so a domain-supplied capability
survives it — correctly. The fix now carries provenance and sends the reader to the domain's
editor instead of to a control that would have reported success and changed nothing.

### Fixed — the boot note could end the run it annotates

`_remedy` filled its templates from a hardcoded vocabulary, so a kind naming any other placeholder
raised `KeyError` — which neither the CLI nor the boot path caught, making the note the thing that
killed the run. The placeholders are bound to the vocabulary by a test now, and the function fails
the way its docstring always promised.

### Added — the domain editor covers every key a domain shares

It rendered seven of eleven: `machines`, `models`, `budgets` and `tags` had no control, so a
migrated domain carrying one was invisible and uneditable. All four are there, mounting the
routine page's own controls.

The hints were also wrong in a way that mattered, because the eleven keys default in two
directions: lists UNION with a member's own and cannot be subtracted, mappings merge per key with
the MEMBER winning. Secrets and Connections asserted the union rule over mapping keys — naming
precisely the case that is false, a member carrying its own deny-forever tombstone.

`tests/ui/test_lanes.py::test_every_shareable_key_has_an_editor_block` binds the panel to
`domains.CONFIG_KEYS`, so a twelfth key fails the moment it is declared rather than shipping
invisible for a release.

### Fixed — the registry served a routine's pre-edit config after a domain save

`load_routine` merges the domain block under a routine's own keys, but the memo fingerprinted only
`routine.yaml` and `tuning.yaml` — so a domain edit changed what every member effectively holds
while no member's own file moved, and the console kept answering from before the save. Runs were
never affected: the engine calls `load_routine` directly, which is why only the reader lied.

### Fixed — a question rendered with the inline-only markdown subset

A run lays out what it found before it asks, so a deferred question arrives carrying a GFM table
of counts and a numbered list. Rendered inline those reached the page as literal pipes and
asterisks, in the one place a person has to read carefully enough to decide — while the **bold**
and the `code` around them rendered, which is what made it look like markdown was simply off.

Five surfaces render a question body; four used the inline subset. `md()` is a superset of
`mdInline()`, so the block renderer costs nothing anywhere it replaced it. The routine page's
strip stays a preview — it is a row with an `answer` button beside it, and the whole question is
one click away where it is answered.

## [0.311.0] — 2026-09-06

### Changed — a `group` was four things at once, so the strictest of them quantized the rest

One `group` record decided four unrelated things: WHEN a set of routines fires and in what
order; WHAT config its members inherit (D82); WHO may read and write a shared directory — the
trust boundary domain notes rest on entirely; and WHAT the set is about. Only the first of those
demands exclusivity — a routine in two scheduled groups fires twice — but because all four rode
on one membership list, the strictest axis set the cardinality for the rest. "These five
routines share a permission surface" could not be said without also saying "and they fire
together".

The live instance had been paying for that for months. 14 groups, 31 memberships, and **zero**
routines in more than one group: exclusivity had eaten the whole model. The four `Instance ·`
groups carried byte-identical 294-char config blocks and the two `Professional ·` ones did the
same — D82 exists precisely because N copies of one policy surface drift apart, so the cadence
split had recreated its failure mode inside it. Two groups held a config block and no members
at all. `Instance · Weekly · Utils` fired an empty chain at `0 3 * * 2` for weeks, logging a
chain-done event that reads exactly like a chain whose members all completed. And because the
shared config rode on the membership, moving a routine from the nightly group to the weekly one
silently changed what it was allowed to do: **a timing decision was a permissions decision**,
with nothing anywhere to say so.

There are now three objects, with three cardinalities and three owners:

- a **LANE** (`rsched/lanes.py`, `.control/lanes.json`) — when a set of routines fires and in
  what order, plus its mid-chain-failure policy. At most one per routine, ENFORCED; instance
  state the web RECORDS and the daemon FIRES, like triggers and one-shot schedules.
- a **DOMAIN** (`rsched/domains.py`, `.control/domains.json`) — the shared config block, the
  shared store and the notes boundary. At most one; the routine names it in its OWN
  routine.yaml (`domain:`), which makes the cardinality a fact of the file rather than a rule
  someone has to enforce across a list — and keeps membership somewhere it cannot disagree
  with itself.
- **`tags:`** — what a routine is about. Any number, no behaviour, already on the routine.

Config clustering and the trust boundary stayed ONE object on purpose. They answer the same
question — which routines are close enough to share? — and they have the same cardinality.
Separating them would dissolve the argument that makes a domain note approval-free: a note
cannot leave the domain because the domain's store is in its members' fs roots and nobody
else's. The boundary IS the safety model.

Three things dissolve with the split:

- **The cross-record config merge.** It combined several groups' blocks with "first group wins
  the whole key" while unioning WITHIN a group — two rules that contradict each other — so what
  a routine inherited depended on the order rows happened to sit in a JSON file — and no caller
  could have said what that order was. `config/domainconfig.domain_config_for` looks ONE block
  up by id and merges nothing across records.
- **A store-root lookup whose contract was "usually 0 or 1".** It returns zero or one; the
  cardinality is why.
- **Deleting a lane being a config change.** A lane owns no store and no config, so deleting one
  returns its members to their own crons and changes nothing else about them — which is the
  clearest single sign that timing and sharing were never one job.

Each axis is edited where it belongs. Lanes are rows in the Routines list, because a firing
order over routines belongs beside them — and from a conversation, `manage_lane`, which reaches
no config block and no store. Which domain a routine is IN is on that routine's own page beside
every other per-routine setting, since joining one is an ordinary config save; what the domain
SHARES is one block in the Routines page's own domains section, edited once instead of once per
member. `docs/lanes-domains.md` carries the full argument.

### Migration — `migrate_group_split.py`, three rules

1. **Identical config blocks collapse to ONE domain**, clustered on the block's exact content, so
   the four `Instance ·` copies become one with nothing guessed about intent. The domain's name
   is the leading segment the contributing names shared — `Instance`, `Professional` — which is
   exactly the dimension that had nowhere else to live.
2. **A domain inherits the id of whichever contributing group HOLDS FILES**, so no store
   directory moves and the shared files keep the `group-stores/grp-…` path they already had.
   Routines address these paths in their own memory: one live routine carries
   `READ /control/group-stores/grp-8bfd2aa6/…` as a standing prevention rule it wrote for itself
   after an incident; several more name a store id in a ledger. A moved store would
   silently falsify agent-authored notes instead of failing loudly. The id is an opaque handle;
   nothing parses its prefix.
3. **A group with members but no cron was never a lane** — nothing fired it — so it becomes a
   TAG on its members rather than a lane that could never fire: the user's own categorization
   survives on the axis meant to carry it. That branch reads the CLOCK ALONE, so a group with
   members, a config block and no cron becomes BOTH — its block joins a domain cluster and its
   members are stamped `domain:` as well as tagged.

**Collapsing the four `Instance ·` groups into one domain WIDENS a trust boundary**: the store
that was `library-sync` and `self-audit`'s is now readable and writable by every Instance
member. That is what a domain is for — routines sharing a policy surface share a store — but a
widening is never something to discover later.

## [0.310.0] — 2026-09-05

### Fixed — the reminders layer was switched on nowhere, and three separate places said nothing

0.309.0 shipped the consequence-reminder layer "on by default". It was on for zero of the 32
live routines. `bootstrap._merge_caps` carried a PRIVATE copy of the activation cascade that
knew four of the nine capability keys, so adopting a permission whose `requires:` names any
other DIAL wrote the doc into `permissions:` and left the capability at its default — the doc
held, the capability off, and the engine (which enforces from capabilities alone, by design)
behaving exactly as if the permission had never been adopted. `workflows` and `util_tags` had
been falling through the same hole for longer; `reminders` only made it visible by being the
first dial whose whole point was to arrive on.

The same blindness appeared in two more places, each of which would have caught it:

- the setup surface's "held, but its requires: are not switched on" check knew actions and
  utils only, so a routine holding the doc with the dial off read as READY;
- `DEFAULT_CAPABILITIES` — what a routine with no `capabilities:` block MEANS — omitted the
  dial, so those routines were off too, with nothing to adopt them later.

All three now go through `grants.capabilities_for`, the one cascade, so a dial added tomorrow
reaches every one of them without being added anywhere. A one-shot
`migrate_reminders_rollout.py` converges the routines the old code already wrote, and gives the
live settings templates the two dials the seed now names (seed sync only ever ADDS).

### Fixed — the transcript renderer had no branch for four things it was shown

- HELD actions (`reminder_hold`, `assist_hold`) fell through to raw `JSON.stringify`: the one
  observation whose whole job is to be re-read by a person was the least readable on the page.
- All SIX finish-gate rungs rendered as the fabrication guard, the only one that existed when
  the branch was written — so a run deferred for an open stopping condition was labelled a
  hallucinated completion, which is the opposite diagnosis.
- The background archive (0.308.0) carries no before/after chars, because the digest already
  did the shrinking; it reached the branch that prints a span and said "undefined → undefined
  chars". Abandoned, it said "nothing elided this pass" — the line for a no-op pass.
- A transcript header with no orchestrator block printed "undefined:undefined" where the
  workflow half beside it had always had a fallback.

### Added — the tallies have a surface, and a local reminder can be deleted

`state/reminders.json` and `state/assists.json` were engine-written and read by nothing a
person opens, so reviewing whether a caution earns the turn it costs meant opening a file over
ssh. Both now ride the routine page's health tab beside the recipe-version table, because they
answer the question it asks: is this routine's behaviour getting better, and what changed.

Each LOCAL reminder gets a delete button (`DELETE /api/routines/{slug}/reminders/{rid}`). A run
writes one with no approval — that is the whole point of the local rung — so this is the user's
only lever over one; without it a bad pattern kept costing a turn on every match. A curated
reminder stays the library's copy, removed on the Library tab.

A `remind` or `remind_feedback` now renders on the turn that wrote it, beside the `note` pin it
rides with. They cost the same nothing and ride the same any-action seam, so hiding them in the
action-json fold made a run teaching itself something invisible unless you already suspected it.

### Removed — the synchronous archival path, which production stopped calling in 0.308.0

`compact_to_history` and `history_pointer` had no caller outside their own tests once archival
moved off the hot path: eight tests were pinning a path the daemon never took. Deleted, and the
tests repointed at `archive_middle` and the background flow that actually ships. (Vulture could
not see it — it scans `src` and `tests` together, so a src symbol used only by a test is not
reported.)

### Fixed — D38 covered one approval type of three

`_held_not_settled` matched the literal `"util-approval"`, so an ambiguous reply to a
`rule-approval` or a `reminder-approval` settled as a decision. Both write to the LIBRARY — a
copy every holder reads at its next run — which is the last place a "hmm, maybe" should count
as yes. Every approval qtype is named now.

### Fixed — `lint_all` skipped two of the six directories the library holds

The settings templates (whose linter existed, orphaned, called only by its own tests) and the
shared reminder store. `rsched lint` is what says the library is clean, so a directory it skips
is a directory nobody checks.

## [Unreleased]

## [0.309.0] — 2026-09-05

### The Library page is the whole library, and reminders are on by default

Three gaps, all pointing the same way: a feature that exists but cannot be seen or reached is
not really shipped.

**The Library page was missing two of its own kinds.** Reminders had no section — I never built
one. Settings TEMPLATES had none either, and had not for as long as they have existed: they were
in the `/api/library` payload, read by the routine page's picker, and simply never rendered on
the page named after the library. Both are sections now, and a test asserts the page reads every
kind the API returns and names each one in its counts line, so a third cannot go quiet the same
way.

**A rule's assists were invisible.** Six rules now carry a machine-checked trigger — one of them
holds an action before it runs, two defer a finish — and the rules list showed the same row it
always had. A rules listing that omits that describes a rule which no longer exists. Each row now
carries a chip per assist (`moment · payload`, the operative line on hover).

**The curated reminder store is a real library directory, and it can be emptied.** It used to
spring into existence on the first write; it is now seeded like `rules/` and `permissions/`, so
it is in the repo from the first commit with a README stating the record shape and the routing
rule. The Library tab lists what is in it and can remove one — and that removal is not a nicety:
an approval decides what gets IN, so without it nothing could take an entry out again short of
editing the library repo by hand. Removing one leaves each routine's own tally alone, because
that is the routine's evidence about what fired, not the library's.

**`reminders: local` is now held by default.** It sat behind an opt-in nobody had taken, which
made the whole layer dormant: not one routine had it switched on. A caution a run leaves itself
about its own actions is ordinary conduct rather than a privilege — it costs a turn only when it
actually fires, on a pattern that run wrote itself — and a layer nobody switches on is a layer
that never learns anything. `ADOPT_PERMISSIONS` gives it once, at boot, to the routines that
already exist.

`global` is emphatically NOT on by default. That store reaches every capable routine, so it
still needs the dial raised deliberately and every write still needs the user's approval. Born
local, global is earned.


## [0.308.0] — 2026-09-05

### The archive is built off the hot path — without becoming summarize-and-replace

Compaction's good tier hands the elided middle to a model and waits 180–600 seconds for a set
of navigable files back. That wait came out of the run, in the middle of its work, at the
moment it was busiest.

The two tiers are now split in TIME rather than traded off against each other. The
deterministic digest — one line per elided turn — lands instantly and the run carries straight
on; the archival runs in a daemon thread against the middle that was just elided; when it
finishes, the run is told where the navigable history is.

**The guardrail this is built around, because it is the whole point.** The mainstream instant
compaction is instant *because* it is summarize-and-replace, which is lossy — detail survives
only in scrollback. That speed is not worth that mechanism, and this does not adopt it. Nothing
here summarizes anything away: the transcript keeps every byte as it always did, the archive is
still built losslessly from the real middle, and the digest is a **placeholder in the prompt**
for the minute the archive takes, not the product. What changed is when the run gets to keep
working, not what it ends up with.

The note is **appended, never swapped in**. Rewriting the digest message would be a second
rewrite of the prefix and a second cache invalidation for a single compaction; appending costs
nothing and keeps the message list append-only, which is the contract everywhere else in the
engine.

A run that FINISHES inside the archival window would otherwise drop its archive on the floor —
and that archive still has readers after the run ends, since the search index covers
`history/`. So a finishing run settles it: a few seconds' wait, where the run's work is over
and nothing is waiting on the model. Not longer, because a conversation's reply is rendered
from the finish and the 180–600s stall must not come back through that door. An archive that
needs more than that is abandoned with the reason recorded — the thread is a daemon and
`_swap_in_history` is two atomic renames with a restore on failure, so an abandoned archive
cannot leave a half-written one behind, and the digest plus the full transcript are what remain.
That is precisely the degraded fallback the synchronous path already had, reached by a
different route.

Also: `mode` says WHAT a compaction was and a separate `background` flag says how it ran —
overloading the one field made the background event claim to be a mode that does not exist.

New: `engine/archival.py`, `compaction.archive_middle` (the archival call split out so it can
run in a thread: it touches no message list, only the model and the filesystem). 10 tests in
`tests/test_archival.py`.


## [0.307.0] — 2026-09-05

### The history index was a map to files that do not exist

Compaction archives the elided middle losslessly to `runs/<ts>/history/` and leaves the prompt
a pointer at `INDEX.md`. Measured on the live instance, that index was addressing files that
were not there. One archive cited 102 filenames of which **zero** resolved; **36% of every
named history read across the instance returned ENOENT**; runs paid a stereotyped three-turn
recovery — read INDEX, read the bare names and fail, read the prefixed names — again and again
in the same run. Two archives had an `INDEX.md` whose entire content was the string `INDEX.md`.

The cause was a split ownership nobody had noticed. The archival prompt asked the model to name
its files by topic and to write the index against those names; `_swap_in_history` then renamed
every file to `t<turn>-<slug>.md`. The index and the filesystem could never agree.

**The engine writes the filenames, so the engine owns the index.** The model now supplies each
file's content and a one-line `about`, and never names a file at all — exactly the split that
has always governed `.memory/INDEX.md`, where each write supplies `about` and the engine
maintains the index. The invariant is now checkable and checked: every file in the archive has
exactly one index line, under the name it was actually written with.

Two more defects went with it. Entries from earlier passes are carried forward by the ENGINE —
the model used to be asked to "KEEP its entries" and silently dropped them, leaving one live
archive listing 13 of its 23 files with an entire generation gone. And the prior index is no
longer re-fed to the model, which had grown the archival prompt to 20 KB by a run's 23rd pass.

### One turn to externalize, before the middle is evicted

Retention is positional — head 6, tail 24 — so a load-bearing fact in the middle survives only
in `history/`, reachable if the run remembers to go looking. `note`, `memory_write` and the
LEDGER already exist to carry a fact out of the conversation; what was missing was the moment
to use them, which is the one thing this layer exists to supply. The archive is now deferred by
exactly one turn and the run is told what is about to happen and what to do about it.

Deferring is safe by construction, which is what makes it cheap. The gate is
`min(fraction × window, ceiling)`: when the FRACTION binds there is 20–40% of the window before
the hard ceiling — a whole turn of slack — and when the CEILING binds there is none, so the
warning is skipped and the archive happens immediately. `clamp_to_cap` runs unconditionally
afterwards either way, so a deferred turn cannot 400. Once per run: a second warning would be
the layer talking about itself.

### The archive becomes a store the relevance layer surfaces from

`engine/recall.py` — when what the run just did overlaps an archived topic, the observation
tail names ONE file and the turn it was archived at. Free (it rides a message the run was
getting anyway) and a pointer rather than a fetch: the run decides whether the file is worth a
turn. Not a rule assist, because an assist's payload is a static line authored in frontmatter
and this one is computed.

Its worth is measured rather than assumed. Over four real archived runs, scoring the canonical
action string plus the surrounding narration against the index: at least one useful file in the
top 3 for **23%** of the moments a run actually went looking, 37% in the top 5. Split by archive
size, that average hides the real result — archives up to ~23 files hit the top 3 for 7 of 8
moments, while a 101-file archive managed 1 of 22. Deterministic overlap works while the archive
is small and degrades as it grows. So it surfaces one file above a floor score rather than a
list (a wrong pointer costs a read AND teaches the run to ignore the layer), with a cooldown
between pointers. The semantic path stays behind evidence that the cheap one is insufficient.

This is also why the index had to be fixed first: a pointer built on an index whose addresses
were all wrong would have inherited exactly that 36%.

Each layer of the trigger runtime now initialises its own run state (`configure(loop)`) —
`loopsetup.configure` crossed the statement ceiling, which was the ratchet asking why one
function knew the field names of four different layers.

New: `engine/recall.py`. 12 tests in `tests/test_recall.py`, plus the index invariant in
`tests/test_composer.py`.


## [0.306.0] — 2026-09-05

### One seam for every hold, and the rule that stops an action

The two halves of the relevance-trigger layer both need to stop an action before it runs — a
consequence reminder this routine learned (0.304.0) and now a general rule whose moment the
action IS (0.305.0). They go through ONE interception (`engine/hold.py`), extracted when the
second caller appeared rather than in advance of it, because two things go wrong if each layer
owns its own.

**The ledger key.** A hold is remembered so that re-emitting the action IS the confirmation to
proceed. Keyed on the bare action string — as it was — a reminder holding `util:fs-ops mv a b`
would silently spend the rule layer's only hold on that same string, and the rule's caution
would never be seen at all. The key now carries the SOURCE, so each layer has its own
one-hold-per-action budget.

**One interruption per action.** However many sources match, the model is stopped once: the
anti-livelock reasoning — a model and a gate that both refuse to yield burn the budget between
them — applies to the PAIR, not to each layer separately. Precedence resolves it rather than
queueing a second hold behind the first, and it is specific-before-general: a reminder is
evidence this routine gathered about this action, a rule is a principle that applies to
everyone, so when both fire the run hears the one it learned itself.

`hold.is_hold(obs)` replaces a string literal that was tested in three modules, one of them
NEGATIVELY (the resume rebuild of `executed_actions`) — precisely the check a second hold kind
walks straight past, letting a held action re-ground the fabrication guard it was excluded
from. The hold wording moved out of the flat observation renderer into `engine/obs_hold.py`,
the pattern the per-domain formatters already establish.

**The `pre-action` moment, and why it can only HOLD.** Once an action is emitted, stopping it
is the only way to reach the model while the caution can still matter — "remind and let it
run" is not expressible there. So the moment and the payload are COUPLED and the linter
enforces it: `pre-action` carries only `hold`, and the free moments (which have no action in
hand) carry only `remind`. A rule that pairs them wrongly is refused at authoring time instead
of silently never firing.

Three more rules declare their moment, taking the set to six across all four moments and both
built payloads:

- **git-checkpoint** (pre-action, HOLD) — the rung the design note reserves for a crisp
  pre-action predicate with an irreversible cost to skipping. The engine autocommits its own
  working directory at run end; a project repo the routine was granted a write root into has
  no undo point unless the run makes one. It fires on the FIRST such write only, which is what
  makes "no checkpoint yet" true without having to DETECT a checkpoint commit — that happens
  inside a util or a shell command, where the engine sees a command string and an exit code
  and nothing more. Overridable like every payload: re-emit the action and it runs.
- **ask-policy** (boundary) — several decisions now waiting on the user, read off
  `ctx.asks_deferred`, the churn telemetry that already counts a decision thrown over the wall.
- **unexamined-is-not-clean** (pre-finish) — a summary that reports all-clear and names no
  number. Deliberately crude: a summary that quantifies ANYTHING passes, because one that
  judged whether the denominator was the RIGHT one would be grading the reasoning again, which
  is the thing that cannot be done.

The one-shot migration learns the second batch, so all six reach a live library.

New: `engine/hold.py`, `engine/obs_hold.py`. 47 tests in `tests/test_assists.py`.


## [0.305.0] — 2026-09-05

### The rule that notices its own moment — assists

The 26 curated general rules are advisory prose enforced by nothing, and the realistic failure
is not a run that refuses one. It is a run that means to follow a rule and forgets it at the
moment it applies. Most of a rule's length is scaffolding for that noticing — the "when" the
model has to hold and match against the situation in front of it — and holding it is the part
that fails.

An **assist** takes the noticing off the model. A rule declares `(moment, predicate) → line`
entries in its own frontmatter, and the engine surfaces the operative line exactly when the
rule becomes relevant. This is the library-curated half of the relevance-trigger layer the
consequence reminders (0.304.0) are the self-authored half of — same mechanism, different
author.

**Why timing rather than enforcement, stated plainly, because it is what makes this the
primary mechanic and not a consolation prize.** A mechanical check is a function of the trace
a run leaves behind, so it can separate compliance from violation only when the two leave
DIFFERENT traces — and for most rules they do not. Two runs face the same failing test: one
traces the cause and installs a general prevention, the other patches the symptom. Both leave
the same diff, the same green check, the same plausible ledger entry. Whether the fix
addressed the cause is a fact about the FUTURE inputs the diff will face, and no predicate
over the trace can see it. Enforcement would have to grade the reasoning, and reasoning is
never in the trace. Assistance only has to detect the moment the rule becomes relevant — and
relevance is a property of the SITUATION, which IS in the trace. **The rule you can never
check, you can still time.** That is why every rule has a usable assist, including the
judgment-tier ones with no compliance check at all: the impossibility result blocked grading,
not timing.

The rule does not shrink, it FACTORS: the trigger moves off the model, the operative line
becomes the surfaced payload, and the full rationale stays in the body where `read_rule`
reaches it — which the surfaced line always names, because terseness is only honest if the
rest is reachable. The line is its own authored field, never auto-excerpted from the body: the
caveat-heavy rules are exactly the ones a machine-made excerpt would misrepresent.

Three moments, and they cost differently. `observation` rides the tail of the observation the
run was getting anyway; `boundary` is an appended ENGINE NOTE through the same carrier a
mid-run rule binding already uses. Both are free and append-only, so the caching contract
holds. `pre-finish` costs a turn and has to — a line surfaced as a run ends is one nobody can
act on — so it is a finish-gate rung carrying the two guards its neighbours have (never a
child run, never the reserved finish turn) plus its own: a run is held at its finish by an
assist **at most once, ever**. A rule may ask for an ending to be reconsidered; it may not
negotiate over it.

There is deliberately no `pre-action` moment yet. Where an action is chosen but not executed,
the only way to reach the model is to HOLD it — "remind and let it run" is not expressible
there — so pre-action arrives with the hold payload, and it will feed the seam the consequence
reminders already own rather than a second one.

An assist fires at most once per run, the rule `reminder_held` and the stopping verifier's
`_challenged` set already apply to their own interventions and for the same reason. A
predicate that raises is inert, never fatal: a library document names the check, and the run's
work is not this layer's to lose. Every fire is counted per assist in `state/assists.json` —
just a counter, because at the `remind` rung nothing is spent and there is no confusion matrix
to fill in; what it answers is which triggers fire and how often, so an imprecise one is
visible before anyone promotes it to a rung that costs turns.

Not a capability: holding the rule IS the decision, and an assist changes only when its line
is read. A predicate is named, never shipped — a rule is prose in a git-synced multi-writer
directory — and `lint_rule_text` validates the block, so one check covers `write_rule`, the
Library PUT, `rsched lint` and the Library page.

Also here: `observations.is_failure`, one answer to "did that fail?" for a question every
consumer of an observation eventually asks, where failure had been spelled per kind (an exit
code here, an `error` string there, `missing`/`rejected`/`declined_secrets` elsewhere).

The first three, one per moment: **error-recovery** (a call just failed), **intent-inference**
(the user just spoke to a run in flight), **decision-record** (this run is ending without a
ledger entry). Because the seed sync is add-only and all 26 rules already exist live, a
frontmatter block reaches no instance on its own — `migrate_rule_assists` carries these three
across, skipping any rule an operator has edited and naming everything it skips.

New: `rsched/assists.py`, `engine/assist.py`, `engine/assist_predicates.py`,
`migrate_rule_assists.py`, `docs/rule-assists.md`. 36 tests in `tests/test_assists.py`.


## [0.304.0] — 2026-09-05

### A caution that fires at the action, not at the boot — consequence reminders

Models repeat mistakes. A run takes an action, the action has an unintended effect, and a later
run takes a materially identical action and hits the same effect again. Every surface this system
had for "learn from a surprise" is *just-in-case*: `.memory/` puts its index in the boot digest and
asks the model to recall the right note out of a large, always-present context; `note` files a line
that the NEXT run's digest carries forward; the curated rules are advisory prose in the prompt. All
three depend on the model self-detecting relevance, and the failure was never that the caution was
missing — it was somewhere in context and did not come to bear on the turn where it mattered.
Nothing said *you are about to do the thing that burned you last time*.

**A reminder is `(regex → consequence)`, and it is checked BEFORE the action runs.** On the same
turn a run notices an unintended effect it carries a `remind` — the pattern matching the class of
calls that can cause it, plus the caution to show then — and from then on a matching action is
HELD: it does not execute, the caution is put in front of the model, and it decides again. The
pattern matches `actionschema.canon` (0.300.0), the one canonical rendering of an action, which is
why that had to be pinned first: precision and recall are only tunable if the match target is
stable and legible.

Pre-execution is the entire feature, and the passive tier that would have made it cheaper is
deliberately absent: a caution delivered with the observation arrives after the consequence, when
nothing can be avoided. The turn cost is paid on the INPUT side instead — a narrow pattern,
selective authoring, and the tally below — never by a cheaper output.

**The four-way label is what makes a pattern tunable**, so it shipped in the same increment rather
than after it. Every hold is counted as a `fires`, and `remind_feedback` labels how it turned out:
`could_not` (the consequence was impossible for that action — a false positive, narrow the regex) ·
`would_have` (it was on track and the hold avoided it — the value delivered) · `did` (the run went
ahead and it happened) · `didnt` (the run went ahead and nothing bad happened). False-positive rate
is `could_not / fires`, cost is `fires` at one turn each, and `fires - Σlabels` is the count of
holds nobody labelled — which is itself the signal that the layer is being paid for and not read.
Labelling is not enforced: the field rides every kind, so rejecting an action for omitting
bookkeeping would put the layer in the way of the work, and the schema-storm guard fails a run
whose turns keep needing retries. The hold demands the label and the engine asks once more two
turns later — long enough for a `did`/`didnt` to know its own outcome.

**Two stores, split by blast radius.** A routine's own `state/reminders.json` is autonomous — a bad
local reminder taxes one routine's turns. The library's `reminders/` is curated and shared, and a
bad entry there taxes every capable routine at its next run, silently, so it is written under its
own approval dial `remind_confirm` (`always | creations | never`, the `write_rule` ladder, split
where the blast radius splits: a NEW global reminder starts interrupting routines that never asked
for it). Both active means the union, deduped by REGEX with local winning — same regex is the only
"same consequence class" test a machine can make; different regexes are different classes and both
are shown, inside ONE hold. Precedence never multiplies turns. The TALLY stays per-routine even for
a library reminder: the definition is shared, the evidence about it is local, and keeping it out of
the library also keeps the library from taking a git commit on every fire from every routine.

Two rules keep the layer from eating a run. **One hold per action string per run** — so re-emitting
the SAME action IS the confirmation to proceed, and cannot be held again; this is the shape the
stopping verifier already uses (at most one challenge per condition per run) and for the same
reason, since a model and a gate that both refuse to yield would livelock the run into a dead
budget. And **one hold per action**, however many reminders match.

The layer is a capability, off by default and with no baseline — run history floors at `last` for
every routine because reading the previous result costs nothing, and a reminder costs a TURN.
`reminders: none | local | global` says which stores a run reads and may write; when it is off the
two side fields are projected out of the action schema entirely, so a run that cannot use them
cannot generate them, and `validate_action` refuses one that arrives anyway — including on an
always-available kind like `report`, because this gate rides the FIELD, not the kind. A denied
scope routes to an access request like every other capability. The write gate runs inside the
schema-retry cycle, so a pattern that does not compile, is over 200 characters, or matches the
EMPTY STRING (`.*` would hold every action the run ever takes) is corrected before it becomes a
turn instead of being dropped silently afterwards.

New: `rsched/reminders.py` (the store), `engine/remind.py` (interception, the ops, the approval
gate), the `reminders` permission doc, and `docs/reminders.md`.


## [0.303.0] — 2026-09-05

### The other three sidebars become resizable and hideable (F441 complete)

0.289.0 shipped `static/resizable.js` and wired the first of the four sidebars the operator
asked for on 2026-09-04 — the main navigation rail — and then stopped, because the
conversation that raised it asked for the operator's ordering before the next surface. The
remaining three are here, on that same primitive and its same invariant: the module writes a
sidebar's width ONLY as a custom property and its hidden state ONLY as a class, so each
stylesheet's responsive collapse stays authoritative.

- **The routine page's recipe file-tree** (`.recipe-navcol`). Its grip is a flex sibling on the
  column's own border, with negative margins that give back exactly the two extra flex gaps it
  would otherwise add — so the gutter between tree and editor is unchanged, and the grip holds
  its place when the column is hidden.
- **The run and conversation views' LEFT and RIGHT rails**, which live in two different wide
  layouts: fixed cards in the viewport margins at ≥1900px, sticky grid columns beside the chat
  at 1100–1899px. Both read the same pair of width properties, so ONE dragged width follows a
  rail across the breakpoint — resizable.js writes those properties inline on `<html>`, which
  outranks the margin mode's own `calc()` default without either layout having to know about
  the other.

Each grip is a SIBLING of its rail, never a child. A rail is its own scroll container
(`overflow-y: auto`) and clips anything sitting on its border, so a grip inside one is both
invisible and unclickable — the first cut of this had exactly that bug, and the browser tests
caught it. Outside, the grip is positioned by the rail's own width property, which is what lets
a hidden rail be removed outright and still be brought back.

Two things are pinned per surface in `tests/ui/test_sidebar_resize.py`, because both are silent
when broken: the grip is `display: none` in the narrow layout, and a sidebar hidden on a wide
screen must come BACK there — with no grip to click, a stored hidden state would otherwise be
unrecoverable. A hidden rail also leaves the conversation grid's template rather than holding a
zero-width column, or the chat would be auto-placed into the empty one.

The finding also named the settings nav. That one is a horizontal `.filterbar`, not a column,
so there is nothing there to resize; the surface count is three, not four.

## [0.301.0] — 2026-09-05

### A confirmed creation stops asking for a second "go"; a deleted routine stops locking its group

Two reported defects, both where a proxy stood in for the fact it was meant to represent.

**`create_routine` charged the user a whole round-trip after they had already said yes (R1310).**
The confirm gate exists to make sure a human has SEEN the draft before a routine is built, and it
tested for that with the engine PID: a conversation reply runs as its own process, so a draft this
process wrote could not be confirmed by this process. That is true of the ordinary path and false
of the one the orchestrator actually reaches for — a BLOCKING `ask_user` is answered *inside* the
drafting leg. The user answered "Create it now", the orchestrator emitted the materializing call,
the gate read the pid and held it, and the reply had to end asking them to send a content-free
"go". The gate now asks the question it always meant: **has the user spoken since the draft?**
`RunContext.user_replies` counts this leg's user utterances — a settled blocking answer, a held
reply, a dialog turn, an injected message, a slash command, but never a delivered report, which is
a routine's message and not the user's — the draft records it beside its pid, and the same leg may
confirm once the count has grown. A redraft re-records the count, so a design change after an
answer still costs a fresh round-trip: that answer confirmed the old draft. The kind surface and
the draft observation now teach the second option out loud — finish the reply, or put the go-ahead
itself to them as a blocking ask and confirm on their answer.

**One out-of-band routine deletion locked a whole group against every edit (F442).** Routines are
deleted by hand — there is no delete endpoint for a cascade to hang off — so `.control/groups.json`
can name a slug that no longer resolves. `api_groups._validate_members` checked the WHOLE submitted
member list against the live registry, and both the routine page and the dashboard send the members
they are KEEPING alongside the one they are changing: joining or leaving a group that held a stale
slug returned `400 unknown routine(s): clarification` and the console showed the toast with nothing
the user could do about it. Existence is now checked on the slugs a caller **adds**; a slug the
group already holds rides along untouched, so repairing the group is no longer a precondition for
editing it, and a slug that was never a routine is still refused. The stale member is surfaced
where a human can act on it instead: `rsched validate` grew a second instance-level check —
alongside "a scheduled group with no members" — naming every group member that is not a routine.
The chain already tolerated one (it logs and skips), so nothing about the fire path changes.

Swept with it: the F292 two-pass `split` member flag, retired in 0.205.0 (D90), was still being
sent by the routine page's group picker and still documented as part of the member record.


## [0.300.0] — 2026-09-05

### One canonical rendering of an action — the match target three queued features are built on

All three feature requests in the queue (consequence reminders, rule assistance, compaction
recall) rest on the same mechanic: a deterministic predicate over "what is this run about to do".
The reminders doc says to pin the thing that predicate matches against FIRST, and calls it
make-or-break, because precision and recall are only tunable if the match target is stable and
legible. It was neither.

**The same one-line rendering of an action was being derived six different ways**: three copies of
the `BRIEF_FIELD` lookup with three different truncations (`loop._record_turn` at 80, the admin
audit line at 200, `history.read_transcript` at 80), a *fourth* rule in `notes.py` that used
name/path/paths and so stamped a bare kind with no target at all for every kind whose identifying
field is neither — `llm`, `ask_user`, `report`, `shell` — and a richer JS version in the transcript
component that had already drifted ten kinds behind once.

- **`actionschema.canon(action)`** is now that rendering, and the documented match target:
  `util:fs-ops mv a b` · `shell: rm -rf build/` · `read_file paths=a.md,b.md` ·
  `write_file path=state/x.json` · `wait`. A util carries its ARGUMENTS, because `util:fs-ops`
  alone cannot tell `mv` from `rm` — which is exactly the distinction a caution would be written
  about. `read_file` renders its LIST rather than the singular field its `BRIEF_FIELD` entry names.
  A kind with no identifying field is just itself, never a dangling `kind=`.
- **Untruncated on purpose.** Callers apply their own widths. Matching a pre-truncated string would
  silently change what a regex can see as an action's arguments grow — the target must not move
  under the reminders written against it.
- **`actionschema.brief_value(action)`** is the other half: the field VALUE alone, which is what
  the three turn-recording sites actually wanted (they store the kind separately). Their widths are
  unchanged; only the derivation is shared.
- The note stamp changes shape as a result (`util websearch` → `util:websearch`,
  `read_file state/x.md` → `read_file path=state/x.md`) and now carries a target for the kinds it
  used to drop.

No behaviour beyond that: this is the foundation, not the feature. Nothing matches against it yet.

## [0.299.0] — 2026-09-05

### Compaction happens between steps, not in the middle of one

First increment of the compaction-recall/timing request, and the one it names first because it
depends on nothing else: the trigger moves, no new machinery.

The compaction gate is a SIZE check. It is indifferent to *where* in the work it trips, so it can
rewrite the prompt's prefix three actions into a multi-action step — the worst possible moment for
both coherence and the provider cache, which the rewrite invalidates outright.

- **`compaction.ANTICIPATE_AT` (0.85).** At a boundary the engine ALREADY detects — the run
  entering a new stage module, i.e. `ctx.phase` changing on a `stages/<name>.md` read — a prompt
  merely APPROACHING the gate is archived early. The clean between-steps pass pre-empts the forced
  mid-step one. No boundary is added: the phase is set where it always was (`engine/fileops.py`),
  and the check is a pure read of it.
- **Only WHEN moves, never WHETHER.** Every anti-thrash guard still applies — the incompressible
  head+tail floor, a middle under 8 messages, less than 20k of growth since the last pass — so a
  boundary can never cause a compaction the ordinary gate would not eventually have made. Pinned by
  a test that puts a boundary in front of a zero-message middle and asserts nothing happens.
- **The pass is stamped `anticipated: <phase>`** in its `compaction` transcript event. Without it an
  early pass and a forced one are indistinguishable after the fact, and the feature could not be
  evaluated against the runs it is supposed to improve.

*Not* adopted, deliberately: the mainstream summarize-and-replace compaction that would make this
instant. That is lossy — detail survives only in scrollback — and losslessness is the property this
archive exists for. Making archival feel instant is a separate increment (background archival with
the deterministic digest standing in), and it keeps archive-not-summarize as the default.

## [0.298.1] — 2026-09-05

### The machine queue could never read its box

Found by pointing 0.298.0 at the real machine rather than by testing it. `machine_queue.refresh`
assembled the `remote` util's two env vars by hand and keyed the private-key dict by `key_var`
(`PREDATOR_AGENT_KEY`) when the util's contract is `{machine NAME: PEM}`. So every read failed with
"machine 'predator' has no private key available", every mirror recorded an error, and every bound
run was told `COMPUTE QUEUE UNKNOWN` — indefinitely, and looking exactly like a network problem.

`refresh` now goes through `machines.resolve_machines`, which already owns both shapes and is what
the engine itself uses to bind a machine. One resolver, one contract. Pinned by a regression test
asserting the util receives the PEM keyed by machine name.

The failure did behave correctly while it lasted, which is the one consolation: an unreadable queue
reported UNKNOWN and never *free*, so no run was told the GPU was available when it did not know.

## [0.298.0] — 2026-09-05

### A GPU box's compute is QUEUED, in turns — not locked, and not first-come

Operator, 2026-09-05: *"routines that use the gpu on the external predator often seem to block the
gpu for one another… i would prefer they found a way to schedule it so everyone gets their turn."*

**What was actually contended was not the run — it was the detached JOB.** All three predator
routines launch work with `remote submit`, which returns at once and leaves a process on the box
for hours. That is why the obvious fix was already in place and already failing: voice-model-trainer
and funscript-trainer are both in the `Labs` group, whose chain is strictly sequential, and they
still collided — member 0 finishes in minutes and leaves a training job on the card that member 1
walks into. eye-stabilize-folder sits in a different group and cannot see either of them. Facing
that vacuum the routines had invented **three incompatible lease protocols** of their own, one of
them living inside another routine's `scripts/`, and one run had to reclaim an 18-hour-stale lease
by hand.

- **`MachineConfig.exclusive`** makes `remote submit` take a QUEUE TICKET instead of launching.
  Nothing else changes: every other verb, and every non-exclusive machine, behaves exactly as
  before.
- **Fair share, not FIFO.** Round-robin across ROUTINES by each routine's oldest waiting ticket,
  FIFO within one — so three jobs from one routine interleave with another's single job as
  `f1, v1, f2, f3` rather than making it wait for all three. The definition lives once in
  `rsched/machine_queue.fair_share_order` and the `remote` util ships that exact function to the
  box, so the two halves cannot drift.
- **Nobody blocks.** `submit` returns a job id and a queue position immediately; the run reads its
  place in the CAPABILITIES section and can spend the run on work that does not need the machine.
- **Every ticket carries a mandatory deadline.** A detached job has no live process to heartbeat
  against, so a wall clock is the only thing that makes the queue self-healing; past it the job is
  killed with its process group and the ticket pruned.
- **The truth is ON THE BOX** — tickets under the machine's own job root, enforced by the util at
  the one place that opens an SSH connection, so it survives a daemon restart, a container
  recreate and an instance migration. The daemon only MIRRORS it into
  `.control/machine-queue/<name>.json` each tick. A machine that cannot be read says **UNKNOWN**,
  never *free*: an unreachable box reading as free is the single failure mode that would cause the
  very collision this prevents.
- **One defect worth recording**, found by the util's own end-to-end harness rather than by
  reasoning: re-deriving the fair-share order over the *shrinking live set* silently collapses to
  FIFO, because deleting the ticket that just ran also deletes the evidence that its holder used a
  turn. The box therefore retires a spent ticket into `round/` and orders over `spent + live`, and
  the read model no longer re-sorts at all — it reads the order the box returned. Pinned by a
  regression test.

Cooperative, like every other machine guard, and the docs say so: a human on the box, or a `shell`
action, still bypasses it.

*Not yet switched on:* `predator` needs `exclusive: true` in Settings → Machines after this
deploys. Until then `submit` launches directly, exactly as today.

## [0.297.0] — 2026-09-05

### Routines stop pacing themselves: the arity caps go, and creation asks what ENDS a routine

0.293.0 fixed the engine's half of the operator's "steps way smaller than necessary". This is the
other half — the prose that actually bounded the work, and the generation prompts that wrote it.

The evidence: goal-directed runs habitually finished far under budget — voice-model-trainer's last
six runs used 75 → 57 → 44 → 31 → 30 → 19 of 200 turns, bina 21–45 of 200 — because a per-run
ARITY cap, not the budget, was the binding constraint.

- **The generation prompts are the cause, so they changed first.** `workflows/pipeline.py` mandated
  a `## Completion criteria` section and said NOTHING about its content, which is how 12 of 31 live
  recipes ended up with no statement of their own end at all. It now requires two labelled halves —
  `**Per run:**` and `**Overall:**` — where the overall names a concrete terminal artefact in one
  sentence, names a literal DATE where the task has one, and must say `PERPETUAL` explicitly (with
  what makes it never end) when there is genuinely no end. It must also be repeated in the recipe's
  OPENING paragraph, because a goal in a trailing section is read last. `check_main` enforces both
  literals — the right place for a machine check, unlike a name-match over dynamic prose.
- **One standing rule, in both generation prompts**: never write a per-run arity ("exactly one
  increment", "a few", "at most N", "so no run tries everything") UNLESS the work is genuinely
  serialized by something outside the run — and then NAME that thing. An external constraint stated
  is a boundary a run can reason about; an arity asserted is a brake that binds long after its
  reason is gone. Plus: say what makes a run EXIT EARLY, and make the LAST phase one `main()`
  actually branches into (`wind-down` and `wrap-up` sat in two patterns and five recipes for months
  while appearing nowhere in the engine).
- **Five library patterns rewritten**, seed and live library both (the boot sync only adds, so an
  edit in one never reaches the other): `steward-project-feedback-site` (6 holders — the arity was
  in nine places, and its "when AHEAD of plan, spend the run HARDENING… not racing future
  milestones" is deleted outright), `cumulative-feedback-research-site` (`BATCH_RANGE` was a
  ceiling "so no run tries everything"; now a FLOOR), `improvement-proposer` (`CAP_PER_AXIS` gone —
  a cap on an already-ranked list discards work the run has paid for), `general-task` (re-checks
  what is due before finishing), and `distribution-remap-research`, which existed only in the live
  library and is now back-ported to the seed. Genuine serialization was converted, not deleted: the
  status page's ONE open question stays, now justified as an external constraint rather than
  asserted as a pace.
- **Creation asks the second question.** `create_routine` gained a `goal` array beside `stopping`,
  and the intake contract now asks both: what one run must achieve, AND whether there is a state
  after which this routine is FINISHED. `scaffold` seeds them into one document at their two
  scopes. Many routines honestly have no end and take no goal — but they are now ASKED, because a
  routine nobody asked runs forever by default.

## [0.296.0] — 2026-09-05

### Models use their real context window, discovered from the provider

Operator, 2026-09-05: "I don't think the models that are set up use all their available tokens.
can we make them max out their token context window without the user having to set it up?" They
were right, and by more than it looked: of the 17 catalog models on the live instance, **one set
`context_chars` and none set `max_tokens`**, so every model rode a guess made once on its
endpoint. Measured against the providers' own metadata, the OpenRouter endpoint declared a
50,000-token window against real windows of 256,000–1,310,720 — the engine was using **4.8% of
Kimi K3's context**. The `claude` endpoint erred the other way, claiming a 500,000-token window no
Claude model has.

- **New `endpoints/limits.py`** asks each provider what its models' limits actually are —
  OpenRouter and Nano-GPT from their model listings, Ollama from `/api/show`, other
  OpenAI-compatible gateways opportunistically (vLLM emits `max_model_len`) — and caches the
  answer under `<routines>/.control/model-limits.json`, the derived-state pattern
  `daemon/library_watch.py` sets, explicitly never config. Refreshed at boot and on a 24h TTL from
  the scheduler tick, off the critical path and never fatal.
- **One precedence chain**: per-MODEL config → the provider's figure → the endpoint default → the
  floor. A per-model value is the operator sizing that model down on purpose and still wins; the
  ENDPOINT value moved below discovery because that is all it was ever documented as, "a default a
  catalog model inherits when it leaves the field unset". Putting it there rather than deleting it
  matters: `config.yaml` is not versioned, so a migration that removed hand-set numbers would be
  irreversible for no gain.
- **`resolve()` never fetches.** It is on the per-turn path; a probe there would put a provider
  outage between the model and every single turn. Discovery happens on the daemon tick, resolution
  reads the cache, and a miss is simply the next tier down.
- **The output cap is clamped, not maxed — and that is the point.** Maxing it is the obvious
  misreading and is actively harmful: providers validate `input + requested_output <= window` up
  front (which is exactly what this instance's own unparsed 400s said), and
  `compaction.window_ceiling_chars` subtracts `max_tokens` from the input budget for the same
  reason. Kimi K3's real 943,718-token output limit would leave ~10% of its 1M window for the
  prompt. The discovered cap is `min(provider maximum, 32,000)`.
- **The Settings audit flag is re-aimed.** "max_tokens unset" was the warning; unset is now the
  correct, normal state and says nothing. What warns instead is a model riding the FLOOR because
  no provider lists that id — usually a stale catalog entry — and a hand-set window that disagrees
  with the provider's own figure. Each model row shows its effective window and where it came from
  ("from openrouter", "from the built-in table") instead of an empty box that read as "go and
  guess a number".

*Effect on the live instance, once the daemon has refreshed:* the nine OpenRouter models go from
50,000 usable tokens to their real 256,000–1,310,720, the two Nano-GPT models to theirs, and the
six claude-cli models get an honest 200,000 in place of a 500,000 overclaim that nothing could
have caught.

## [0.295.0] — 2026-09-05

### The Claude subscription's real remaining quota, where you can see it

Operator, 2026-09-05: "we should be able to see the remaining percentages of our claude code
subscription. why don't i?" Two reasons, both fixed.

- **The console was rendering something else.** D33 shipped a LOCAL PROXY — tokens this instance
  burned through claude-cli endpoints in rolling 5h/7d windows — and it cannot answer "% remaining"
  even in principle: Anthropic's windows are not a token count, the tally is blind to your own
  interactive sessions and to claude.ai on the same subscription, it drops cache traffic (the most
  quota-expensive input class), and it is bounded by run retention. A percentage derived from it
  would be a fabricated number wearing a percent sign, so it is **deleted**, not patched —
  `readmodels/claude_usage.py`, its route and its three tests.
- **There is a real source.** `GET api.anthropic.com/api/oauth/usage` reports per-window
  utilization — the same numbers claude.ai's usage panel and the CLI statusline show. New
  `endpoints/claude_quota.py` reads it and `GET /api/settings/endpoints/{name}/quota` serves it,
  shaped exactly like the credits probe beside it: `{"supported": false}` for any other kind, and
  never raising. The endpoint is UNDOCUMENTED, so every failure is soft and every failure message
  names its own fix.
- **And it is where you look.** The endpoint card keeps its line, but the Routines page now carries
  the chip — "5h 61% left (resets in 2h10m) · 7d 32% left" — because "why don't I see it" was
  partly a discoverability answer: it lived as muted 11px text behind Settings → Endpoints →
  scroll. Coloured on the watchfloor rule: SIGNAL while there is room, SUMMONS when a window is
  nearly spent, since an exhausted quota is a thing that waits on a person.
- **It needs a second token, and that is now said out loud.** The usage API requires the
  `user:profile` scope; the headless `claude setup-token` behind `CLAUDE_CODE_OAUTH_TOKEN` carries
  `user:inference` only and 403s. An interactive `claude /login` mints a full-scope one into
  `~/.claude/.credentials.json`, which is the only credential this reads — the inference ladder is
  untouched. Nothing refreshes it headlessly, so the probe reads the file's `expiresAt` and reports
  the expiry with the one-line fix, rather than surfacing an authentication error that does not
  name its cause. **The live instance's token had been expired for four days when this was built,
  silently** — which is the whole argument for reading the stamp.
- **And it survives a recreate.** `~/.claude` is bind-mounted from a dedicated `~/.claude-daemon`
  on the host (not the host's own `~/.claude` — that would put the daemon and a person's Claude
  Code in one session store) and listed in `deploy/state-paths.sh`, so the backup carries it.

*After deploying:* run `docker exec -it -u 1000:1000 rsched claude /login` once. Until then the
chip says so.

## [0.294.0] — 2026-09-05

### Summaries are messages: the Summary page folds into Messages as a fourth item type

Operator order 2026-09-05. A run's finish summary IS a message — it is the one text a routine
writes for a person to read — and it had a page of its own that nothing else linked to, next door
to the page that already indexed everything the instance has to say.

- **A summary is now an item.** `readmodels/summaries.py` shapes what `registry.scan` already
  carries into the same dict `readmodels/items.py` produces, and `GET /api/items` merges the two
  before filtering, so one page, one filter vocabulary, one card. Its id is the RUN id
  (`<slug>:<ts>`) — there is no `S<n>` namespace to own, and `S1` would collide visually with the
  stopping-condition accounting `[s1] met — …` that appears verbatim inside summary prose. Kept
  in its own module because `items.py` is at the house size cap and its four inputs are the
  maintenance record; a summary is a fifth source of a different kind.
- **The status vocabulary is reused, not forked.** `open` = unread, `settled` = dismissed (never
  `in_progress`/`addressed`/`dropped` — nobody works on a summary, they read it). That is what
  makes the page's existing `status=open,in_progress` default land exactly on the unread ones,
  reproducing the old page's Unread-by-default behaviour with no new machinery. The card says
  "unread"/"read", which are the right words on a card and the wrong ones in a shared vocabulary.
- **`type=summary` is the page's default filter**, with the same explicit-`all` sentinel `status`
  already had — without it, clicking the chip off would silently come back on the next reload.
  Landing on Messages now answers "what did everything I run last tell me"; the maintenance
  backlog is one chip away. That reverses D75's "a worklist first, an archive on request",
  deliberately: the backlog is a producer's view and the summaries are the reader's.
- **Both earned behaviours carried across**: the bulk "✓ mark all read" sweep (F303 — without it,
  clearing a backlog is one click per routine) now sits in the Messages toolbar and is shown only
  while summaries are what you are looking at, and the read marker keeps its path, shape and
  meaning (`{slug: newest run seen}`), so **no migration** — a rename would have bought a one-shot
  migration for nothing.
- **Deleted**: `web/api_summary.py`, `static/views/summary.js`, their two test files, the route,
  the nav entry, the breadcrumb and the active-nav key. The rail goes nine destinations to eight.
- The ⚑ priority flag is deliberately not offered on a summary (`priorities.ITEM_ID_RE` rejects a
  run id by design), and summaries are served on the `exists: False` branch too: an instance
  without self-audit has no findings, but its routines still have things to tell you.

## [0.293.0] — 2026-09-05

### Stopping conditions gain a SCOPE, and a routine that reaches its final goal retires itself

The operator's report was that routines "take steps towards that goal that are way smaller than
necessary", especially from run to run, and asked for "a new final state and the ability to
disable themselves once they think they reached it". Investigating it turned up a live defect
underneath: **22 of the 31 routines were being told "EVERY stopping condition is now met — the job
is DONE. Finish NOW" at the top of every single run.**

- **The defect.** `record_accounting` made a `met` condition STICKY — deliberately, so a user is
  not told twice that a goal is done. The 0.286.x backfill wrote 96 conditions across 32 routines
  as PER-RUN bounds ("exactly one bounded increment was produced") — also deliberately, because
  the finish gate demands an accounting for every ACTIVE condition and a project milestone would
  report `unmet` forever. Sticky + per-run is a contradiction: the first run satisfies the bound,
  `active()` then drops it, the gate stops demanding it, the verifier stops checking it, and the
  digest opens with a finish instruction forever. self-audit ran 271 of its 300 turns in that
  state.
- **The fix is a SCOPE on each condition.** `run` (the default) bounds one run: re-asked every
  run, verdict recorded in `last_verdict` and rendered as "last run: met — …", never transitions.
  `goal` is the state after which the ROUTINE is finished: sticky, and the only scope `evaluate()`
  has an opinion about (`goal_satisfied`; `None` when no goal is declared, so nothing announces a
  finish line nobody drew). A one-shot migration converts every live document — every condition
  becomes a `run` bound again and every sticky `met` reopens, with the verdict, the note and the
  run that wrote it all preserved. Nothing is promoted to `goal`: whose words draw a finish line
  is the user's call, made in the panel.
- **Retirement, without anything writing config.** Meeting every goal condition stops the routine
  running, and it does so by DERIVATION: `registry.RoutineInfo.retired` reads the goal document,
  the scheduler builds no fire-table entry and makes up no missed fire, and a group chain skips
  the member. Clearing a goal condition puts it back on the next rescan; `enabled` is untouched.
  So "a run never writes routine.yaml" and "the engine never writes config" both hold, and the
  routine still disables itself. Making it permanent is a click: the finish that completes the
  goal queues ONE `goal-reached` proposal on the Decisions page (deduped — a met goal is sticky,
  so every later run would otherwise file the same row). **Retire it** writes `enabled: false`
  through the ordinary PATCH, the one config writer; **not yet** reopens the goal and the routine
  resumes. Doing nothing leaves it stopped with the proposal standing — an honest third state.
  Two things make the derived half safe to act on unattended: only the web can CREATE a goal
  condition, so a run reports against a finish line and can never draw its own; and every `met`
  claim still passes the v2 verifier against the run's own transcript.
- **FINISHED is not DISABLED.** The dashboard gets its own bucket and chip, the routine page its
  own run chip, and the setup surface a NOTE saying the routine is done — instead of showing a
  cron that will never fire again. Group membership is deliberately untouched: a retired member is
  skipped cleanly, so removing it would only cost its D82 inherited config and its group store.
- **A deliberately-off group member is no longer logged as a chain failure.** Missing, disabled and
  crashed shared one branch, so a member that was simply switched off was recorded
  `outcome: "failed"` — 28 such events on the live instance, all four FAU members among them — and
  under `on_failure: stop` a retirement would have become a daily outage of every later member.
  Absent is still a failure; deliberately off is now `outcome: "skipped"` and the chain continues.
- **The budget paragraph no longer licenses pacing.** It told every run to "**Spend them** on the
  workflow's priorities" and to "work until the job (**or a step of it worth handing over**) is
  actually done" — an instruction to consume the ceiling, and an explicit permission to defer.
  It now names both failures — stopping SHORT because turns have been spent, and spreading a job
  THIN because turns remain — and asks for the shortest sound route: a run done at turn 6 finishes
  at turn 6, unspent budget is never a reason to widen scope or gold-plate, a run with nothing due
  establishes that and finishes, and work is not handed to the next run unless the recipe names
  why it is serialized (an external gate, one submission, a shared resource). One string, in the
  cached system prefix, so it costs nothing per turn.
- `engine/stopping_digest.py` splits the prompt block out of `engine/stopping.py` — the same split
  `harness.py` got from `composer.py`, and what keeps the module under the size rule.

*Deploy note:* the migration runs at the next daemon boot, so it needs the restart sentinel.

## [0.292.0] — 2026-09-05

### The LLM activity dock had no stylesheet, and the class of loss is now closed

- **The dock is styled again.** `#llm-tasks` and every `.lt-*` rule went out with the 0.277.0
  palette migration and were never replaced, so for twenty releases `components/taskmanager.js`
  rendered a bare browser button in the document flow at the foot of every page — confirmed at
  390px and at 1400px. Restored on the watchfloor palette rather than pasted back: the deleted
  rules were written against `--text`/`--faint`/`--amber`/`--live`/`--line`/`--surface`, tokens
  that no longer exist, and a blanket rename would have collapsed distinctions the old palette
  made and lost the ones the new one makes.
- **A running LLM call is the machine working, so the dock is SIGNAL.** The old palette dressed
  the pill, the panel heading and a running process in one amber accent that also carried
  warnings; here the pill goes signal only while something is in flight, the running state mark
  takes the daemon lamp's halo instead of a glow, and terminal states leave the two-urgency
  palette for the plain `ok`/`err` pair a `.chip` uses. Nothing in the dock is ever summons — it
  reports on work, it never asks for a person. Type follows the same rule: mono throughout
  (every word in it was emitted by a counter) except the panel's heading, which is the console
  speaking and takes the display face. The dead `.lt-pill.on` rule was not ported; the component
  has only ever set `active`.
- **The dock now clears the phone's bottom bar.** Its pre-rail corner (`bottom: 12px`) belonged
  to a console whose navigation was a top strip. Below 860px it lifts to the bar's own height
  plus the padding that grows with the safe-area inset, landing on the same line `.workspace`'s
  62px reserve gives the content.
- **The class is closed, not just this instance.** `.side-toc` was lost the same way in the same
  migration and found three releases later; the dock took twenty. Both are mounted outside
  `#view` so they survive navigation, and neither sets a `position` of its own — which is why
  deleting their stylesheet block throws nothing and shows up only in a screenshot.
  `tests/ui/test_global_chrome.py` pins the pair to `position: fixed`, asserts the dock wears the
  design system rather than UA button defaults, and holds it off the bottom nav at 390px. The two
  blocks now sit together under one `base.css` section header that says what they are, and
  CLAUDE.md carries the rule for the next component mounted out there.


## [0.291.0] — 2026-09-05

### What a restart writes: the seed no longer un-deletes, the templates converge, a damaged library is refused

An audit of every write the daemon performs at boot, prompted by the operator asking whether any
of the structures built here get overwritten on restart. The headline answer is **no** — the seed
syncs are add-only and were verified so — but three real hazards came out of it.

- **A deleted library doc no longer comes back (and no longer gets pushed).** `sync_seed_library_docs`
  installs whatever the seed carries and the library lacks, and a doc the operator DELETED in the
  Library tab is also "missing" — so every restart resurrected it, then pushed it, since the library
  repo has a post-commit push hook. Only utils had a never-resurrect guard. The git question behind
  it is now `libgit.path_was_deleted`, shared by both syncs (`utils_lib.was_deleted` delegates to
  it), and it covers workflows, rules, permissions, templates and playbooks. `converse` is
  unaffected: it is refused at delete time by name, not restored after the fact.
- **Settings templates are synced, and the two stale ones are converted.** `<library>/templates/`
  was reachable by nothing: `seed_libraries` writes it only when the repo is CREATED, and the boot
  top-up did not list it. So `maintainer.md` and `operator.md` still carried the pre-0.287.0
  `capabilities.utils: [shell]` shape long after the seed copies were corrected — and **every
  routine created from either was born holding the `shell` permission with the `shell` ACTION
  switched off**, naming a util that no longer exists, which `rsched validate` fails on. Templates
  now top up like any other doc kind, and the shell migration gained the templates pass it never
  had (its fifth surface, after routine files, group config, the permission doc and the util).
- **A library that lost its `.git` is refused, not re-initialised.** `utils_lib.ensure_library`
  treated "directory exists, no repo" as a fresh tree: it would have discarded the live library's
  844 commits, overwritten the real `.gitignore` (which excludes `.active/`, `INDEX.md`, `.venv/`)
  with the two-line seed one, and committed and pushed that runtime state. It now logs what is
  wrong and returns; boot is unaffected, since the lifespan already tolerates a library failure.
- **`deploy/install.sh` seeds the library by calling `bootstrap.seed_libraries`** instead of its own
  shell copy of it. That copy had drifted: it still created the retired `fragments/` directory and
  copied neither rules, permissions, templates nor playbooks, so a host install began with four of
  the library's five doc kinds absent and only the add-only boot sync ever filled them in.

*Deploy note:* the template conversion runs at the next daemon boot, so it needs the restart
sentinel (or a `docker compose restart`) — a `compose up -d` will not reload the code.

## [0.290.0] — 2026-09-05

### The phone's bottom nav: the bar was never dropping icons, the document was scrolling sideways

- **F439 reopened and actually fixed.** The bottom bar was reported as showing about five of its
  nine destinations; 0.289.1 shipped a guard that said it carried all nine, and both were true —
  of different pages. `.topbar` is `position: fixed; inset: auto 0 0 0`, so it is laid out against
  the *layout* viewport, and mobile Chrome grows the layout viewport to the document's scroll width
  whenever anything overflows horizontally. The bar then stretches across the wider box and its
  nine equal children carry the tail off the physical screen. Visible icons are
  `floor(9 × screen ÷ document)` — five on the operator's screenshot, nine thirty minutes earlier
  on the same phone.
- **The overflow source was the transcript's text surfaces.** `.md`, `.ev`, `.finish-banner`, the
  `say` line, a captured `note`, the action brief and a decision's question text had no
  `overflow-wrap`, unlike the chat surfaces (`.msg-body`) which were hardened long ago. One token
  with no break opportunity — a commit sha, a run id, a base64 blob — widens the document. (A
  *path* does not: Chrome takes a break after `/`, which is why this was never reproduced from a
  filename.) All seven surfaces now carry `overflow-wrap: anywhere`; the action brief also takes
  `min-width: 0`, since it sits in a flex row where the two are needed together.
- **The guard that let it ship is gone.** `tests/ui/test_reported_ui.py` counted nav items against
  `window.innerWidth` — on a phone that IS the expanded layout viewport, so it agreed with the bug
  by construction and reported 9/9 in every broken state. Replaced by `tests/ui/test_mobile_nav.py`,
  which clips against the width the test itself emulated and holds the invariant that actually
  keeps the bar intact: **no route may make the document scroll sideways.** Asserted per route at
  390px over a seeded transcript, so the next overflowing widget is named by the test rather than by
  the operator. Before the fix it caught two routes — the run view and, separately, `#/summary`.

## [0.289.1] — 2026-09-05

### Copy buttons on touch, a clearer script-install-timeout error, and UI regression guards

- **Chat copy buttons are now reachable on touch devices (F433).** The per-message and
  per-code-fence copy glyphs were revealed on hover only, so on a phone — which has no hover —
  they were invisible, reading as "missing". Where the pointer cannot hover
  (`@media (hover: none)`) they now show at rest, at the same half strength a hover gives; a
  mouse keeps the quiet hover-only affordance.
- **A dependency-install timeout now explains itself (R1296).** When a `script` action's
  per-routine venv exceeds the fixed install cap, the error used to be a bare `timed out after
  300 seconds`; it now says the cap is separate from the action's `timeout_s` (which bounds the
  script's runtime, not the build) and cannot be raised from a recipe, and points at the fix
  (a lighter dependency, or offloading heavy compute to a util or a remote machine).
- **Regression guards for three operator-reported UI issues** (`tests/ui/test_reported_ui.py`):
  the mobile bottom nav carries all nine destinations (F439), the watch ribbon paints a bar for a
  recent run (F432), and the routines view re-fetches its cards on nav-back (F434). *Corrected in
  0.290.0: F439 was NOT resolved. Its guard measured nav items against `window.innerWidth`, which
  on a phone is the very layout viewport the bug expands, so it could only ever agree with the
  bug. The other two guards stand.*

## [0.289.0] — 2026-09-04

### Resizable + hideable sidebars — the navigation rail first (operator request)

The console has four sidebars — the main navigation rail, the page-nav, and the run view's left
and right rails — and the operator asked to drag any of their vertical borders to resize, and to
hide each one. This ships the shared mechanism and the FIRST surface (the main navigation rail);
the other three follow on the same primitive.

`static/resizable.js` (`wireSidebar`) turns a thin grip element on a sidebar's moving border into
BOTH controls at once: a **drag** resizes, a **click** (no drag) hides or shows, and the grip is
keyboard-focusable (Left/Right arrows resize, Enter/Space toggle). The load-bearing rule that keeps
the responsive layouts intact: it writes a sidebar's width ONLY as a CSS custom property and its
hidden state ONLY as a class — never inline geometry — so a `@media` collapse always stays
authoritative. A dragged rail width lives in the new `--rail-w-set`, which only the wide layout
reads; the ≤1180 narrow icon rail and the ≤860 mobile bottom bar drive `--rail-w` themselves and
are untouched (the grip is `display:none` there). Width and hidden state persist to localStorage
and restore before first paint.

Wiring: `app.js` `initRailResize()` mounts the grip and calls `restore()`; `base.css` adds
`--rail-w-set`, the grip, and the `.sb-hidden-rail` rules scoped to `@media (min-width: 1181px)`.
Test: `tests/ui/test_sidebar_resize.py` drives real chromium — drag widens the rail and persists
across a reload; a click hides it (width → 0, grip stays as the re-show target) and shows it again.

**Next increments (same primitive):** the run view's left and right rails, then the settings /
routine page-nav.



## [0.288.3] — 2026-09-04

### Theme: declare dual-scheme support so a "light" choice survives a force-darkening browser

The operator reported that picking **light** in the theme toggle left everything dark. The
in-browser mechanism is correct and now proven: every palette token is `light-dark(light, dark)`
and the theme is `data-theme="light"` → `color-scheme: light` on the root, which makes both
`light-dark()` and the browser's own page canvas go light. A new `tests/ui/test_theme.py` drives
the REAL chromium and confirms picking light computes `color-scheme: light` and a light rail
surface (and dark stays dark) — this whole surface previously had **no test at all**, which is how
"light does nothing" reached a user.

Since the code path is correct, a report of "light stays dark" is a browser **force-darkening** the
page over the top — Android Chrome's *Auto Dark Theme*, or a desktop dark-mode extension
(Dark Reader) — which overrides the site's `color-scheme`. The documented opt-out those features
honor is a DOCUMENT-level `<meta name="color-scheme">`, which the console was missing (it set
`color-scheme` only in `base.css`). Added `<meta name="color-scheme" content="dark light">` to
`index.html` (`dark` first keeps the shipped default flash-free) and a test pinning its presence.
If a page-served fix is not enough for a given browser, the theme is honoured once that browser's
force-dark / dark-mode extension is disabled for this origin.

## [0.288.2] — 2026-09-04

### Messages: clear the "addressed, never delivered" orphans, and stop mislabelling closures (F435/F436)

The Messages page banners reports that name a target routine but have no `inbox/msg-rep-<id>.json`
for it — so no run can ever drain them (`readmodels/orphans.find_undelivered`). The operator asked
why 17 such rows exist and whether they sit forever. They were two things: **12** stale 2026-08-29
web-UI migration announcements (superseded), and **5** operator *closure* replies (`closes:true`,
answering R1146–R1150) that a run's own `report` action would never orphan — the sole code producer
(`file_report`, via the report action) writes the ledger row and the target's inbox message in one
call, and refuses an unknown target outright. Both cohorts were an operator batch appended straight
to the stream, and nothing drained or aged them out.

Three changes:

- **Closures are no longer flagged.** `find_undelivered` skips a `closes:true` row: a closure is
  born settled — the terminal acknowledgment of an exchange, asking nothing back — so an undelivered
  one is not lost work. The 5 "Closed:" rows drop off the banner.
- **A discard affordance.** Each undelivered-orphan row gains a **discard** button
  (`POST /api/items/orphans/{id}/discard` → `reports.discard_undelivered_report`) that appends a
  `retracted` event, so the row reads `dropped` and leaves both the banner and the backlog. It is
  the mirror of `retract_report`: retract withdraws a delivery still WAITING in the inbox; discard
  clears one whose delivery is genuinely ABSENT (and refuses a row whose message still waits — that
  is retract's). Clears the 12 stale announcements in one click each.
- **A producer-invariant guard.** A regression test pins the invariant the whole orphans design
  rests on: a targeted `file_report` always writes its inbox delivery in the same call, and an
  unknown target files nothing at all — so an addressed report can never be orphaned at the source.
  (There is no live buggy producer to fix; the 17 rows came from a one-off operator batch.)

Wiring: `readmodels/orphans.py` (closure skip), `reports.py` (`discard_undelivered_report`),
`web/api_items.py` (the discard endpoint), `static/views/messages.js` (the banner button + a
re-render). Tests: `test_orphans.py` (closure excluded), `test_reports.py` (discard clears/refuses;
the producer invariant), `tests/ui/test_messages_page.py` (the discard flow).

## [0.288.1] — 2026-09-04

### Goal panel: a long condition's meta no longer wraps one letter per line on a narrow screen (F421 v3)

The run view's goal/stopping panel renders each condition as a flex row —
`[mark, text, [s<n>]+note meta, requires-select, "any stage" input, ✕]` — and on the ROUTINE run
view the per-stage input is present (`showStage: true`). The row did not `flex-wrap`, so on a
narrow (mobile) viewport the fixed edit controls plus the `min-width: 22ch` condition text filled
the line and the only shrinkable child, `.goal-meta` (`min-width: 0`), collapsed to ~0 width;
`overflow-wrap: anywhere` then broke its `[s1] · <note>` text into a tall single-character column
(operator screenshot, `miz-grant-steward`). This is the third failure mode of the same row: F421
first stopped the note running off the sidebar, then stopped it starving the condition text — and
the `min-width: 0` added for that second fix is what let it over-collapse here.

`.goal-row` now `flex-wrap: wrap`s so the meta and edit controls drop to their own line under
width pressure instead of crushing, and `.goal-meta` gets a `min-width: 12ch` floor so it can
never re-collapse to a character column even when it shares a wrapped line (`flex: 0 1 34ch` still
lets it yield width to the condition text). Regression test on the previously-uncovered
`showStage: true` run-view path at a 390px viewport asserts the meta stays wider than it is tall
(`tests/ui/test_stopping.py`).

## [0.288.0] — 2026-09-04

### The agent can reply to an earlier message (D117)

A conversation is a stream of messages, and until now the agent's reply was always implicitly an
answer to the newest one. The user could already REFER to any earlier message — the ↩ on a bubble
primes the composer and the sent message leads with a quoted `.reply-ref` chip. The agent had no
equivalent: after doing async or out-of-order work it could not point its reply at the specific
message it addressed.

`finish` now takes an optional `reply_to` field (conversations only): a short reference to the
earlier message this reply answers — quote it or name it. It rides the finish EVENT payload and
the conversation chat renders it as the same ↩ `.reply-ref` chip above the reply that a user's
reply-to renders, so at a glance you can see WHICH message a reply belongs to. Optional and ignored
outside a conversation (a routine run has no chat), so nothing else changes.

Wiring: `reply_to` declared on the flat action schema (`actionschema.py`) and added to finish's
allowed fields so `normalize_action` keeps it (`actions.py`); threaded from the finish action
through `finishgate.check_finish` → `EngineLoop._finish_run` onto the `finish` transcript event
(`loop.py`); rendered in `chat.js` `replyNode`, mirroring `userNode`'s existing `.reply-ref`. Tests:
`test_actions.py` (accepted on finish, stripped on other kinds), `test_loop.py` (threads onto the
finish event; absent when unset), `tests/ui/test_branches.py` (the chip renders on the reply).
Scoped increment (operator chose "design + build scoped"): the reference is free text like the
user's, not yet a click-to-jump link — a natural later enhancement, and a precursor to out-of-order
replies once actions can run in the background (D118).

## [0.287.1] — 2026-09-03

### The shell migration reaches GROUP config too

0.287.0 deployed, and `rsched validate` against the running instance immediately reported four
routines — `ards`, `fau-grant-prep`, `nanogeofeld`, `suedlink-wlf` — as `permission:shell held,
but its requires: are not switched on` plus `util:shell held as a reserved util — no util by that
name is in the library`. None of their `routine.yaml` files mentions shell at all.

The grant came from their GROUP. A group's config block is a LIVE layer its members inherit at
load time (D82), unlike a settings template's one-shot copy, and it lives in
`.control/groups.json` — not in any routine dir. The migration walked routine, conversation and
background dirs and never looked there, so the FAU group kept re-supplying `capabilities.utils:
[shell]` to four members whose permission then had nothing behind it: fail-closed, exactly as the
two-layer model promises, and exactly the silent capability loss the migration exists to prevent.

`_migrate_groups` converts every group config block on the same terms, patching `groups.json` as
raw JSON rather than round-tripping it through `groups.load`/`_save` (which normalizes, and would
rewrite fields this migration has no business touching). Two tests cover it: the store-level
conversion (asserting the untouched fields stay untouched) and the effective one — a member's
config merged through `apply_group_config` yields a policy that allows the kind.

The lesson is the migration's own docstring now: a dry run over copies of the routine files was
not a verification, because a routine's effective config is not only its own file.

## [0.287.0] — 2026-09-03

### `shell` becomes an action kind, not a reserved util

The escape hatch was a global util at `<library>/utils/shell/`, unlocked by the `shell`
permission's `requires: utils: [shell]`. It is now the `shell` ACTION KIND, gated by the `shell`
capability. Two findings drove it, and only one of them was about the sandbox:

- **Gating was fail-open.** `capabilities.utils` is an EXCEPTION list, not an allowlist — 6 of
  114 utils are gated at all, and a util is gated only because some permission doc happens to
  name it. A gated KIND goes through `validate_action` AND `kindsurface.effective_kinds`, so a
  routine without the capability is not sent the kind: the call is ungeneratable rather than
  generated and then rejected.
- **The sandbox is a wash, and had to stay one.** The retired util declared `fs: roots` +
  `net: outbound` — the widest terms available — so its intersection term was already a no-op
  and its effective bound was exactly the run's granted roots; it declared no `secrets:`, so
  declared-only injection handed it nothing. `rsched/shellrun.py` reproduces all three
  (`fs_roots=True`, `net=True`, `scoped_env(set())`) through the same `sandbox.wrap`, and
  `tests/test_shell_action.py` pins the jail's inputs directly so a later edit cannot quietly
  turn a gating improvement into a sandbox regression.

The surface is the util's: `command` (one string through `bash -c`), plus `timeout_s` (default
120, the util's own) and `path` (the working directory) — both REUSED shared schema fields
rather than shell-only ones. The 64 KB per-stream head+tail cap survives in `shellrun`, and the
observation truncates and spills to `.util_outputs/` exactly as a `util` or `script` call does
(`observations._run_body` is now the one copy of that body for all three callable kinds).

- 27 action kinds (was 26; CLAUDE.md's list said 25 — it had been missing `list_models`, now
  fixed alongside).
- `library-seed/permissions/shell.md` requires `actions: [shell]` and its BODY describes the
  action — that body is inlined into every holder's prompt, so a frontmatter-only change would
  have taught 14 routines a call the engine now rejects.
- `util-seed/utils/shell/` is deleted. It declared no private `fs:` store, so no other util's
  jail changes; nothing in the library declares it on a `calls:` line.
- The harness contract's "how code runs" sentence names the hatch only for a run that holds it.
- MIGRATION(expires=2026-10-03): `migrate_shell_action` moves `shell` from `capabilities.utils`
  to `capabilities.actions` in every routine/conversation/background config (14 routines and 12
  conversations on the live instance), replaces the live library's permission doc with the
  seed's, and deletes the installed util. Without it a holder keeps an inert `utils:` entry and
  loses the hatch its permission says it has.

## [0.286.1] — 2026-09-03

### The phase-file check looks at the right signal (msg-13 follow-up)

0.286.0's new setup-surface row detected "this routine tracks a phase" by a `## Phases` heading in
main.md. The routines that get the phase key WRONG are exactly the ones with no such heading — they
describe the phase in prose: self-audit walks a ten-stage state machine through `state/phase.json`,
routine-improver keeps a step cursor in it. Both were invisible to the check that exists for them.
It now looks for the recipe naming `state/phase.json` at all (main.md plus stages/), and a routine
that never mentions the file is still silent.

Against the live instance that flags three routines whose RECIPES instruct the wrong key — self-audit
`state`, routine-improver `step`, funscript-trainer `lifecycle` — one concept under three names, none
of them the `phase` the composer reads and stage-scoped stopping conditions match on. Fixed at the
cause (the recipes) with the live files renamed to match.

`src/rsched/readmodels/surface.py`.

## [0.286.0] — 2026-09-03

### The workflow layer stops teaching things that are not true (msg-13)

Operator: *"the workflow 'general-task' says to 'Do one small step and return its result.' … it also
says 'There is NO shell' which is only true for routines that dont get the permission. as the
workflows are the foundation for every new routine, this is a big problem."* An audit of all nine
library patterns against the live engine, 32 routines and ~9,700 transcript actions found both, and
ten more.

**The claim that was false.** `shell` is a real permission unlocking a reserved util, held by **14 of
32 routines** — so for those runs the CAPABILITIES section advertised "run arbitrary shell commands"
in the same prompt where the recipe denied a shell existed. It was not only in the patterns: the
HARNESS CONTRACT itself opened with "You have NO shell" for every run
(`engine/harness.py`), and `utils_lib.py`, seven docs and the `global-utils` permission repeated it.
All of them now say how code runs rather than what does not exist. (Shell becoming a first-class
action kind is queued separately; this release only stops the lying.)

**The claim that was a pace.** `pyworkflow.render_markdown` promotes each step function's docstring
FIRST LINE into the prose step list, so general-task's central work step led with a pure pacing
sentence carrying no task content — and decomposed into recipes like funscript-trainer's "never try
to finish it all in one fire — do a single verified increment, record it, and stop." Median unused
turn budget across the 31 routines with run records: **58%**. The step now names the work, and
`pick_work` states the rule the engine already holds — the turn budget is a runaway backstop, not a
ration.

**One definition of done.** Patterns carried a `COMPLETION` literal and a `DONE_WHEN` param, both
frozen into main.md where the user cannot edit them, while `state/stopping.json` — the surface the
composer inlines, the finish gate enforces and `verifier.py` checks — went unmentioned by all nine.
Both are gone, with the `COMPLETION` lint requirement, the renderer's `## Completion criteria`
section and the `generate` template's line.

**The step list is what `main()` sequences.** The renderer promoted EVERY module-level function,
so materialized routines were told to act out `file_exists — Helper to check if a state file
exists`. It now walks the calls `main()` makes, in source-position order (`ast.walk` is
breadth-first and put `bootstrap` after `record`).

**A second copy of the intake contract, unread and stale.** `clarify-instruction` was undeletable on
the belief that "routine creation runs it" — nothing ever did; the live contract is the
`create_routine` kind surface. Its copy had gone stale unnoticed, still describing conduct as
per-routine "traits" long after rules became one shared library doc, and still headed "RECIPE vs
PROCEDURE" after that doctrine was reversed. Deleted, with its four unique judgements lifted into the
draft observation as `design_checks` (SHAPE / MECHANISM / OWNERSHIP / SCOPE). The undeletable guard
moved to `converse`, which every conversation IS materialized from by slug and which had no guard at
all.

**Harness patterns are no longer offered as buildable.** `_catalog()` listed every library pattern
unfiltered, so routine creation offered `converse` — whose own `when_to_use` says "Not for scheduled
routines: … the reply cycle assumes a user who reads the answer and writes back". It now excludes the
`meta` tag, which `converse` already carried for exactly this purpose.

**Per-pattern fixes.** A bootstrap run now falls through into real work in all four patterns that
returned early (application-coaching-steward already did — a first fire that delivers only setup
costs a whole cadence). `general-task`'s `wait(children)` described a contract the engine does not
have (real fields are `n`/`all`/`timeout_s`) and gated delegation on a magic `PARALLEL_THRESHOLD = 8`;
both replaced. `improvement-proposer` imported `write_util`/`spawn`/`wait` while declaring itself
record-only — and since `kindsurface` narrows the schema to `tools:`, that was prose for channels the
run could not emit; `workflows/lint.py` now fails on the disagreement. `feed-monitor`'s hand-coded
dedup algorithm became a step the routine owns as persistent tooling, and its per-item `llm` call
became one batched judgement. `config-audit`'s per-item blocking apply-gate is gone: both holders are
scheduled routines that correctly never blocked (0 blocking asks in 8 runs each), so the branch was
dead and the audit now files deferred decisions. Every `orient()` stopped re-reading `LEDGER.md`,
which the state digest already inlines (`composer.py:127`) — 15% of runs were spending a turn on it.

**Two new setup-surface truths.** `readmodels/surface.py` now notes a `state/phase.json` that records
the phase under some other key, or that no completed run ever wrote while the recipe declares
phases: the composer reads `.get("phase")` and that value scopes a stopping condition to a stage, so
funscript-trainer's `lifecycle`, self-audit's `state` and routine-improver's `{}` matched nothing,
silently. Adding it exposed a second thing: the engine's BOOT note was carrying every unmet row
including `note`, though its own closing sentence explains only FAIL and WARN. A note is addressed
to the operator — a cron the group suppresses, a phase file keyed wrong — and a run can neither act
on it nor be saved a turn by it, so the boot note now carries `blocks`/`interrupts` only
(`surface.BOOT_SEVERITIES`). The routine page and `rsched validate` still show all three.

`src/rsched/workflows/{pyworkflow,lint,generate}.py`, `src/rsched/engine/{create_routine,harness,
actionschema,autocommit}.py`, `src/rsched/utils_lib.py`, `src/rsched/readmodels/surface.py`,
`src/rsched/web/api_workflows.py`, `src/rsched/engine/boot.py`, `static/views/library.js`, all nine library patterns (one deleted),
`library-seed/permissions/global-utils.md`, and the doc sweep across CLAUDE.md + seven `docs/` pages.

## [0.285.0] — 2026-09-03

### The daemon lamp shows when a restart is pending (msg-12)

Operator: *"we need a visual indicator if the daemon is draining atm."* With 0.283.0 the daemon
no longer blocks-drains — a requested restart waits for a quiet gap and fires at the next idle
moment — so the thing worth seeing is that a restart is *queued*. `/api/status` already reports
`restart_requested`; the rail's daemon lamp now turns **amber and pulses** while one is pending
(green when connected and clear, red when the link is down), with a title that says the restart
applies at the next idle moment. `static/app.js` (the shared boot+poll `refreshStatus` toggles the
`restart-pending` class + title), `static/base.css` (the amber `.lamp.restart-pending` reusing the
`--warn` token and the existing `pulse` keyframe); a `tests/ui/test_smoke.py` case drops the
restart sentinel and asserts the lamp carries the class and the explaining title.

## [0.284.0] — 2026-09-03

### A long action can run: the `timeout_s` ceiling is raised from 600s to 1800s

Operator: *"the default timeout must be overridable as an action parameter."* A `util`/`script`
action's `timeout_s` was capped at 600s by the action schema, but the scheduler's own full test
suite now takes ~900s — so a run could not gate itself in a single action, and self-audit had to
split the suite into chunks by hand every run. The schema ceiling is now **1800s** (the 300s
default is unchanged; blank still means 300). The executor already passes `timeout_s` through
unclamped, so raising the schema `maximum` is the whole fix. `src/rsched/engine/actionschema.py`;
a `tests/test_actions.py` case pins that an 1800s override validates and 1801 is rejected.
(The run's wall-clock budget remains the real bound — a per-action timeout only decides when one
subprocess is killed, not how long the run may live.)

## [0.283.0] — 2026-09-03

### Conversation lifecycle: a fork no longer wedges its parent, and a pending restart never blocks a start

**Forking a conversation collided the branch with its parent and wedged both.** `fork_conversation`
(`branches.py`) copies the parent's `routine.yaml`, then sets the branch's name, description and
`parent` provenance — but never reset the `slug`, so the branch kept the **parent's** slug. Because
`load_routine` reads `raw["slug"]` before falling back to the directory name, the branch loaded
*as the parent*, and the runner's slug-keyed `active` map cannot hold two runs under one key: the
parent conversation became unreachable (its open question could not be answered — "I give
permission, nothing happens") and a message to the branch was refused as an overrun of the parent's
slug ("add a new message, nothing happens"). The fork now sets `raw["slug"] = slug` (its own
directory name) so it loads as itself. Reproduced by the live fork `c-20260903-062355-b1`, which had
carried `slug: c-20260903-062355`. `src/rsched/branches.py`; a `tests/test_branches.py` regression
pins that a fork loads under its own slug with no slug/dir mismatch. (Existing wedged forks predating
this fix must be discarded to free the parent's slug.)

**A pending self-update restart no longer blocks starting a run or a conversation.** The daemon
used to enter a *drain* the moment a restart was requested — firing nothing new until every active
run finished — so a restart requested during a busy stretch left the operator unable to start a
conversation (`could not start the conversation (daemon draining?)`) or resume a run for as long as
anything was running. Per the operator's rule, a pending restart now keeps scheduling normally and
simply **waits for a quiet gap**: it restarts only once nothing has been active for
`RESTART_IDLE_S` (10s). The two safety invariants hold — it never restarts while a run is parked in
`waiting_user`/`paused` (never out from under a live dialogue) and never interrupts an active run.
`src/rsched/daemon/restart.py` (the pure `restart_action` state machine: `drain`→`wait`, gated on an
idle-window flag instead of the draining bit), `src/rsched/daemon/scheduler.py` (a monotonic
`_idle_since` clock drives the flag); `tests/test_restart.py` rewritten for the new semantics plus a
test that the idle window is waited out before the restart fires.

## [0.282.0] — 2026-09-03

### Two bugs from the operator's screenshots: a dead group-`run` proposal, and a leaked NetworkError

**A `manage_group verb=run` from a run with no user queued a proposal that could never be built.**
The engine queued every mutating verb outside a root conversation (F328), but `run` is not a
mutation — it arms an *ephemeral* group fire, writes no config, and the materializer
(`web/api_pending._materialize_group`) only knows create/update/delete/set-default. So a routine's
`run` (miz-grant-steward, trying to accelerate a sibling — R1200) became a card on the Decisions
page under "queued creations" with a **create it** button that errored `cannot materialize group
verb 'run'` when clicked: a dead card the operator could only discard. `run` now **fails loudly**
at the engine instead of queuing — a fire is time-sensitive and an approval hours later would fire
a stale chain, so a no-user run is told to ask the user or report which group needs firing (R1200's
own ask: fire, or fail with the reason). Config verbs still queue. The materialize fallback, which
the one legacy `run` card still hits, now says to discard it and fire the group live rather than
emitting internal jargon. `engine/manage_group.py`, `web/api_pending.py`; a test pins that a
no-user `run` rejects and queues nothing.

**The "Recommended setup" panel leaked a raw browser "NetworkError when attempting to fetch
resource".** The recommendation is one system-model read of the whole recipe — a slow call the
button holds the browser on, with no client-side timeout. When the connection is dropped (a proxy
idle-limit, a slow model, or a deploy in progress) the fetch rejects with a bare browser string the
panel printed verbatim. It now distinguishes a no-response failure (no `e.status`) from an HTTP
error and reads honestly — what happened, to retry, and that the Permissions and General rules
panels below work without it. `static/components/recommend.js`; a `tests/ui` test aborts the request
and asserts the honest copy renders and the raw "NetworkError" never does. (The deeper fix — making
the recommendation non-blocking so a slow model can't drop the browser at all — is left as an open
question for the operator; it changes the interaction model.)

## [0.281.0] — 2026-09-03

### The routine description is a multi-line field (msg-2)

Operator: *"the editable description field for routines must be multi line."* The routine
config page built the description editor as a single-line `<input type="text">` with a
"one-line description" placeholder, so a summary longer than one line could be typed but never
seen while editing — the field scrolled it out of view. It is now a three-row `<textarea>`
(vertically resizable) that holds and shows multiple lines, and the per-control copy no longer
promises "one-line". Read-back on save is unchanged (`.value.trim()` reads a textarea the same
as an input), so the PATCH path and the empty-guard are untouched. `static/views/routine-config.js`;
a `tests/ui/test_routine_page.py` case pins that the control is a textarea (the single-line input
is gone) and genuinely holds a newline.

## [0.280.0] — 2026-09-02

### Fixed — three console regressions from the 0.277.0 redesign, all found by measuring

- **`position: fixed` was broken on every page.** The page-enter animation ended on
  `transform: none` with `animation-fill-mode: both`, and a *filled* `transform: none` computes to
  the identity matrix rather than the keyword — so every view container stayed a containing block
  for its fixed descendants, permanently. The conversation and run rails measured from the reading
  column instead of the viewport and sat on top of the text they belong beside: the left rail
  landed at x=634 instead of 230 on a 2000px viewport. The enter animation is opacity-only now,
  and a comment says why it must stay that way. Measured after: rails at 230–474 and 1738–1982,
  chat 510–1702, both clear.
- **The "On this page" navigation had been invisible since 0.277.0.** `components/toc.js` was never
  removed and `app.js` still mounted it — the palette migration deleted its entire stylesheet block
  (it used the retired `--line`/`--muted`/`--amber` tokens), so Settings and every routine page
  built a nav with no styles. Restored on the current palette. Its breakpoint moves 1560 → 1900:
  the navigation rail takes 212px off the left, so the margin it parks in opens up that much later,
  and the two tests asserting it at 1600/1700 were asserting something the layout cannot deliver.
- **A long note still collapsed the goal panel** — the other half of F421, which fixed the note
  *overflowing* by letting it wrap. Wrapping says how a note breaks, not how much of the row it
  may claim, so a paragraph of stopping-condition accounting still won the width negotiation and
  squeezed the condition itself — `flex: 1; min-width: 0` — to one word per line, destroying the
  panel that was reporting it. The condition keeps a 22ch floor and the note a shrinkable 34ch
  basis; it still wraps, as F421 requires. A test now pins the starving half too.

### Changed

- `miz-grant-steward`'s recipe builds the single-editor lock, the ten-minute inactivity logout and
  the visible countdown its original brief asked for. The routine had reasoned them away as
  unnecessary over an append-only store — sound about data safety, and beside the point: the ask
  was about two people not talking over each other. It needs nothing the host does not already
  offer, so it was work misfiled as a question.

## [0.279.0] — 2026-09-02

### Changed — nobody deploys for anybody: `steward-hub-maintainer` retired

The hub's maintainer had already lost every reason to run on a clock (0.277.0). What was left was
file placement for ten siblings that each already run on their own schedule — so every document
waited for a second routine's run, every deploy needed a staged copy and a report, and a page could
stand broken for hours with its fix already sitting on the first routine's disk. It existed for one
reason: the host's FTP credential is a single account rooted at the document root, so handing it to
a publisher handed over every other project's directory, all of `_store/`, and the gate itself.

That is now closed in the `ftp` util instead of by a routine, using what already existed rather
than a new mechanism.

- **`ftp` util — `dir` is a confinement, not a convenience.** It used to be a directory the util
  `cd`'d into after login, which an absolute path or a `..` walked straight out of. Now every op
  resolves its remote path under it and refuses anything that leaves. Sources without `dir` are
  unchanged. New `deny_ext` refuses WRITING given extensions, for a source pointing at a web root.
  Both pinned by the offline selftest. (Its missing PEP 723 block, a pre-existing nonconformance,
  is fixed in the same pass.)
- **`{routine}` in `dir` — the directory is named after whoever is calling.** The engine now
  exports `RSCHED_ROUTINE` to util subprocesses (declared-only, like every other injected name),
  so `"dir": "/{routine}"` gives one credential to many callers with each confined to its own
  directory and NOTHING configured per caller — including a caller that does not exist yet. A
  routine cannot forge its environment, and a call with no caller name is refused rather than
  given the account root. Every other answer to "which directory is yours" needed maintaining: an
  account per project, a central map, or a per-routine variable that is one more thing to set and
  forget.
- **Nine ROUTINES renamed to match their directory on the host**, which is what makes the
  derivation possible — `ards-consulting-steward` → `ards`, `personal-weight-loss-coach` →
  `weightloss`, and seven more. Renaming the directories instead was the obvious reading and the
  wrong one: it would have changed every page URL, made every routine republish its card, and for
  the weight-loss coach — whose directory is a published PWA — broken a service-worker scope, a
  webmanifest, an installed app on a phone, a GPSLogger device configuration and an OAuth redirect
  URI registered with Google. Nothing outside this system knows a routine slug, so the slugs moved
  and the host stood still. The old slugs are long and distinctive, which also makes the textual
  sweep safe; the reverse would not have been, since `ards` is also a medical term.
- **A publisher proves its own confinement.** Nothing is auto-set at creation and no flag declares
  the requirement: the confining directory is the project's name on the far side, which need not
  match the routine's slug (`ards-consulting-steward` publishes to `/ards`), so guessing it would
  write a wrong directory silently. Instead the rule makes the publisher ask for something one
  level above its own and require the refusal — place nothing and report if it answers. That is
  the same discipline as the link check beside it: an unconfined publisher's only symptom is that
  nothing stops it. The confinement also binds the TOOL, not the credential: a routine's own
  `scripts/` may declare `FTP_SOURCES` and speak FTP directly, so this stops a misread path or an
  instruction arriving in ingested content, not a routine that sets out to go around it.
- **`status-page` rule** gains "Your directory on the host is yours to fill": place your own page
  assets and documents, in your own run, and prove them by fetching. One copy, reaching all
  fourteen holders on their existing schedules.
- **`steward-hub-maintainer` disabled**, trigger removed, directory kept for its ledger and memory.
  `miz-grant-steward`'s recipe no longer stages assets for it or asks it to register anything —
  registration has not existed since `p.php` — and three routine memories that still described the
  old ownership are corrected.

Deploying the kit itself stays operator work: the unconfined source, from the library repo, when
the kit changes, `links.php` before `store.php`. A hub cannot safely update through itself, and a
release is not a cadence.

## [0.278.0] — 2026-09-02

### Fixed — a page whose every document link 404'd, and the proof that said otherwise

A project corrected its document links four turns after publishing them, re-sent its state
document, and never re-sent the collection those links live in. The corrected values sat on disk
for five hours while the page served the broken ones, and the run reported the page verified in
full — because the `status-page` rule's third proof was scoped to the `documents` key of the state
document, while a `module:"own"` page renders its links out of the item collection beside it. The
proof was also written on a false premise: that a run holds no credential the gate accepts, so it
must reason about its own payload rather than fetch. A proof a run reasons about is a proof that
never fails.

- **`status-page` rule.** Proof 1 reads back *every* stored copy the page renders from and diffs
  it against what was built — a store corrected locally but never re-sent reads back as the
  version before the correction. Proof 3 is stated over the links the page RENDERS, wherever the
  project keeps them, and performed by FETCHING each one and requiring the file back; a status
  code alone proves nothing, least of all a sign-in page served as a 200. A failed check whose
  repair is the run's own is repaired in that run, not deferred. PENDING became a reading of the
  host to be re-taken each run rather than a decision made once. `effect.with` follows.
- **The rule now forbids transcribing itself.** Nine routines had copied the document-linking
  paragraph into their own `.memory/`, where nothing updates it — one of them nineteen lines above
  that routine's own correct version. All nine corrected; three were publishing dead links because
  of it (`ards-consulting-steward`, `fau-grant-application-prep`, `aisafety-grant-steward`).
- **`web/steward/links.php` (new).** The extension allowlist, the never-served trees and the
  resolution predicate, in one file required from `store.php` — what every entry point reaches.
  The tables used to live in `gate-file.php`, where only the serve path could read them, which is
  why the write path stored links nothing had ever evaluated. `api.php` now refuses a `put-items`
  or `put-state` carrying a gate link this host cannot serve, naming the link and the reason; an
  empty url stays legal, because that is how a document says it is not on the host yet. The check
  reads values, not field names — checking by name would have missed `pdf_url`.
  **`store.php` and `links.php` deploy together**: the former without the latter is fatal on every
  request.
- **`_shared/modules/status.js`.** Documents are no longer filtered on the presence of a url. A
  document with no url is listed unlinked and reads as pending instead of vanishing — five
  compiled PDFs read to their author as missing because the page that listed them showed nothing.
- **`gate-file.php`.** Refusals leave with `X-Content-Type-Options` and `Referrer-Policy`, which
  only the success path had been sending.

## [0.277.0] — 2026-09-02

### A queued proposal no longer renders as a completed action (R1200, R1183, F328)

F328 gave `create_routine` and `manage_group` a proposal path for a run with no user in the loop,
and taught only the HANDLERS about it. The queued observation then fell straight through to each
kind's success wording over a payload that was not there, so the run was told it had done the
thing: `created routine 'x' from workflow None`, `armed a sequential fire of group None (0
member(s))` (R1200 — a routine trying to accelerate a sibling read that as success and moved on),
and `group None (None) now has members []` (R1183 — which reads as a group that was just emptied).
The same false-success class as F378.

One shared branch in `engine/obs_admin.py` (`QUEUEABLE_KINDS` + `_queued_line`) now renders every
queued shape, checked BEFORE any kind's success line, so the two cannot drift apart again. It
names the proposal id, says NOTHING CHANGED, and carries a self-describing `proposal` line the
HANDLER writes — `manage_group` resolves the target group's real name and member count where it
has the store (`_proposal_line`), and says so plainly when the target does not exist rather than
implying it does. The same line is the summary on the Decisions-page row, so the operator and the
run read the same words. New tests in `tests/test_pending.py` pin every queued shape.

### `manage_group list` names its members (F424, R1142)

The listing gave a name, an id and a member COUNT, and nothing anywhere answered "which routines
are in this group" — which is the group's entire semantics, since the member order is the fire
order. It now lists the slugs in fire order, with the cron and a paused marker.

### A run's file sidebar serves a child's working directory (R1193)

A relative path only means something against the directory of the run that touched it, and the
file-activity read model keyed its rows on the path string alone. The file server then resolved
every row against the parent, so a subtask's working-dir file (`build/page-1.png` under `sub/1/`)
was listed as an openable link and 404'd, while a sibling in the same directory that happened to
exist under the parent opened fine. Rows now carry `bases` — the run-relative directories the path
was seen under — and `GET /runs/{id}/file` tries them first.

### The health stream can be asked whether anything died by signal (F422)

F422's premise was wrong and is corrected here: the five rc=-9 deaths of 2026-09-01 DID produce
health events. They were recorded as `run_canceled` with the signal buried in free-text `detail`,
so a sweep looking for failures read the window as clean. The two close-out events differ by who
asked for the death, not by how the process died, so `rc` and `vm_hwm_kb` are now structured
fields on the row (`log_health_event(**fields)`, dropped when unknown — a boot-recovered orphan
has no process to report on).

### The setup surface reads the SCHEDULE (new)

Two ways a routine's file could stop saying when it runs, both silent. A group with a cron
suppresses its members' own crons (D71), so a member that kept one names a time it will never fire
at — `steward-hub-maintainer` recorded 23:00 while firing at 06:30 in its group's chain, and
`moltbook-heartbeat` recorded 00:00 while firing with Morning Brief. The mirror is a routine with
no cron in no scheduled group, which is a good on-demand design and indistinguishable from an
oversight. Both are now NOTE rows on the setup surface. `rsched validate` also reports the
instance-level case no routine's surface can see: a scheduled group with no members, which fires
nothing on every tick and leaves a `group_chain_done: 0 member runs` that reads like success.
(Two live groups are in that state.)

### The workflow matcher ranks on MECHANISM before subject (R1181/R1165)

Two patterns can describe the same subject and differ only in the machinery a routine is born
with. Ranked on subject words, the matcher picked the one whose prose was most colourful, and a
steward-page task was born wired to an external JSON store that 403s from this host. The suggest
prompt now states the deciding dimension first: how state persists, what is published, what is
read each run, and whether a human edits the output in between.

### The console, reworked — "watchfloor"

A new design system (`static/base.css` rewritten, `views.css` migrated onto it), built on one
distinction: **the two kinds of urgency here are not the same kind**. SIGNAL (cyan) is the machine
working — a live run, an open stream, the daemon link — and is the interactive colour. SUMMONS
(coral) is what waits on a PERSON: an open decision, a gate, a blocking ask, the badge. IRIS
(violet) is structure. A single amber accent used to carry the brand, every heading, every link
AND every warning, so "this is a heading" and "answer me" were the same colour.

Type now says who wrote the words, from system faces only (the no-webfont rule is absolute):
system-ui for the console's own voice, mono for anything a counter emitted, and a reading SERIF
for anything a mind wrote — run summaries, a transcript's narration, questions, chat, markdown
bodies. The longest screen in the product is an agent explaining what it did.

Structure: the nine-tab strip that overflowed into a sideways scroll became a left RAIL grouped by
what a destination is for (Work / Fleet / System), collapsing to an icon rail and then to a bottom
tab bar. Location is said ONCE — the page kicker that repeated the breadcrumb on ten views is
gone. A three-state theme control (auto / light / dark) ships with a real light theme, applied
before first paint; dark stays the default so nobody's console changes under them.

New: the **watch ribbon** (`static/components/ribbon.js`), a band of the last 24 hours and the
next 6 on every page — every run coloured by how it ended, every fire still to come, a hairline
at now. A console for work that happens while you are away should not make you navigate to find
out whether any did.

Fixed while rebuilding: `refreshStatus()` opened with a call to `gateNav`, a function deleted in
0.142.0. It threw a ReferenceError on every 30-second tick, so the catch ran instead and the
daemon lamp was switched OFF thirty seconds after every load and stayed off — a console
permanently claiming the daemon was down. Nothing caught it because the poll was reached only on a
timer no test outlives; the boot path and the poll are now the same call, which puts it under
every UI test. And a dropdown is sized by its options rather than by the page: four stacked
full-width selects on a chart card are one inline toolbar.

### The steward hub: a project registers itself

Answering "does a hub of child pages really need a routine firing at it every night" — for
registration, no. A project needed its slug in `store.php`, its title/standfirst/module/width in
`pages/generate.py`, and a generated `index.php` uploaded, all three the maintainer's and all on
its schedule; until they landed `api.php` answered `400 unknown project` to a routine publishing
perfectly correct state. `miz-grant-steward` lost three runs and part of a deadline to that queue.

`store.php` now derives the project set from the store (`known_projects()`) and validates a slug's
SHAPE rather than its membership of a list; `put-state` accepts a well-formed slug it has not seen,
so a routine's first publish creates its project. `p.php` is one page shell that reads the
project's own state document for its title, standfirst, language, body module and width (the new
optional `page` key), and the root `.htaccess` routes `/<slug>/` to it for any path that is not a
real directory — so a project that owns its body keeps serving exactly as it did. The converged
one-shot `migrate.php` is deleted. The maintainer's recipe now says the sweep, the byte-diff and
the root check are a script's job, not a turn's, and it holds the `scripts` capability and a
`report` trigger so a sibling's ask reaches it when it arrives.

### The reduction pass the revision owed

A six-slice adversarial audit of the whole tree — every candidate refuted before it was
proposed — produced 28 verified items. What it found is worth stating plainly: **the fat is
thin, mechanical and everywhere**, and the largest single win was policy residue rather than
duplication. Six surveyors independently reported `engine/`, `web/` routing, `readmodels/`,
`search/`, the config/endpoint/daemon layer and `static/`'s shared modules as already clean,
with their deliberate non-DRY choices (the flat per-kind observation renderers, the four
`for_*` fallback rules, the three item-status precedences) correctly marked do-not-collapse.

Removed outright, all verified reachable-by-nothing:

- **`suggest_rules_permissions`** — the creation-time rules/permissions/deliberation
  preselection. It has had no caller since 0.164.0 and is superseded by `recommend_setup`,
  which judges a routine's setup on its PAGE, against the finished recipe, as advice beside
  every toggle (D108) — creation cannot judge a recipe it is about to write. Three docs
  asserted the opposite and were corrected. Its three `scaffold()` parameters went with it.
- **The `params` and `progress` channels** through `scaffold → decompose → _pipeline`: no
  caller ever supplied either, so `_params_markdown` always returned `""` and the three
  generation prompts always concatenated an empty note.
- **The `llm_process` contextvar** in `endpoints/instrument.py`, plus the `process=` parameter
  the `ChatEndpoint` protocol never declared. The one live attributor is the daemon, which
  stamps `process_id` on the way into the task centre.
- **`existing_tags` / `normalize_tags`, `statemap.norm`, `groupnotes.write_note` /
  `shared_group`, `pyworkflow`'s `funcs` and `format` keys, `api_workflows`' unreachable
  markdown branch.** Each was alive through its own test and nothing else — the shape vulture
  cannot see, because a test IS a caller.

Written once instead of N times, where N had already drifted:

- **`fold_usage`** (`endpoints/base.py`), beside the usage vocabulary it folds. Four
  hand-rolled accumulators. `usage_total` now seeds `in`/`out` explicitly, because a fold adds
  no key for a zero reading and status.json must carry both even for a run that spent nothing.
- **`memo.fingerprint`** — `registry` and `util_stats` each carried a byte-identical nine-line
  copy of a function documented as canonical. `util_stats`' snapshot also wrote through an
  ad-hoc tmp+rename instead of `paths.atomic_write_json`, against the house rule.
- **The child-run MODE vocabulary.** `engine/child.py` declares itself the single owner "so the
  kind copy, the observations and the docs cannot drift apart" — while `obs_children.py` and
  `cli_render.py` each hardcoded their own `"sequential" ? … : …` pair. Both now ask the owner.
- **`queued_message`**, **`post_token`**, **`reload_into`/`rewrite_block`**, **`panelSection`**,
  **`svgEl`**, **`deleter`**, **`statSection`**, the admin-token toggle, `resolve_token`'s copy
  of `key_from_env_file`. Several of these are net-zero or slightly net-positive once the
  docstring is counted, and they were kept for one reason each: the message-id regex is the only
  thing keeping `answer-*` files out of a PUT, the OAuth POST is the one place a client secret
  goes on the wire and its two copies are exercised a token-lifetime apart, and the admin header
  name pairs with `engine/admin.py`'s constant.

**And two the reduction pass itself caused, both caught by the gate.** The `panelSection`
extraction removed the per-section `fill()` the Settings controls call to show the result of a
delete or an add — the helper now hands `render` a `reload` callback, which is the honest shape
anyway. And the console rework had renamed the palette in the stylesheets but not in the inline
styles the views build in JS: `--muted`, `--line` and `--surface` no longer existed, so those
rules resolved to nothing, while `--ink` and `--ink-2` still existed and now mean TEXT, so two
panels were painting their background in the foreground colour. Every token referenced from JS
now resolves against the real palette.

**Two bugs found in passing.** `search/index.py` set `synchronous=NORMAL` on the first
connection but not on the schema-mismatch rebuild — and it is the rebuilt connection that is
cached for the process lifetime, so after any `SCHEMA_VERSION` bump the index fsynced every
commit of what its own docstring calls a pure cache. And `groupnotes.TEXT_CAP` was applied only
in the dead writer, so a real note — written by the sibling routine itself, unvalidated — reached
the reader's state digest and prompt uncapped. The cap now runs on the read, which is the only
half this module owns.

Six colour regressions from the console rework were repaired too: the mechanical `--amber →
--signal` migration had made the budget meter's warning state identical to its normal state, a
finished child identical to a running one, and a tone literally named `amber` render cyan. Two
hardcoded hexes that escaped the token migration (a group lane, the recipe-length bars) could not
follow the theme and are now tokens.

### Five converged migrations deleted, and one of them was reverting live work

The rule is "historical data migrations are NOT kept: each runs once on the production instance
and is deleted after convergence". Five were still in the daemon's boot path — traits→rules
(0.164.0), the `library_sync:` config key (0.165.0), three forced seed utils (0.166.0), group
members as records (0.181.0) and the settings-template layer (0.269.0). All five verified
converged against the live instance, all five gone, with their call sites and their tests: about
590 lines of `src/` and 250 of `tests/`, and the daemon's boot is five lines of real work again
instead of five migrations interleaved with it.

**`migrate_seed_utils` was not merely dead.** It force-copies `git-sync`, `instance-export` and
`remote` from `util-seed/` over the live library on EVERY boot, and `remote` had since been
revised twice by a routine — 509 lines to 529, adding the `pull` mkdir-p fix for R1140/R1176 with
its own selftest. The library's own log carries two `migrate: install 1 seed util(s) over live`
commits, so it had already thrown routine-authored work away twice, and the next restart would
have done it again. The live version is synced back into `util-seed` first; all ten seed utils now
match their live copies.

### One LLM-JSON call, written once instead of four times

Every suggester in `workflows/suggest.py` — rank the workflows, propose rules and permissions,
recommend setup, write a routine's description — carried its own copy of the same twenty-line
"ask, and retry once on a schema violation" loop. The copies had drifted: three called
`for_system()` OUTSIDE the try, so an instance with no `system_model` configured got an
`EndpointError` out of a function whose own comment promised it "never 500s the creation flow".
Extracted to `_ask_json`, which returns `(obj, why)` so `suggest()` keeps telling the user whether
the suggester was unavailable or its reply was malformed — a distinction a test pins, and which a
first pass at this had quietly dropped.

### The console palette was declared three times

`:root`, `:root[data-theme="light"]` and a `prefers-color-scheme` copy each carried the whole
token set, which is the shape that drifts: a colour corrected in one and not the others is a bug
nobody sees until they switch theme. One `light-dark(light, dark)` declaration per token now, with
the three states selected by `color-scheme` alone, and the two elevation shadows built from colour
tokens because `light-dark()` takes colours only. Verified against the live daemon in all three
states.

### The seed library was months behind the live one

`sync_seed_library_docs` only ever ADDS, so live edits win and `library-seed/` rots silently: six
rules had drifted and six more existed only live, along with a permission and six workflow
patterns. A fresh install was getting materially worse prose than the instance that authored it.
Synced, and the two tests that pinned an exact workflow/version set now assert what actually
matters (the three the system itself depends on are present; the version is read from the pattern).

### `status-page`: the write-root expectation was wrong on every holder

`expects: {fs-write: ["*"]}` produced an `interrupts` row on all seven holders and was false for
each: a status page is published through an upload channel, and the documents a routine generates
land in its own routine directory, which the sandbox always permits. Dropped — the same mistake
`git-checkpoint` made and reverted within a day. The rule instead gains what its holders genuinely
could not do without: fs-READ on the shared kit (R1160), plus the `WEB_AUTH_SOURCES` requirement
and `gate.php?diag` (R1143/R1175) and the docroot-relative meaning of `gate-file.php?p=` (R1199).
Thirteen holders were granted the read root.


## [0.276.0] — 2026-09-02

### The conversation-header fork button is gone; forking is per-message only (D113)

Operator: the header `⑂ branch` button sat "at the top ... where we don't want it." It was the
last entry point that forked at a TYPED turn number — the translation-from-"this reply"-to-a-number
that R1006 already removed for the per-message control. Removed from `branchControls`
(`static/components/branches.js`): the button, its prompt handler and the now-unused `isLive`
parameter (dropped at the `conversations-head.js` call site too). Kept, exactly as the operator
asked: the per-message `⑂ branch from here` on any reply (R1006), the lineage line, and the
`↩ hand back` button. `forkAt` stays the one guarded fork path. `tests/ui/test_branches.py`
now forks through the per-message control and asserts the header carries no fork button.

### Goal-panel condition rows no longer break per-word or overflow the sidebar (F421)

Operator: in the run/conversation sidebar goal panel, the condition text (`.goal-row`) broke at
every word while a long `.goal-note` did not break at all, running past the viewport. Cause: the
`.goal-meta` holding the note was `white-space: nowrap`, giving it an unshrinkable width that
overflowed the narrow sidebar AND starved the flexible `.goal-text` down to ~0, forcing per-word
wrapping. `.goal-meta` now shrinks and wraps (`min-width: 0; overflow-wrap: anywhere`, nowrap
dropped), so the note wraps and the condition text keeps its width.

## [0.275.0] — 2026-09-01

### A reply carries a ⟲ rewind-to-here control, beside ⑂ branch

Operator: *"rewind is a button at the top instead a small button at the message the user wants
to rewind to."* Rewinding a conversation (D69: truncate the transcript at a turn, archive the
tail, re-open live) lived only in the run view's `⟲ rewind` control, behind a prompt asking the
user to TYPE the turn number — the same translation-from-"this reply"-to-a-number that R1006
removed for forking. Now each assistant reply carries a per-message `⟲` next to its `⑂` branch:
the clicked reply's own turn is the cut point, so no number is typed. It confirms (the cut is
destructive, though archived and reversible), then POSTs `/api/runs/{run_id}/rewind`. Like a
fork it is terminal-only — on a still-live conversation the control toasts "rewind it once the
reply has finished" rather than acting. The run view's turn-prompt `⟲ rewind` stays for cutting
at a turn no reply names. New shared `rewindTo()` in `branches.js`, a `.rewind-msg` corner
control in `chat.js`, wired through the conversation view; covered by a `tests/ui` flow test.

## [0.274.1] — 2026-09-01

### Hygiene: drop two dead re-exports and a retired `manage_group` prompt parameter

A fresh-eyes sweep (self-audit) found three stale surfaces, each proven with a repo-wide
reference search:

- **`manage_group` prompt taught a retired parameter.** The capability digest in
  `engine/capabilities.py` still told every `manage_group`-capable routine that `` `split`
  marks two-phase members `` — but the F292 two-pass `split` machinery was retired (D90,
  2026-08-16), the action schema defines no `split` field, and no handler reads one. A routine
  that followed it would emit a `split` the schema rejects. Clause removed.
- **`web/api_routines.py`** re-exported `active_run_dir` and `guard_not_active` under a
  now-false `# noqa: F401 — siblings historically import from here`; nothing imports from that
  module (every real consumer imports the two from `routines_common`). Dead names and the
  stale noqa dropped.
- **`endpoints/claude_cli.py`** re-exported `token_source` (dead — its real consumers, the
  Settings endpoint and a test, import it from `claude_cli_wire` directly) under a stale
  comment claiming the Settings card imports the wire vocabulary from the adapter. Entry
  removed, comment corrected to name only the `STRIP_VARS` test surface.

## [0.274.0] — 2026-08-31

### Routines get a comprehensive generated description, not just their name

Operator: *"routine descriptions must be generated way more comprehensively … wrt purpose,
requirements, side effects, and dependencies w other routines."* Until now both create paths wrote
`description = name` — the description field was literally the routine's name, carrying nothing.
Creating a routine now generates a comprehensive description via one system-model pass
(`workflows/suggest.generate_description`): from the routine's own task plus the catalog of
sibling routines that already exist, it writes flowing prose covering **purpose** (what one run
produces and why), **requirements** (permissions / secrets / inputs / external services),
**side effects** (what it writes, publishes or sends outside itself), and **dependencies with
other routines** (which it feeds, consumes from, or shares a store / group with — named only from
the real sibling catalog, never invented). Wired into BOTH materializers — the conversation's
confirmed `create_routine` (`engine/create_routine._materialize`) and the queued-proposal path
(`web/api_pending._materialize_routine`) — so a routine born either way gets the same description.
It degrades gracefully: an empty task, a missing system model, or a blank reply all fall back to
the name, so creation never fails on it. Existing routines' descriptions are unchanged by this
commit (backfilling them is a separate, operator-gated step — see the open decision). (F414)

## [0.273.1] — 2026-08-31

### A util that declares both `roots` and its own private store now reaches that store

A conversation trying to pair WhatsApp hit `sqlite3.OperationalError: unable to open database
file` even with `fs-write` granted for the session dir (R1136). Root cause: `sandbox.wrap` chose
the wholesale-`roots` mount and the private-store admission by an exclusive `if/else`. A util that
declares BOTH — `whatsapp`: `fs: roots, rw $WHATSAPP_SESSION_DIR, rw /home/mark/whatsapp-sessions` —
took only the `roots` branch, which subtracts EVERY library-declared private store (its own
included) from the wholesale mount and never ran the admission loop, so the util lost access to the
very store it declared. The private-store admission now runs unconditionally after the (still
subtractive) `roots` mount, re-admitting each of the util's OWN declared stores against the grant
that covers it — strictly additive, grant-bounded, and deduped by the spec. (R1136, F415)

## [0.273.0] — 2026-08-31

### Answered decisions withdraw their phone notification

Operator: *"can you withdraw phone notifications on decisions once they've been responded to?"*
A Web Push notification for a decision (`tag: rsched-<qid>`) stayed in the phone's tray after the
operator answered it — a stale alert for a settled decision. Now `web/push.py::notify_new_decisions`
diffs the already-pushed set against the still-open-and-unanswered decisions and, for each one that
has since been answered or withdrawn, sends a same-tag `{close: true}` push; `static/sw.js` handles
that payload by clearing the tagged notification (`getNotifications({tag})` → `close()`) instead of
showing a new one, and the qid is dropped from `push-notified.json` so a decision that re-opens
later alerts afresh. (msg-4, F409)

### The Settings page no longer shows a phantom "none" secret

Operator: *"why is there a 'none' secret in settings?"* Six utils declare `secrets: none` (an
explicit "no secrets", symmetric with `net: none` / `calls: none`) — but the header parser stripped
only `(none)`, not bare `none`, so it read "none" as a secret NAME and surfaced it as a needed
credential. `utils_header.parse_header` now excludes `none` too, matching the `calls:` line's
handling. (msg-5, F410)

### Internal: single source for the engine poll interval

`engine/subruns.py` carried a dead duplicate `POLL_S = 2.0`; the constant now lives only in
`engine/loopconst.py` (its stated purpose). No behaviour change. (F396)

## [0.272.1] — 2026-08-31

### The ability & rule cards no longer break on a phone

Operator, on mobile (screenshots): *"The cards look awful and are broken on mobile."* Below the
620px breakpoint the ability-card / rule-card grid templates dropped content into the wrong
column, so text was crushed to a sliver and wrapped one word — or one **letter** — per line:

- **A stack row's entity fell into the 9px dot column.** `.ab-row` becomes `9px 1fr` on mobile
  with `.kind` pinned to column 2, so the entity div auto-placed into column 1 (the 9px dot
  column) on the next row — a value like `detach` wrapped vertically, one character per line.
  The entity now shares column 2 with the kind.
- **An available ability's on/off/when block fell into the checkbox column.** `.avail-row`
  becomes `auto 1fr`, and the effect block auto-placed into the narrow `auto` (checkbox) column,
  wrapping the sentence to one word per line. It spans the full width on its own row now.
- **The rule row kept the "full description" expander in a ~150px side column.** `.rule-line`'s
  `1fr auto` mobile template left the effect text barely a third of the row. The expander and the
  effect block each take the full width below the checkbox·name row now.

Pure CSS (the three `@media (max-width: 620px)` placements). New phone-viewport UI test
`tests/ui/test_setup_check.py::test_cards_keep_real_columns_on_a_phone_viewport` asserts the
entity and effect columns keep real width and the page does not overflow horizontally.

## [0.272.0] — 2026-08-30

### "Recommended setup" — a second reading of the recipe, with reasons

The setup surface answers the FORWARD question — "given what this routine holds, what does it
still need?". It had no inverse: nothing looked at what a routine actually DOES and asked whether
its rules and permissions are the right set for that. So the choice of which rules bind and which
permissions are held rested on the operator reading each of ~40 toggles against the recipe by
hand (a routine page rage-click on the description expander made the friction visible).

A new **Recommended setup** panel on the routine page, sitting above Permissions & General rules,
now provides it. One system-model pass reads the routine's recipe (its description + `main.md`)
against both catalogs — using each doc's `effect.when` ("hold it when …") clause as the test —
and returns, per item, whether this routine should hold it and a one-line reason. The panel
surfaces only the MISMATCHES: *consider adding* (recommended but unheld) and *consider removing*
(held but unneeded), with the already-aligned ones counted, not listed; a set that matches reads
as "Looks right".

- **Advisory, never automatic.** It flips no switch — the Permissions and General rules panels
  below remain the only place a toggle changes, so the operator stays the one who decides and can
  ignore any suggestion. "The user must be able to change it if they disagree" is the default,
  not a feature: the recommendation is a reading, the toggles are the control.
- **Backend** `workflows/suggest.recommend_setup(server, cfg)` — the reasoning-carrying inverse of
  `readmodels/surface.py`, mirroring the sibling suggesters' schema-guarded retry and graceful
  degradation (no endpoint → `available: false`, the toggles still work, the page never 500s).
  Served read-only at `GET /api/routines/{slug}/recommendations`, computed live and never stored
  (the library moves under a routine, so a persisted answer would be stale).
- Hallucinated slugs are dropped against the live catalog; an item the model does not mention
  keeps its current state as the default verdict.

## [0.271.0] — 2026-08-30

### The on / off / when rows lay out correctly

0.270.0 shipped the right three fields in a broken box (operator: *"ugly AND broken"*). Three
faults, all the same shape — text that silently overflows instead of wrapping, which no
assertion about CONTENT can see:

- **A grid item defaults to `min-width: auto`,** so a bare `1fr` text column refuses to shrink
  below its longest line: the sentence was CLIPPED at the ability card's edge instead of
  wrapping. Every column holding a description is `minmax(0, 1fr)` now.
- **`.when` is the console's TIMESTAMP class** (`base.css`, `white-space: nowrap`), and the
  third row carried it as a modifier — so that one row alone never wrapped and ran off the
  card. It is `.advice` now.
- **`.rule-line`'s template did not line up with its children:** `190px` was landing on the
  checkbox, so the description got an `auto` (max-content) column and the name was drawn on top
  of it. Tolerable while the description was one line; not with a block.
- The third label is **`when`**, not `hold it when`: all three share one column, so the longest
  one sizes it — a phrase there overran into the sentence with no gap. Italic, under `on`/`off`,
  is what makes the short word read as "when to use it". `▸ full description` no longer wraps
  to two lines either.

`tests/ui/test_setup_check.py` now asserts `scrollWidth == clientWidth` on every effect row and
that the name and description boxes do not overlap — the machine check for exactly this class,
none of which reproduces in a unit test of the component.


## [0.270.0] — 2026-08-30

### Every toggle states both sides, and when to hold it

Operator, on the routine page: *"permission, capability, and general rule descriptions still
don't provide actionable information — `ask-policy`: `when and how to involve the user` — wtf
does that mean?! the control element is a toggle?! how are you supposed to know what 'on'
means?!"* Right on both counts, and neither thing a conduct doc already has can fix it: the
TITLE names a topic, and the BODY is written to the RUN in the imperative ("read the error
before you try again") — an instruction for the agent, not a description for the person
deciding whether to switch it on.

A toggle is a COMPARISON. So every rule and permission now carries an `effect:` block of three
fields, and the row shows all three:

    with:    answers its own questions first and interrupts you only for a decision that is yours
    without: asks you whenever it is unsure, and waits — or decides alone without saying which
    when:    the routine runs unattended and you do not want pinging for things it could look up

- **The side the routine is actually in is emphasised**, the other dimmed, so the row reads as
  "this is what you have, and this is what you would have instead". An earlier one-line version
  of this shipped a prefix identical on all 25 rows, which carried no information and ate the
  width the description needed; it is gone.
- **`when:` answers the decision the control actually asks** — not "what is this rule about" but
  "is it for THIS routine".
- All 48 live docs and all 42 seed docs have the block; the linter requires all three fields,
  a length floor, and refuses `with` and `without` being the same sentence with a negation. A
  test asserts the shipped set passes the bar it sets.
- The keys are `with`/`without`, not `on`/`off`: YAML 1.1 reads a bare `on:` as the boolean
  true, so a hand-edit on the Library tab would silently produce a key nothing reads. The UI
  still labels the two sides "on" and "off", which is the toggle's language rather than YAML's.
- A slug no longer wraps mid-word in the two lists (`ask-` / `policy` read as two words).

## [0.269.0] — 2026-08-30

### A settings template is a preselection again, not a layer

Operator decision, reversing 0.262.0. Adopting a template now COPIES its values into the
routine's own `routine.yaml`, once, and the link is gone.

**Why the layer read badly.** A routine's own file recorded only its DIFFERENCES from its
template, so opening `routine.yaml` told you almost nothing about what the routine could do; the
routine page had to explain a second inheritance chain stacked on the group's; and
`template_except:` existed purely to subtract from a layer nobody could see. The cost of copying
is the leverage — editing a template no longer reaches its adopters — which is the correct trade
for a *starting point*. A live shared config is what a GROUP is, and that layer stays (D82).

- **Nothing resolves a template at runtime.** `RoutineConfig` has no `template` or
  `template_except` field, `load_routine` no longer takes a `libraries_home` (it was threaded
  solely to resolve one, along with the `_ACTIVE_LIBRARIES` process-global — both gone), a group
  can no longer name a template, and the setup surface has no template rows.
- **`POST /api/routines/{slug}/adopt-template`** is the write. Lists union, maps fill only what
  the routine left unset, its own value always winning — the group merge's rules applied once.
  It returns what it CONTRIBUTED, because an adoption that silently changed nine things is the
  layer's illegibility in a different costume. Adopting twice is harmless; adopting a second
  template adds to the first. `grants` is never copied: a grant is a settled decision a person
  made about one routine, and a template pre-answering one would be a template exposing a secret.
- **The panel is an action, not a view.** "Start from a template" previews what applying would
  ADD (already-held entries greyed), applies it, and then those values are ordinary entries in
  the panels that own them — editable and removable there. Three concepts (inherited / set here /
  subtracted) collapse to one button.
- **`workflows.scaffold` writes the fitted template in FULL**, so a new routine's file says what
  it is from its first line.
- **MIGRATION(expires=2026-09-30)** — `migrate_template_layer` materializes every existing
  routine's template contribution into its own file at daemon boot, applying `template_except:`
  on the way in and then dropping both keys. Without it a differences-only file would silently
  lose its permissions, rules and capabilities the moment nothing resolved a template. The test
  asserts the EFFECTIVE config is unchanged, which is the actual contract.


## [0.268.0] — 2026-08-30

### A new routine is born knowing what DONE means, and the console stops printing "null"

- **`create_routine` carries `stopping`.** The creation flow has always asked what DONE looks
  like for one run, in the user's own words (F383) — the answer went into the instruction prose
  and nowhere else, so every routine ever created started with an EMPTY goal document and was
  bounded by its budgets alone. That is precisely the state D98 was taken to end, and it is why
  no routine on the instance has a populated `state/stopping.json`. The answer now rides the
  call as one condition per entry and `workflows.scaffold` seeds the store from it. Omitted
  rather than invented: a condition the user did not state is one every later run must account
  for. The queued-proposal path materializes through the same argument, so a routine created
  from the Decisions page is born the same way.
- **`append` / `replaceChildren` stringify a `null` argument into the literal text "null".**
  `util.el()` drops null children, which makes `el("div", {}, cond ? node : null)` the house
  idiom and safe; the DOM methods do not, and the two read identically at the call site. The
  settings-template panel shipped with a stray "null" after "read it" and a "nullnull" between
  its two layer lists (reported from the live console). The same bug was already in two other
  places nobody had noticed: Settings → Public URL rendered one once a URL was set, and a queued
  message row rendered one where its timestamp would be.
- **`expects:` gets the criterion it was missing** (`docs/rules-permissions.md`): declare it
  only for an UNCONDITIONAL presumption, one where a holder without the entity can do nothing
  the doc describes. It produces an `interrupts` row on EVERY holder, so a doc whose prose
  applies only sometimes turns that row into noise — which is why `git-checkpoint`'s was added
  and removed within a day. A survey of all 48 live rules and permissions against that bar
  found no fourth candidate: the messaging and mail docs presume SECRETS and session dirs,
  which the util-header join already carries, and the rest presume nothing external at all.
  Three declarations is the whole set, not a starting point.
- **`tests/test_static_dom.py`** is the guard. The console is no-build vanilla ES modules, so
  nothing but the browser catches this, and a stray word of text raises no JS error for the UI
  suite's collector to see. The check reads every `append`/`replaceChildren`/`prepend` call and
  fails on a bare `null` at the top level — nested `el()` children, which are filtered, are
  deliberately not flagged.


## [0.267.0] — 2026-08-30

### The library's blast radius is on the page; three read-only answers stopped being invisible

- **The Library tab previews what a save would break — and asks.** `library_impact` has computed
  each holder's setup surface against the current library and against the proposed one since
  0.256.0; `POST /api/library/{kind}/{slug}/impact` has served it — with no caller. The tab
  saved blind; the server's only defence was a 409 naming an `impact_digest` no UI could
  echo back, so a breaking save was a dead end rather than a decision. Now the preview runs
  BEFORE every write: a change that breaks nobody goes straight through; one that breaks
  somebody names each routine and the rows it gains, then confirms with that digest. Opening a
  document states who holds it, because "one copy, no migration, reaches every holder at its
  next run" is the fact you need before you start typing, not after.
- **Deleting is previewed too** — the widest change of all and the one path with no impact
  check at all. `preview_impact` grew its own body (`ImpactBody`, `content` optional) so the
  deletion question the module always modelled is finally askable; `DocBody.content` is
  required, which is why it was not.
- **`library-drift` records have a renderer.** `daemon/library_watch.py` files one whenever a
  library commit newly BLOCKS a routine that holds the changed document; `pending.js` knew
  only the two creation kinds — so a drift record fell through to the group branch and rendered
  as `group: ?` beside a "create it" button whose only possible answer was a 400. They get
  their own band: what broke, what it costs, a link to the routine where the fix lives, and
  dismiss. Nothing proposed them, so nothing tells a "proposer" they were discarded either
  (`notify_proposer` returns False for a record no run queued — that routine is the victim).
- **A group's orphan-capability warnings are shown.** `PATCH /api/groups/{id}` has returned
  `warnings` naming every capability switched on in the shared config that no permission in
  that config requires; nothing read them. The editor now renders them and keeps them across
  its own re-render, so the one moment somebody is looking is the moment they are told.
- **`docs/prompt-anatomy.md` caught up with 0.261.0.** It still said the recipe unlocks when a
  write root covers the routine's own dir; own-recipe writes have been the `write_recipe`
  capability (via `recipe-authoring`) since that release; `routine.yaml` — which the same
  sentence lumped in with them — stays never-writable by any run. The `harness.py` comment
  carried the same stale derivation.
- **`docs/designs.md`** records the `fs:` narrowing review: of 123 utils mechanically migrated
  to `fs: roots`, 113 perform a filesystem operation in their own source and 5 of the
  remaining 10 spawn a child that plausibly does. The candidate set is five, not a hundred — and
  the "narrow the caller-supplied-path utils" idea needs a declaration form that does not exist.

## [0.266.0] — 2026-08-30

### Branch a conversation from the reply you are reading

- **`⑂` on every reply bubble.** Branching is conceptually *fork AT a turn* — the API requires
  one — but the only control lived in the conversation header behind a prompt asking you to
  TYPE that turn number, which meant counting turns in a transcript to split a conversation you
  were already reading (R1006, filed 2026-08-28). A reply IS a clean turn boundary: its `turns`
  is the turn its finish action ran on, exactly what `cut_index_for_turn` snaps to. So the fork
  point is now implied by which reply you clicked and the number is never spoken.
- A user message carries no such control, deliberately: it sits BETWEEN turns, so offering one
  there would have to invent a fork point the API would refuse.
- The header's `⑂ branch` stays, for the fork points a reply cannot name — both entry points
  now run the same `forkAt`, one set of guards and one toast.

## [0.265.0] — 2026-08-30

### The routine page's setup layer: reachable, subtractable, and honest about what it inherited

- **"Settings template" was unreachable.** `routine-config.js` has rendered the picker since
  0.262.0, but `SECTION_GROUPS` never claimed its heading, so `groupSections` dropped it into
  the trailing "More" fold. It now leads **Permissions & practices** — it is the layer
  everything under it overrides, so it is read first. **"General rules" was in the same fold**
  for longer: the group claimed `Practice modules`, a heading that no longer exists.
  A test now asserts the routine page renders NO "More" group at all, so the next unclaimed
  section fails the gate instead of quietly going missing.
- **The picker could not read back what the routine adopted.** `template` was not in the
  `/api/routines/{slug}` payload at all, so a routine running on `basic` showed "none".
- **`template_except:` had no UI anywhere.** A routine could add to its template but never drop
  anything from it — the subtraction the field exists for was config-file-only. Each entry the
  template supplies now carries a drop/restore control; the panel splits what is
  **inherited from the template** from what is **set on this routine**, exactly (the template's
  own config says what it supplies; no guessing from counts). A drop that no longer matches
  anything the template supplies is named rather than left as dead weight.
- **Inheritance provenance was malformed and mis-attributed.** `apply_group_config` hard-coded
  "from the group" into its note, so a template's contribution read `3 from the group from the
  template`; the merge now takes the layer's name. And `inherited_from` was set to the GROUP's
  name whenever anything was inherited — a template-only routine got an empty one, which the
  page rendered as "Some settings below come from the group ''".
- **Goal conditions had no authoring surface on the routine.** The stopping-conditions panel
  (F334/D98) existed only in a RUN's rail, so a routine that had never run could not be given
  one at all; one that had meant opening a run to find it. It is now a section of the
  routine page, leading **Goal & limits** — ahead of the budgets, which is the whole claim of
  D98: budgets are a runaway backstop; this is what decides when a job is finished.
  A note reporting the ABSENCE of conditions was built and then removed: it would have fired
  for every routine (all 28 have none), which makes the always-on setup strip and the engine's
  every-boot note exactly the panels their own docs say nobody reads. Two existing tests said
  so. The panel being where you look is the fix; a permanent banner is not.
- **The effective surface is readable.** The setup-check strip shows only what is UNMET, by
  design — which left "what does this add up to when it IS satisfied?" unanswered anywhere,
  because every panel above shows exactly one layer. A read-only **Effective surface** section
  renders the whole join, satisfied rows included, grouped by the conduct doc or util that
  declares each one (`node.source` is machine-readable for exactly this). It edits nothing: a
  second place to change one value is a second place for it to be wrong.

## [0.264.0] — 2026-08-30

### The gate is 22% faster — and a real failure is reported in half the time

Measured on the 4-core deployment before changing anything — the whole suite, then each half:

| | tests | wall | cpu (of 400%) |
|---|---|---|---|
| whole suite, `-n auto` (=4) | 1899 | **574 s** | 149% |
| non-UI only | 1744 | 252 s | 213% |
| `tests/ui` only | 155 | 361 s | 151% |

The suite was never CPU-bound: at `-n 4` the box used under 1.5 of its 4 cores. The UI half is
the reason — each of those workers drives a chromium (several processes) and serves a uvicorn
in-process, so `-n cores` runs about three times that many runnable processes and the machine
thrashes instead of working. `tests/ui` alone takes ~347 s serially and 361 s on four workers:
four-way parallelism bought it nothing and cost it flakes.

- **`-n auto` now means cores MINUS ONE** (`pytest_xdist_auto_num_workers` in
  `tests/conftest.py`, so `auto` stays portable rather than pinning a literal). Whole suite:
  **574 s → 445 s, 149% → 179% cpu.** Fewer workers is faster because the loss was contention,
  not idleness. `-n 6` was measured too; browser tests collapse: 13 failures in one file.
- **Playwright's action timeout drops 30 s → 15 s for the UI suite.** Nothing this console does
  takes 30 s — a page renders in under two; the slowest whole test in a clean parallel run
  is under 17 — so the old ceiling never rescued a passing test; it only set the price of a
  failing one, which the flaky shield then multiplies by five. In the baseline one flake burned
  31 s and then passed in 8 s on retry. 15 s is still ~7× a normal render.
- **A passing test's tmp dir is removed at its own teardown** (`tmp_path_retention_policy =
  "failed"`). The default kept three whole generations: 85 000 files / 394 MB had accumulated
  under `/tmp/pytest-of-<user>` on the deployment, including an abandoned `garbage-*` tree
  pytest had renamed for deletion and never finished removing — and every session paid to prune
  before it could start. Failed tests keep their directory, which is the only one anyone opens.

Measured and NOT changed, with the reasons:

- **The flaky shield stays at `reruns=4` over all of `tests/ui`.** It cannot be narrowed by
  failure signature: a contention flake and a genuine regression both surface as a Playwright
  timeout (`expect` raises `AssertionError` on one), so `only_rerun`/`rerun_except` cannot tell
  them apart. Narrowing it to a curated list of "genuinely flaky" tests re-creates exactly the
  cost F261 exists to avoid — the run above, taken without reruns, flaked in
  `test_conversation_rail.py`, which no such list would have contained. What was actually
  reducible is the PRICE of each attempt; the timeout change halves it.
- **The three quality gates stay three pytest cases.** Measured standalone: ruff 1.9 s, mypy
  3.0 s, vulture 8.9 s — 14 s of a 574 s run; they run beside everything else under xdist.
  Merging them would save about a second of process spawn and lose which gate went red.
- **Sharing the UI fixture more aggressively is not where the time is.** The library template is
  already session-scoped per worker and copying it costs 0.05 s; a non-first UI test's whole
  setup is ~0.3 s. The per-test cost is the browser work itself.


## [0.263.0] — 2026-08-30

### A routine created from a conversation adopts a template, and you get a link to it

- **`workflows.scaffold` fits a settings template** to what the creation flow already decided
  (`templates.suggest`) and writes only the DIFFERENCES into the new `routine.yaml`. The fit is
  a deterministic score over the requested permissions and rules rather than a model call: a
  wrong guess here writes a wrong DEFAULT into a config file, which is worse than a
  slightly-narrow one you widen on the page. Ties go to the narrower template, and nothing
  fitting falls to `basic`.
- `steward` and `correspondent` hold the same permissions on purpose (the shell is not a
  publishing tool), so the RULES are what tell them apart — a request carrying `status-page`
  resolves to `steward`, one without it to `correspondent`.
- **`load_routine` takes the library home explicitly** (`libraries_home=`), because resolving a
  routine's template needs one and it had no server to ask. Reading the ambient `config_file()`
  there would resolve the DEFAULT config — for an engine subprocess started with `--config X`,
  a different instance's library, which is precisely the class of bug CLAUDE.md warns about.
  The registry scan, `rsched validate` and the engine runtime all pass their own; the fallback
  is the config this process actually loaded, never the ambient path.
- **`bootstrap.seed_libraries` seeds `templates/`** — without it a fresh install would adopt
  templates that were not there.
- **The `create_routine` observation carries the link** to the new routine's page
  (`public_url` when the instance knows its own, the in-app route otherwise) and names the
  template it adopted, so a conversation can hand the user somewhere to go instead of "it
  exists, look for it on the dashboard".


## [0.262.0] — 2026-08-30

### Settings templates: one named starting point instead of five scattered decisions

A routine's setup was five separate decisions — held conduct docs, the capability mapping,
secret exposure, filesystem roots, bound rules — spread across as many panels and almost never
made independently. Reading the 28 live routines makes that concrete: eight rules are held by
two thirds of them, `memory` + `util-authoring` + `util-revision` by nearly all, and the
differences fall into a handful of recognisable JOBS.

- **`rsched/templates.py` + `<libraries_home>/templates/*.md`.** A template carries the same
  keys a GROUP's shared config carries and layers under it:
  `the routine's own routine.yaml > its group's config > its template`. Each layer fills only
  what the one above left unset, with the union/merge rules the group merge already uses, so
  adopting a template **subtracts nothing**.
- **`template_except:` subtracts.** Without it a routine could add to a template but never drop
  from one, and adopting one would cost exactly the granularity it exists to preserve. It names
  permission slugs, rule slugs, utils or gated actions to remove after the merge.
- **Six templates, inferred from the live routines rather than invented**: `basic`, `watcher`,
  `correspondent`, `steward`, `operator`, `maintainer`. Two capabilities are deliberately NOT
  template defaults because their blast radius is the instance itself — `recipe-authoring` and
  `shell`; filesystem roots and machine bindings are absent because they name paths and hosts
  specific to one instance.
- **A group can name a template too** (`template` joins `groups.CONFIG_KEYS`), resolved after
  the group merge so a member's own choice still wins.
- Templates are ordinary library documents: linted (`lint_template_text`), editable on the
  Library tab, carried in the `/api/library` payload, and covered by `library_impact` — a
  revision reaches every adopter at its next run, like every other library edit.
- The routine page gains a **Settings template** section naming what the template supplies, and
  `rsched validate` reports a routine pointing at a template the library no longer has (a note:
  it runs on its own config rather than failing to load).

**Migration**: all 28 live routines adopted a template. **326 permission and rule entries moved
out of 28 routine files into 6 templates**, verified against a before/after snapshot of every
routine's EFFECTIVE config: **nothing was lost**, and the 16 capability-level gains are named in
the release notes rather than left to be discovered.


## [0.261.0] — 2026-08-30

### Editing your own instructions is a permission; unbinding a rule now lands on a live run

- **`recipe-authoring` / the `write_recipe` capability.** A run's writes into its own recipe
  (`main.md`, `stages/`, `tuning.yaml`) used to unlock as a SIDE EFFECT of a user-granted
  `fs_write_root` covering the routine's own directory. That conflated two different
  decisions — "may write files here" and "may reword its own task" — so granting a routine
  access to its working directory silently handed it the right to change what it is for.
  It is a switch now, with its own conduct doc. `routine.yaml` stays sealed under both.
  The three routines that relied on the old coupling were granted it explicitly.
- **Unbinding a general rule takes effect immediately**, symmetric with binding. "Prose
  already in a live context cannot be unsaid" is true of the TEXT and false of its
  AUTHORITY: telling the run the rule no longer binds costs one appended note, so
  `drop_rules` now lands at the next turn boundary exactly as `add_rules` does.
- **…and optionally withdraws the text too.** `erase` rewrites the messages carrying the
  unbound rule into a tombstone — content only, so the transport's user/assistant
  alternation survives. That INVALIDATES the provider's prompt cache from the first edited
  message on, which is why it is opt-in, offered only while a run is live, and why the
  control says what it costs.
- **The general-rules panel says what on and off actually DO** — the two directions, the
  fact that the prose is never inlined (the run reads the library's single copy on demand),
  and that a run may read any rule regardless: binding is what makes one standing.
- The setup surface emits **one row per entity**; two checks reaching the same id (a
  capability worth naming that is also uncovered) collapse to the worst-severity row.


## [0.260.0] — 2026-08-30

### The two setup panels are legible: hold vs available, and a toggle stops rebuilding the page

Operator review of 0.259.0's cards found three real faults and one that was never fixed at all.

- **An ability that is OFF no longer renders a requirement stack.** Every unheld ability painted
  its capabilities with red dots, so a page of perfectly fine configuration read as alarming —
  and said the opposite of the truth: nothing is outstanding for something the routine is not
  doing. Held abilities are cards with their stack; everything else is a compact catalogue row
  with no state at all. Most abilities are off, so this removes most of the panel's noise.
- **Cards no longer stretch to the tallest sibling** (`align-items: start`), which had left
  half-empty cards beside tall ones.
- **The policy dial had its label crushed to one character per line** — a `1fr` column squeezed
  by a `select` in an `auto` column beside it. The dial is now its own row shape; its options
  already say what it is, so the label went.
- **General rules got the same treatment**, which is the part 0.259.0 never touched: twenty
  equal checkboxes in one list answered a question nobody asks. Bound rules read as a short list
  of what the routine PRACTISES; the rest is a catalogue grouped by what the rules are for. The
  two panels now answer the same shape of question and look like it.
- **A toggle stages a change instead of re-laying out the panel.** Both panels build their
  sections from the COMMITTED state and only mark rows as pending, so clicking a control no
  longer destroys it under the pointer — which was breaking the interaction, not just the tests
  — and the diff these panels exist to show survives until you apply it.


## [0.259.0] — 2026-08-30

### Ability cards replace the two-column permissions panel

The last stage of the setup-coherence work, and the one that removes the confusion rather than
annotating it.

- **`components/abilities.js`** renders one card per conduct doc — which already IS an ability,
  prose plus the capabilities it presumes — with the whole requirement stack inside it: the
  capabilities from `requires:`, plus the secrets, private stores and bindings
  `readmodels/surface.py` derived from the util headers. The card's badge states the verdict
  (`ready` / `needs a decision` / `will fail`); the stack states why.
- **The old two-column panel is gone** (`components/permissions.js` deleted). It was faithful
  to the model and asked the reader to do the join by eye — and only two of an ability's four
  halves were on that screen at all: the secrets lived in one panel and the filesystem roots in
  another. Seeing whether "reach a person on Discord" actually worked meant reading four places.
- **Rows are attributed by machine-readable provenance.** Surface nodes gained a `source`
  naming the doc or the utils that put them there, so the cards group by declaration rather
  than by parsing `why` — the join-on-prose mistake invariant 5 exists to prevent.
- A trailing card collects capabilities no held doc asked for (the group-inheritance blind
  spot 0.257.1 made visible), so the panel shows what the routine can do AND what nothing on
  it asked for.
- One fetch of `/surface` feeds both readers on the routine page: the setup check at the top
  and the cards below.
- The panel is used by four surfaces — the routine page, the conversation rail, the new
  conversation composer and the group editor — so the cards are a drop-in with the same
  signature. `opts.surface` is optional: a group's shared config and an unsaved conversation
  have no routine to resolve against and degrade to the two-layer view.

The 153-test browser suite is what made replacing a load-bearing panel safe; three assertions
moved from the old markup to the cards, and five new ones cover what the cards add.


## [0.258.0] — 2026-08-30

### The setup surface gets its reverse reading, and a page to show it on

Stage three. 0.256 declared the missing edges, 0.257 read them forwards; this reads them
backwards and puts the forward reading where somebody will see it.

- **`library_impact.py`** answers "who depends on this, and does this change break them?".
  Not a per-kind diff: it computes each holder's surface against the current library and
  against the proposed one — over a shadow library of symlinks, so the real one is untouched —
  and reports whoever gains a blocking or interrupting row. The approval and the routine page
  therefore cannot disagree about what a gap means.
- **Both interactive writers now carry it.** `write_util`'s approval named nobody at all and
  `write_rule`'s named WHO but never what it broke; both now state the blast radius. The
  Library tab, which has no approval, gets `POST /api/library/{kind}/{slug}/impact` plus an
  `impact_digest` confirm token — a library that moved between preview and save yields a
  different digest and the save 409s. Only a breaking change is gated.
- **`daemon/library_watch.py`** covers what arrives with no writer: a sync pull, a hand edit, a
  restored bundle. It compares the library's git HEAD each scheduler tick (~1ms) and queues a
  `library-drift` pending record per newly-broken routine. A break is a decision, not a
  notification, so it rides the existing `pending.py` queue and inherits the Decisions page,
  the audit trail and browser push — no new outbound channel, which 0.230.0 forbids.
- **The setup check on the routine page** (`components/setupcheck.js`) renders the forward
  surface above the panels, ordered by what each gap COSTS and worded in those terms. A routine
  with nothing outstanding renders nothing at all: a panel that is always there is a panel
  nobody reads.


## [0.257.1] — 2026-08-30

### A capability no held doc asks for is now reported, at both ends

Found by applying a real config decision: dropping `messaging-discord` from a routine did not
clear its failures, because the capability came from its GROUP — whose config block switched
`util:discord` on without holding `messaging-discord` at all.

Three deliberate designs meet at that blind spot and each correctly declines to catch it: the
floor binds a routine's OWN mapping at save; a group's block is not floored (a member may hold
the covering doc itself); and enforcement reads capabilities ONLY, so the doc layer can never
widen a run. A stale docstring claimed `load_routine` floored the merged config — nothing does,
and nothing should.

Nothing is broken when it happens — the routine really can do the thing — so it is reported,
not corrected:

- `readmodels/surface.py` adds a `note` row per uncovered util and gated kind, naming the group
  when the group supplied it. It catches every provenance, including a hand-edited file or a
  restored backup that arrives with no save at all.
- `PATCH /api/groups/{id}` returns a `warnings` list naming each orphan, at the one moment
  somebody is looking. Returned, never raised: refusing would break the legitimate arrangement.


## [0.257.0] — 2026-08-30

### The setup surface: one join that answers "what does this routine still need?"

Stage two of the setup-coherence work. 0.256.0 added the missing DECLARATIONS; this reads them.

- **`readmodels/surface.py`** joins a routine's effective config (group inheritance merged) with
  the library's `requires:`/`expects:` and with the util HEADERS of every reserved util it holds
  — walked transitively over `calls:` — plus the live secrets store, machine catalog and
  connection registry. Every unmet need is reported with what it will COST: `blocks` (the call
  is rejected or fails), `interrupts` (the run stops mid-way to ask you) or `note`.
- **Nothing is stored.** The library moves under a routine, so a persisted resolution would be
  stale the first time somebody ran `write_util`. One function, recomputed at every read,
  answers at all four moments: the routine page, `validate`, run boot, the turn boundary.
- **`GET /api/routines/{slug}/surface`** — the read-only endpoint.
- **`rsched validate` now checks coherence**, not only well-formedness. A routine can parse
  perfectly and still hold a rule telling it to publish into a directory it cannot write. A
  `blocks` row fails the command; a warn or a note is reported and leaves the exit code green.
- **Run boot files an engine note** naming the gaps, so a run plans around them instead of
  discovering at turn nine that a util cannot reach its credential store. Advisory: a broken
  library yields no note rather than a dead run, and a child (which inherits its parent's
  resources, not its config) never gets one.
- **`expects:` stays advisory in the read model too** — it can produce an `interrupts` row but
  never a `blocks` one. A soft edge that fails a build is just a worse `requires:`.
- Two library corrections the first run of the linter earned: `git-checkpoint` lost the
  `expects: fs-write` it briefly had (its own prose already handles "this is not a repo", so the
  row was noise on every holder), and `telegram` marks `TELEGRAM_2FA_PASSWORD` OPTIONAL — only a
  2FA-enabled account needs it, which is exactly what the `?` marker is for.

**First run against the live instance: 28 routines, 11 blocking rows across 5 of them** —
including a routine holding three messengers whose session stores no grant covers, one whose
Discord secrets are declined forever while it holds `messaging-discord`, and two holding conduct
docs whose capabilities are switched off (enforcement reads capabilities only, so they fail
closed). None of these was visible anywhere in the console before.


## [0.256.1] — 2026-08-30

### An unset `$VAR` in an `fs:` declaration names no path

- `fs: rw $SIGNAL_SESSION_DIR` with the variable unset daemon-side resolved to the literal
  string `$SIGNAL_SESSION_DIR` — harmless in the jail spec (no real root matches it) but wrong
  in `sandbox.private_store_paths`, which is the set subtracted from every `fs: roots` util's
  mount. A bogus member of a security-relevant set is a bug even when it is inert. Both the
  admission check and the private-store scan now drop an entry that still contains `$` after
  expansion. Found by probing the running daemon after the 0.256.0 deploy.


## [0.256.0] — 2026-08-30

### The setup graph gets its two missing declarations, and the util jail gets a filesystem axis

The routine setup surface was confusing for a structural reason, not a layout one: the system
declared exactly ONE of its dependencies — a permission doc's `requires:`, pointing at a
capability. Everything else was real but unwritten, so nothing could render it, lint it or warn
about it. This is stage one of fixing that: the two missing declarations, plus the enforcement
the filesystem one unlocks.

- **`fs:` is now a required util-header line**, on exactly the terms `net:` already had —
  undeclared is treated as none, and `header_problems` refuses a util without it. Values:
  `roots` (the run's granted roots, for a util acting on caller-supplied paths), `none`, or
  `rw <path>` / `ro <path>` for a private store the util reaches on its own. Entries combine and
  resolve transitively over `calls:`, like secrets and network. `sandbox.wrap` takes the
  declaration alongside `net` and INTERSECTS it with the run's grants — a declaration narrows
  what the routine already holds and can never widen it, which is what keeps a routine holding
  `write_util` from authoring itself a wider jail.
- **A messenger's session store stops leaking to every other util in the run.** Signal, Telegram
  and WhatsApp authenticate by a linked session on disk — the session directory IS the
  credential — and the only way to reach it was a routine-wide `fs_write_root`, which the jail
  mounted into every util the run called. A path some util claims private is now subtracted from
  the wholesale `roots` mount (`sandbox.private_store_paths`), so the grant stays one explicit,
  auditable, four-state decision while its blast radius drops from every util to one.
- **`SandboxPolicy.own_dir`** holds the routine's own directory apart from its granted roots: it
  is the working directory relative paths resolve against, not a grant to be narrowed, so every
  util keeps it whatever its `fs:` line says.
- **`expects:` — the soft dependency edge** — is now legal in permission AND rule frontmatter
  (`grants.normalize_expects`, `grants.read_library_expects`, both linters). It names entities
  the prose PRESUMES but nothing enforces: `remote-machines` requires the `remote` util and
  expects a bound machine; the `status-page` and `git-checkpoint` rules expect a write root. A
  rule may expect, it may never `require` — that would switch a capability on. Advisory forever
  by design.
- **Zulip's credential moved into the secrets store** (`ZULIP_EMAIL`, `ZULIP_API_KEY`,
  `ZULIP_SITE`). It was the one messenger whose credential was a FILE — `~/.config/zulip/zuliprc`
  — which is invisible inside the sandbox and, unlike the three session stores, is neither
  bind-mounted nor in `deploy/state-paths.sh`, so it did not survive a container recreate. The
  `--config` flag and the `$ZULIPRC` / `~/.config` fallbacks are gone rather than kept alongside.
- **Library migration**: all 136 utils in the live library and all 10 in `util-seed/` carry an
  `fs:` line, derived from their source and biased toward `roots` so nothing changes behaviour;
  the credential-grade cases were reviewed by hand and narrowed. The write_util prompt surface
  (`engine/kindsurface.py`, mirrored in `docs/prompt-anatomy.md`) teaches the new line, so
  authored utils get it right at the cause.


_Nothing yet._

## [0.255.1] — 2026-08-29

### Decisions answers survive a live-run refresh (mobile keyboard fix)

- **The Decisions page no longer yanks focus out of the answer field while you type.** Every
  global SSE event dispatches an `rsched-bus` tick — several a second while a run is live — and
  the inbox reloaded the whole list on each one (`renderList` → `list.replaceChildren`), which
  dropped focus from the answer `<input>`/`<textarea>` mid-type. On desktop that was a flicker; on
  mobile it dismissed the keyboard and the caret every few seconds, so typed answers never landed
  (operator report). `static/views/questions.js` now DEFERS the bus-driven reload while an answer
  control in the list holds focus and flushes it once focus leaves. Covered by a new
  `tests/ui` flow test (focus + in-progress text both survive a bus tick).

### Test reliability

- **`test_kill_child` no longer flakes under parallel-suite load.** The killed child slept only
  0.5s before `finish`, which could beat the parent's kill under `xdist` load and leave the subrun
  `ok` instead of `aborted`. It now sleeps long enough that the kill reliably lands first (the kill
  interrupts the sleep instantly on success), matching the test's own "sleep forever" intent.

## [0.255.0] — 2026-08-29

### Every routine web UI is one notebook

Seven project pages had grown independently on `steward.markwernsdorfer.com`, and each had
re-implemented the same four things — a masthead, a feedback channel, a list of the user's
unconsumed input, and a webhook ping. So they carried the same bugs at different times and got
them fixed at different times, or never. **Two** of the seven could show the user the feedback he
had already sent; **one** refreshed that list after a submission; **one** rendered an approval
draft in a box he could edit. Three feedback contracts were live at once.

There is now one shell. `docs/status-pages.md` is the map.

- **New general rule `status-page`** — the modular half. Publishing a web UI is opt-in per
  routine (`rules:` in routine.yaml, bound to the ten that publish), and a routine that does not
  publish never reads a word of it. It is the first curated rule written from this instance's own
  observed failures rather than from an upstream source: every clause answers a complaint in the
  feedback stores, most repeated across unconnected projects. A `web-publishing` PERMISSION was
  considered and rejected — `requires: {utils: [ftp]}` would gate `ftp` for every routine and
  break the four that publish to other hosts, and the access that matters is already the
  four-state `secret:FTP_SOURCES` grant.
- **One interface and one storage layout, not just one shell.** `/api.php` is the only way any
  page reads or writes anything: `what=state|items|model|feedback|log|all` to read,
  `op=say|revise|retract|advance|put-state|put-items|put-model` to write. Under
  `_store/<project>/` every project has the same six things — a state document, a collection, a
  model of what its item states mean, an append-only feedback log, an append-only write trail and
  snapshots. Three storage designs and three answers to "what has he told us that we have not
  acted on" collapse into one.
- **`migrate.php`** converts every pre-unification store in place — idempotent, additive, deletes
  nothing, dry-runs by default. The one genuine shape change is each radar's feedback
  (`{id, opp_id, verdict, reason}` → the shared row), so a reason Mark typed on the radar months
  ago now appears in the same rail as everything else, editable and retractable.
- **One gate for the host, and it is a cookie.** nginx-level Basic Auth broke three things the
  same way — an installed PWA (which is why weightloss grew its own passphrase gate), the Withings
  OAuth callback, and a 301 that dropped the credential and cost a routine its intake. `gate.php`
  takes a session cookie *or* HTTP Basic against the same secret, so routines keep working
  untouched while people and PWAs get something they can carry. Weightloss's separate passphrase
  gate is retired, and no per-path exception is needed to host it under the hub.
- **The store now refuses a direct GET on any server.** It held Mark's own words behind a
  `.htaccess`, which does nothing on nginx — and this hosting is nginx. Every stored file ends in
  `.php` and opens with a guard line, so the denial is in the file rather than in a config that
  may not apply. Basic Auth still covers the host; this is what holds if it ever comes off, which
  has happened on a sibling host before.
- **The hub's cards are derived, and `projects.json` is gone.** It was one file every routine
  rewrote daily, so editing a stale copy clobbered a sibling's card and every routine had to be
  told to re-fetch first. `?what=hub` now reads each project's own state document, and
  `needs_you` is counted server-side from the open gate and the open question — so it cannot be
  understated by the routine it reflects.
- **`put-items` floors, for every project.** The collection is the only copy of his decisions, so
  an empty set, a shrink past half of what is stored, and an item without an id are all refused,
  and the previous set is snapshotted first — generalised from the floors `freelance-radar` had
  already earned the hard way. `advance` is gated by the project's own transition model, and a
  refused move is recorded, so "why did nothing happen when I clicked" has an answer.
- **New shared kit**, mastered in the library repo at `<libraries_home>/web/steward/`: the design
  system, the shell, a `status` body module, a `board` body module for the two radars, the hub,
  and the data layer. A routine links them and never edits them; it uploads one only if that path
  is absent on the host, so the first routine to run bootstraps and the rest no-op.
- **The whole design is new** — "field notebook": warm grained paper, a red margin rule down the
  sheet that means one thing only (something here is waiting for you), markers in the margin that
  encode state, and three typefaces that separate the page's own voice from a person's from the
  machine's. Light and dark on tokens with a persisted three-state toggle.
- **The radars are rebuilt, not restyled.** ~150 KB of per-radar markup, rendering and CSS is
  deleted in favour of `board.js`, and their `api.php`/`lib.php`/`stage_*.php` go with it;
  `config/pipeline.json` becomes `model.json` and drives the entire pipeline surface, with the
  module hard-coding no stage, label, button or help text. The measured filter tuning is carried
  over verbatim — it is measurement, not design. An intermediate *token bridge* that recoloured
  the old stylesheets was built, verified and then rejected for exactly that reason.

### Fixed

- **Feedback could be permanently lost.** `feedback.php` derived the next sequence number from
  the store's LINE COUNT, which is only correct while the file has never been touched. After any
  truncation, rotation or hand repair it re-issued numbers below every routine's consumed-cursor,
  and everything written from then on was filtered out as "already read" and never surfaced
  again. It now takes the highest stored seq plus one. (Reproduced against the real endpoint.)
- The read endpoint's project allowlist covered two of seven projects, so five pages accepted
  feedback they could never display back. The allowlist now lives once, in `store.php`.
- A `revise` can no longer re-file one entry's revision under another entry's id: the id is
  carried forward from the row being replaced rather than taken from the client.
- A submission no longer appears to vanish: every control refreshes the pending list after it
  writes, on every page (was ards-only — R129/R134).
- Every approval draft is editable and the text approved is the text in the box; every question
  takes a free-text answer, with quick answers as an addition rather than the whole vocabulary;
  every generated document carries its own "not ready" control.
- A status payload missing `feedback_cursor` now says so on the page instead of silently
  re-listing months of already-answered notes.
- A short write to the store returns an error rather than a sequence number for a row that is not
  on disk.

### Hand-off

Ten reports (R1040–R1044, R1047–R1051) carry the migration to each publishing routine; nothing
on the host changes until a routine runs. `sprind` and `birthday-admin` move under the hub with
their own markup; `weightloss` is BLOCKED on a Basic-Auth exception only the operator can make,
and says so rather than publishing into a wall. `grantsforbina` and the guest half of the
birthday site stay where they are, by operator decision.

## [0.254.0] — 2026-08-29

### Removed
- **The `clarification` template routine, and the `kind: template` protection built around it.**
  Nothing read it. The standalone new-routine wizard that copied its budgets, models and rules
  into a clarify session was retired with D59 — clarifying now happens in the conversation's own
  chat — so what remained was a routine on the Routines page that existed only to be protected:
  `guard_template` / `guard_template_dir` and nine call sites refusing to run, archive, message,
  trigger, resume or rewind it, a `protected` flag on its card, and frontend special-cases
  including one still keyed off the literal slug. `web/routines_common.py`'s docstring claimed
  "every clarify session copies" its config; no live code path did.
  Gone with it: `migrate_template_kind` and its test, `test_template_routine`, the `protected`
  option on the triggers and schedule-once cards (`opts` existed only to carry it), and the
  `kind != "template"` filter on the dashboard's meta list. `kind` keeps its one real value,
  `conversation`. The live routine was archived rather than deleted — it holds 18 runs of
  history, and `.archive/` is what this system's delete affordance does.

### Fixed
- **Two `docs/architecture.md` claims that were already false.** It described `wizard_store.py`
  as retaining "the on-disk helpers for that template" (the module was deleted in 0.230.0) and
  said live clarify runs hold the restart drain via `restart.clarify_states` reading
  `clarification/runs/*` (no such function, and `daemon/restart.py` works from `active_states`).
  Both now describe what the code does. The creation section also documents the decision-element
  intake and the always-open workflow list from 0.253.0.

## [0.253.0] — 2026-08-29

### Changed
- **Routine creation asks in DECISIONS, and the workflow list is no longer closed.** The draft
  observation's `next` contract told the agent to "relay this draft to the user in your reply" —
  so the clarification arrived as prose and the user answered a blank text box, composing by hand
  an answer the agent already knew how to offer. Every point still open now goes out as its own
  `ask_user` carrying `options`, which the console renders as numbered picks; the workflow
  question is always one of them, and PRODUCES / DONE must be the user's own words or be asked
  the same way. `kindsurface`'s create_routine PRECONDITION says the same thing to the model.
- **`generate` is a first-class workflow choice.** The pattern catalog now always ends with
  "draft a NEW pattern fitted to this task", because no catalog covers every task and a routine
  built on a pattern that merely almost fits carries that mismatch for its whole life. Picking it
  makes the confirming call draft the pattern inline (`workflows.generate`, folded into the run's
  budget) and build on the slug it wrote. This reverses F387's rejection: the user PICKING it is
  the gate — the `workflows: generate` capability governs a subtask drafting a pattern on its own
  initiative, unwatched, which is a different situation. Generation failure creates nothing and
  never falls back to a catalog pattern: the user chose `generate` over all of them, so building
  on one anyway would materialize the option they rejected under the name they approved.
  The confirmed half moves into `_materialize` — its own step, its own failure modes.

## [0.252.0] — 2026-08-29

### Removed
- **`routine-seed/` and the routine-seeding boot path.** A fresh instance no longer arrives with
  six bundled meta routines, and `bootstrap.seed_routines` / `adopt_seed_routine` are gone with
  the directory they read. The seed had stopped describing anything real: its `self-audit` was
  weeks behind the live routine (eight stages against nine, without the ten-oldest quota or the
  status vocabulary), it hardcoded one machine's `/home/mark/...` paths, and its evidence step
  still tailed `.control/bug-reports.jsonl` — a file renamed to `reports.jsonl` long ago. A seed
  that installs a routine which fails on its own first run is worse than no seed, and `test_seeds`
  could not catch it: the suite pins config, stage references and action kinds, never a path named
  in prose. Meta routines are authored on the instance through the scaffold path like every other
  routine. `library-seed/` and `util-seed/` are unaffected — they seed a library, not a routine,
  and they are still synced at boot.

## [0.251.1] — 2026-08-28

### Fixed
- **`docs/conversations.md` still said deletion was simply permanent**, which stopped being the
  whole truth the moment a nightly mirror existed (0.251.0). The doc-sweep for that release
  covered the deploy surfaces and missed the one page that makes a claim about losing a
  conversation. It now says what the mirror is and is not: a *converging* mirror, in which a
  deleted conversation survives only until the next run propagates the deletion — a recovery
  window of at most a day, real but not to be relied on, and not an archive. The existing advice
  (land the work as an artifact or a project commit) stands unchanged.

## [0.251.0] — 2026-08-28

### Added
- **`deploy/rsched-backup.{service,timer}` — the state mirror runs nightly.** 0.249.0 gave the
  instance an incremental backup; a backup nobody runs is not one, and this is the schedule.
  03:30 with a 15-minute jitter, off the hour and clear of the cron lanes routines fire on — at
  ~90 seconds an incremental run barely occupies a slot, but it should not be reading routine
  dirs while a run writes them. Three settings carry the rest.
  **`Persistent=true`**, because a backup that silently skips every night the host was down is
  not a backup: a missed firing runs once the machine is back rather than waiting for tomorrow.
  **`TimeoutStartSec=2h`**, because the target is a network share — if the NAS wedges, rsync
  blocks in uninterruptible IO indefinitely and every later firing would then skip on the
  `flock`, so the run is bounded and tomorrow starts clean. And **`Nice=10`**, since a backup
  never deserves to compete with a run.
  The units are **not** installed by `deploy/install.sh`, deliberately: the mirror root is
  host-specific and a default install has nowhere correct to point it. Install and enable them
  by hand (deploy/DOCKER.md), and point a host elsewhere with a `systemctl --user edit` drop-in
  setting `RSCHED_MIRROR` rather than by editing the tracked unit.

### Changed
- `deploy/DOCKER.md` gains the enable-it recipe and the linger prerequisite; CLAUDE.md notes the
  timer alongside `backup.sh`, including why `install.sh` leaves it alone.

## [0.250.0] — 2026-08-28

### Added
- **System word lists in the engine image** (`wngerman`, `wswiss`, `wamerican`, ~10 MB). A routine
  that writes German prose checks its own output for ASCII transliteration — `fuer` where `für`
  belongs — and the check's most precise tier weighs each candidate against the real word lists at
  `/usr/share/dict/`. The image shipped without them, so that tier stood down and every ASCII
  digraph read as a transliteration: **159 false positives in one run** (R1009, freelance-radar,
  2026-08-28), against a genuine finding count in the low tens. A check that cries wolf every run
  is one its reader learns to ignore, which costs more than the check was worth.
  The lists are the fix rather than a threshold, because the check already degrades honestly
  without them — it names in its own summary line which lists it had, so a run always states the
  evidence its verdict rests on. The paths it looks for (`ngerman`, `swiss`, `words`) are exactly
  what these three packages provide; verified before the image changed rather than after.

## [0.249.0] — 2026-08-28

### Added
- **`deploy/backup.sh` — a recurring incremental mirror, because the tarball was never a backup.**
  `bundle.sh` writes a frozen snapshot for a ONE-SHOT host move, and DOCKER.md's flow ends by
  decommissioning the source, which is the only reason staleness is acceptable there. This data
  does not hold still: `routines` and `conversations` are rewritten by every run — measured on the
  live instance at ~1600 files and ~90 MB a day — so a nightly re-tar would move ~3.6 GB to
  capture ~90 MB, and between runs there would be no copy at all. Worse, the instance had no
  second copy of anything: 26 of its 29 routine repos have no git remote, and all 85 conversation
  dirs are un-versioned by design. rsync moves the delta and converges.
  Two guards carry the design, both load-bearing rather than defensive. It **refuses a
  destination on the same device as `$HOME`**: the intended target is an autofs/sshfs mount of
  another machine, and when that share is down its mountpoint is an ordinary empty local
  directory — so the mirror would land on the very disk it exists to survive and report success.
  And it passes **`--one-file-system`**, because a routine bound to a remote machine has that
  machine's share sshfs-mounted at `<routine>/mnt/<name>` while it runs
  (docs/remote-machines.md), so a backup firing at that moment would otherwise copy another
  host's filesystem into the mirror. A `flock` stops two scheduled runs racing on one tree, and
  the root is created mode 700 for the secrets it carries.
  Deletion is **`--delete-excluded`**, not plain `--delete`, which the first live run proved
  necessary: rsync PROTECTS excluded files on the receiving side, so anything the exclude list
  gains later sits in the mirror forever. Chrome's `SingletonLock`/`SingletonSocket`/
  `SingletonCookie` are now excluded (shared list, so the tarball drops them too) — they are
  DANGLING symlinks naming the host and pid holding the profile, they made rsync exit 23 trying
  to set times on targets that do not exist, and a restored `SingletonLock` tells a fresh Chrome
  that another instance already owns the profile.
  Measured on the live instance: first mirror 4.5 GB in 27 min, second 50 MB in 85 s.
- **`deploy/state-paths.sh` — ONE copy of the state inventory**, sourced by both `bundle.sh` and
  `backup.sh`. A second consumer with its own path list is exactly how five data homes went
  unbundled for a release (0.248.1), so the list does not get copied; it gets shared.

### Fixed
- **`bundle.sh` could not succeed on a live instance.** `tar` exits 1 for warnings and ≥2 for a
  real failure, and warnings are the NORM here — the chrome sidecar rewrites its profile
  continuously, so files change or vanish between tar's stat and its read. Under `set -e` that
  turned a complete, readable archive into a failed run that printed no summary and no
  next-steps. The two exit classes are now distinguished, and a warning is never swallowed: the
  unstable paths are named, with the note that `chrome-profile` is the one that matters, since a
  torn LevelDB copy restores as a signed-out browser and `docker compose stop chrome` avoids it.

### Changed
- `deploy/DOCKER.md` gains a backup section drawing the migration/backup line explicitly, and
  points at `state-paths.sh` rather than `bundle.sh` as the inventory's authority; CLAUDE.md and
  README.md carry the same distinction.

## [0.248.1] — 2026-08-28

### Fixed
- **`deploy/bundle.sh` left five data homes out of the migration tarball** — every conversation,
  every detached background run and all three messenger links. `docker-compose.yml` binds each of
  them and says in as many words why (they "DIE on every recreate" without the bind; a linked
  messenger session on disk IS the credential, so losing the dir unlinks the account and someone
  re-pairs by phone), but the bundler's `PATHS` had never been widened past routines, config,
  credentials and the library repo. A data home that is mounted but unbundled does not survive
  the migration either — it is the same loss, one host later, and quieter, because a bundle that
  ran clean looks like a complete one. `conversations` and `background` join `PATHS` (core data,
  their absence is a broken install); the three messenger session stores join `OPTIONAL_PATHS`
  alongside `chrome-profile`, since they exist only once that messenger has been paired.
  `.config/gh` — `gh auth login`'s token, re-mintable only by another device flow — was omitted on
  the same terms and joins them.
  The two lists now carry the invariant they answer to: **every DATA bind mount in
  `docker-compose.yml` appears in one of them.** The two exceptions are named in the script as
  decisions rather than left to be re-derived — `.cache/ms-playwright` is a re-downloadable
  browser cache bound to survive a *recreate*, and `tor-data` is regenerable guard state in a
  named volume.
- **`deploy/install.sh` now creates `~/conversations` and `~/background`.** Both homes were built
  lazily on first use, so a fresh install had neither — and `bundle.sh`'s missing-path error
  points the operator at `install.sh`, which would not have made them. The three data homes are
  created together, so a fresh install is complete rather than half-migratable.

### Changed
- `deploy/DOCKER.md`'s migration step now tabulates what the tarball actually carries, what rides
  along only when the feature has been used, and what is deliberately excluded — it had described
  the contents in prose that predated three of the homes.

## [0.248.0] — 2026-08-28

### Fixed
- **The `chrome` sidecar no longer shares the engine's network namespace, because that coupled
  their lifetimes.** 0.247.0 used `network_mode: "service:rsched"`, which put CDP on the engine's
  own loopback and needed no configuration — and which breaks every time the engine restarts. A
  network namespace does not survive its owner being restarted, and the engine restarts itself
  routinely (self-audit's drain-and-exit is a normal event). The browser is then stranded in a dead
  namespace with its sessions unreachable while the container still reports as running, so
  **nothing surfaces it**. Found the same day it shipped: an ordinary version deploy took the
  browser down mid-login, and the symptom that reached a person was "fails to connect".
  The browser now sits on its own network at a pinned address (`172.30.7.10`), and neither
  container depends on the other's uptime. Two non-obvious things make CDP work across that
  boundary, both handled in the entrypoint: **Chrome refuses to bind DevTools to anything but
  loopback** (no flag changes it — DevTools runs on `127.0.0.1:9223` and socat forwards
  `0.0.0.0:9222` to it), and **DevTools rejects a `Host` header that is not an IP literal or
  `localhost`** (so the endpoint is numeric, not the service name). Chrome then echoes that host
  back in the `webSocketDebuggerUrl` it returns, which is what makes a forwarded CDP connection
  work rather than half-work — verified against a real CDP client, not assumed.
  The consequence for callers: `--cdp http://172.30.7.10:9222` is now explicit, where the
  namespace-sharing version inherited the utils' `127.0.0.1:9222` default. noVNC moves to the
  `chrome` service's own port mapping, still bound to the host's loopback only.

## [0.247.0] — 2026-08-28

### Added
- **A `chrome` sidecar: the logged-in browser the `--cdp` utils drive** (`deploy/Dockerfile.chrome`,
  `deploy/chrome-entrypoint.sh`, `docs/browser-sessions.md`). Some sources are only readable while
  signed in — a freelance board's inbox, an application form — and for those the session IS a
  cookie jar inside a browser. Until now that browser lived on an operator's laptop, so every util
  taking `--cdp` (`job-scrape`, `job-inbox`, `job-apply`, `browser-session`) could only run there.
  It is now a compose service, a sibling of `tor` and for the same reason: the engine image stays
  engine-only and the daemon supervises no second process.
  Three choices carry the design. It is **headful under Xvfb, never `--headless=new`** —
  freelancermap's invisible reCAPTCHA scores headless Chrome badly enough to refuse the
  application form. It uses **`network_mode: "service:rsched"`**, which puts CDP on the engine's
  own loopback so `http://127.0.0.1:9222` — the default those utils already ship — resolves with
  no wiring; over the compose network it would fail twice over, because DevTools rejects a `Host`
  header that is not an IP literal and the `webSocketDebuggerUrl` it returns names the address
  Chrome bound rather than the one the client dialled. And it forces **`--password-store=basic`**,
  because there is no keyring in a container and a guessed backend reads as "logged out" on every
  start.
  The profile (`${RSCHED_HOME}/chrome-profile`) is a bind mount and is now part of the migration
  bundle: it holds the sessions, so it is a credential, not a cache. A profile copied off a
  desktop machine does **not** carry its logins — desktop Chrome wraps cookie values under `v11`
  with a key held by gnome-keyring and absent from the profile directory entirely — so signing in
  is a one-time human step, done over noVNC. noVNC is published on the **host's loopback only**
  (`127.0.0.1:6080`): it is a keyboard and mouse on a browser holding live sessions, never a LAN
  port.

## [0.246.1] — 2026-08-27

### Fixed
- **Endpoint credit-balance card and connection-test were 404** (regression from the F393
  split). `web/settings/endpoint_probe.py` was extracted out of `endpoints.py` but its router
  was never wired into the `settings` package aggregate (`settings/__init__.py`), so
  `GET /api/settings/endpoints/{name}/credits` and `POST /api/settings/endpoints/{name}/test`
  — both called by `static/views/settings-endpoints.js` — served nothing. Added `endpoint_probe`
  to the import tuple and the `include_router` loop, plus a regression test asserting the two
  routes are mounted on the aggregated settings router (the existing handler-unit tests passed
  either way, which is why the split went unnoticed). (F395)

## [0.246.0] — 2026-08-27

### Changed
- **A script may use the utils it declares.** `scripts/<name>.py` was sealed off from the
  library — `gu` off PATH, "a step needing a util's capability belongs in the recipe". That
  boundary was doctrine, not safety, and it cut exactly the wrong way: the sub-steps most worth
  moving out of prose are the repeating ones that ACQUIRE something (fetch a mailbox, drive a
  browser session, reach an OAuth connector), and those are precisely what a script could not
  do. A script could always re-implement a fetch with `net: outbound` and a pip dep; what it
  could not reach was the utils wrapping something hard enough that reimplementing it is not an
  option.
  So `calls:` now means for a script what it has always meant for a util. The declaration is
  the whole mechanism, and it is DECLARED-ONLY in both directions:
  - `scripts.needs` resolves secrets and `net:` TRANSITIVELY over the declared utils
    (`utils_run.util_needs`, unchanged — one call tree, one jail, one env). A script calling a
    `net: outbound` util gets the network; it inherits that util's credentials without
    redeclaring them, and a name is optional only if every declarer in the tree marks it `?`.
  - The same `calls:` line earns the library handle: `GLOBAL_UTILS_HOME` + the library root on
    PATH, pointed at THIS library like a util's own sibling calls. A script declaring no calls
    gets no handle at all — the old behaviour, now the default rather than the rule.
  - `scripts.call_problems` refuses the two declarations that would leave a script running
    without the access it needs: a `gu` exec the `calls:` line never names, and a declared util
    the library does not have. Refused at the header with the fix spelled out, the bargain
    `misdeclared` already strikes — failing loudly there beats failing obscurely at the first
    env read or blocked socket.
  - The secret-exposure gate (`gate_script_secrets`) now asks over the transitive set, so the
    four-state grant model covers a callee's credentials exactly as it covers a util call's.
  There is still NO model channel inside a script: a judgment call belongs in the recipe, and
  that is the boundary that actually holds the "recipe is the single interpreter" doctrine up.

## [0.245.0] — 2026-08-27

### Fixed
- **A test whose engine stub stopped taking ran against PRODUCTION** (F394). The daemon spawns
  each run as `python -m rsched.cli engine-run …`, and that child is a FRESH interpreter: it
  inherited nothing from its spawner and loaded `~/.config/routine-scheduler/config.yaml` on its
  own. So when the F393 split moved `engine_cmd` and `runner.py` bound it with `from … import`
  (which copies the reference, so the test's `monkeypatch.setattr` no longer reached the caller),
  `tests/test_scheduler.py` stopped stubbing the engine and silently executed its tmp-homed
  fixture routine `strand` on the live instance — eleven turns and ~8,800 tokens on a paid
  endpoint, two rows (R1001, R1002) appended to the production report ledger, four telemetry rows
  under `~/routines/.control/`, for a routine that has never existed there. That trigger was
  already fixed by resolving the three substitutable names through `runner_state` at call time.
  This release fixes the DEFECT behind it — that nothing refused a tmp-homed Runner spawning a
  production-homed engine — in both halves:
  - **The spawn now names what the child must use, and the child requires it.** `engine_cmd`
    takes the spawner's `ServerConfig` and passes `--config <its source>` plus
    `--homes <registry.homes_fingerprint>`; `engine-run` has NO default for either, loads exactly
    the config it was handed, and REFUSES (exit 2, both sides printed) when that config resolves
    to different run homes than the spawner is using. A config nobody pointed it at can no longer
    be adopted, and a mismatch is a loud refusal rather than a silent fallback to `~`. A spawner
    whose config was never loaded from a file — every test's in-memory `ServerConfig()` — is
    refused in `engine_cmd` itself, before a process exists.
  - **`tests/production_guard.py` is the wider net under the whole class.** A session-scoped
    autouse barrier: no write may land inside the live instance's data homes (covering `open`,
    `io.open`, and the `mkstemp`+`replace`/`mkdir`/`unlink` family behind `paths.atomic_write`
    and `Path`), and no test may spawn this package's CLI at all — that child loads the
    production config by definition. The protected set is derived from `registry.all_homes`, so
    a fourth home is covered without an edit. The rule is deliberately "the instance's data
    homes", not "outside `tmp_path`": tests legitimately write to `/tmp`, to the checkout, and —
    through the real `uv run` the util tests exercise — to `~/.cache` and `~/.local/share/uv`.
  `tests/test_engine_spawn.py` pins both halves of the contract and `tests/test_production_guard.py`
  exercises the barrier through the same chokepoints the codebase writes through.

## [0.244.0] — 2026-08-27

### Changed
- **Every source file is under the ~350-line budget** (F393). 26 were over; 0 are now, across
  222 files. Split by RESPONSIBILITY, never by line count — which is what the finding asked for,
  and the reason `engine/actions.py`'s flat schema and `engine/observations.py`'s per-kind
  wording survived intact rather than being chopped to hit a number.
  Twenty-two new modules, each named for the one job it does. The ones worth knowing about:
  `secretgate` / `authoring` (out of `interact`), `utils_header` / `utils_run` (out of
  `utils_lib`), `grantpolicy` / `policyload` (out of `grants`), `window` / `degrade` (out of
  `completion`), `switches` (out of `control`), `machine_mounts` (out of `machines`),
  `exec_env` (out of `executor`), `runner_reap` / `runner_state` (out of `daemon/runner`), and
  `actionroute` / `finishgate` / `loopsetup` / `loopnudge` (out of `engine/loop`).
  - **Six import cycles surfaced**, five created by the splits. Every one was broken by MOVING
    what the far side needed to the module that actually uses it — never by deferring an import
    into a function, which hides a cycle rather than removing it. Twice that meant extracting a
    leaf both sides depend on (`runner_state`, `loopconst`).
  - `EngineLoop` now **declares its 37 fields**. Lifting construction out cost the one place
    that said what an instance holds, and mypy said so immediately; declaring them is better
    than the original, where the shape was implied by the order of 139 lines of assignment.
  - Callers were repointed rather than re-exported. A compatibility shim would have hidden where
    things live, which is the opposite of the point.

### Fixed
- F393 itself said "seven files over budget". It was 26 of 179 — the original count came from
  reading only the top of a sorted list. Corrected on the instance, along with an honest note
  that the 0.234–0.243 work ADDED to the backlog before clearing it: `machines.py` and
  `control.py` crossed the budget as a direct result of R514 and F337/F338.

## [0.243.0] — 2026-08-27

### Added
- **Stopping-condition verification — v2 of F334/D98** (`engine/verifier.py`). v1 proves a run
  ACCOUNTED for its goal; it cannot prove the account is true. A run could write
  `[s3] met — PDF verified` having never opened the PDF, and the gate, the writer and the panel
  would all agree it was done — silently and confidently. At the finish a SECOND model (the
  `tool_call` role, never the main one) is now asked, per condition the summary claims `met`,
  whether the run's own transcript supports the claim.
  The design is dominated by the two ways this could be worse than the problem it solves:
  - **False blocks.** A judge that blocks on doubt is a machine for stranding finished jobs over
    evidence outside the tail it was shown. It is FAIL-OPEN at every level: an unavailable
    endpoint, an unparseable answer, a condition the judge did not mention, and anything short of
    an explicit `supported: false` all ACCEPT. The prompt states that absence of evidence is not
    evidence of absence and that a wrong `false` strands a finished job while a wrong `true`
    costs only a stale mark a human can correct.
  - **A livelock.** A stubborn model and a stubborn judge would trade refutations until the
    budget died — and a dead budget is precisely the outcome stopping conditions exist to
    replace. A condition is challenged **at most once per run**: the finish is set aside for one
    turn carrying the objection and how to overrule it, and a re-asserted verdict STANDS. The
    disagreement is then recorded rather than enforced — `disputed` on the condition, in the
    `stopping_update` event, and as an amber `disputed` mark in the goal panel (hover for the
    objection). The engine gets one intervention, the model keeps the last word, the operator
    gets the audit trail.
  Cost is naturally scoped: one subcall per finish attempt, and only for a run that HAS active
  conditions claiming met — `unmet` claims are never judged, since the run already agrees. A run
  with no goal pays nothing, which is most runs. 18 module tests + 2 loop-level, the important
  one being that a re-asserted verdict ends the exchange instead of looping.

## [0.242.0] — 2026-08-27

Recovering a dropped commitment, and closing the mechanism that dropped it. The user's order
of 2026-08-14 — *"a run should stop on a MEANING-level condition, not only on budget walls"* —
became F334/D98=A, whose five parts shipped as three in 0.208.0. The sidebar panel was deferred
into F324, F324 shipped the rail on 2026-08-26 and closed `addressed` without it, and the
`stopping_update` writer was recorded as a "documented deviation". So the feature was enforced in
the prompt and the finish gate while being invisible, over a store whose status column nothing
ever wrote. All of it lands here, with the logical connectives the flat list never had.

### Added
- **Goal conditions are LOGICALLY CONNECTED.** A condition belongs to a GROUP that combines its
  members with `all`/`any`, and the document combines the groups the same way — two levels, which
  expresses "(A AND B) OR (C AND D)" and its dual. Deeper nesting is where a UI and a weak model
  both stop being able to reason, so it is deliberately not offered. Two further connectives about
  WHEN a condition is live: **`requires`** holds one dormant until another is met (the sequencing
  case), and **`stage`** scopes one to a routine stage module — the "per-stage routine conditions
  LATER" half of the original order. A dormant condition is shown to the run so it sees the shape
  of the job, but is never demanded in the accounting.
- **The GOAL rail panel** (`static/components/stopping.js`), on the conversation view and the run
  view alike: `✓` met / `○` open / `–` dropped, met conditions dimmed and struck through in the
  same visual language the state graph uses for a done phase, each group's ALL/ANY chip editable,
  a per-group tally, a dormant row greyed with what it waits for, and a `goal met` verdict chip.
  Click a mark to overrule the run's conclusion. 6 browser tests.
- **Routines get stopping conditions at all** — `GET/PUT /api/routines/{slug}/stopping`, one
  implementation shared with conversations in the new `web/api_stopping.py`. The read carries the
  evaluated verdict so a panel never re-derives the boolean structure and disagrees with the
  prompt the run was given.
- **`readmodels/orphans.py` + `GET /api/items/orphans`** — deferrals whose CARRIER closed without
  delivering them, bannered on the Messages page. This is the mechanism that lost the panel: the
  changelog `items` join marks the carrier addressed and nothing checks what was deferred INTO it,
  because a deferral is only prose. Run against the real ledger it finds exactly one row — the
  lost panel — and no false positives; `tests/test_orphans.py` uses that history as its fixture.

### Fixed
- **The run's verdict is now recorded.** `stopping.record_accounting` parses the model's own
  `[s<n>] met|unmet` lines at the finish and stamps them into the store, emitting a
  `stopping_update` transcript event (the event D98 specified and 0.208.0 skipped). Without it a
  condition stayed `open` however often a run reported it met — the reason the status column was
  dead. `met` is STICKY, so a later run cannot silently reopen a goal the user has been told is
  done, and `unmet` records the REASON while staying open. Parsing is over the whole summary with
  each note bounded by the next entry or its own line end: models routinely put two entries on one
  line, and an unbounded note swallowed every entry after it.
- The finish gate demands an accounting for **ACTIVE** conditions only — a dormant one cannot yet
  have happened, and demanding a verdict on it only teaches the model to write noise.
- The digest renders the **structure**, not a flat list. A run that cannot see two conditions are
  an OR treats them as an AND and works past where the user meant it to stop.
- **Documented at last**: the feature had no coverage in CLAUDE.md, `docs/`, or the prompt-anatomy
  reference despite being a live prompt surface since 0.208.0.

## [0.241.0] — 2026-08-27

### Added
- **A config change made while a run is live becomes an in-flow message** (F337). A run reads
  `routine.yaml` at boot and composes its prompt once, so a mid-run edit landed on disk with the
  run unaware of it — except for the ad-hoc live paths the system had grown (an access-request
  decision bridges into the live policy; the `/rules` picker pushes an added rule through
  `control.json`). "I changed it while it was running" therefore meant two different things
  depending on which field was touched, and the run was never told either way.
  - The fix is **not** more live paths. It is ONE classification table, `configflow.CLASSIFICATION`,
    mapping every `RoutinePatch` and `ConversationPatch` field to **LIVE** — adopted at a turn
    boundary: `budgets`, `deliberation`, `grants` — or **NEXT_RUN**, each with the reason the
    operator is shown.
  - `tests/test_configflow.py` **fails on a patch field the table does not declare.** That guard
    is the anti-drift mechanism the finding asks for: a new config field cannot be added without
    deciding which half it is in, so the silent divergence cannot quietly come back. (It caught a
    lazy `"as fs_read_roots"` cross-reference during this very build.)
  - Both PATCH handlers call `routines_common.signal_config_change`, writing a `config_change`
    signal into the live run's `control.json` — the seam that already exists for reaching a
    running run, not a second one. `engine/control.apply_config_change` adopts the live half at
    the next turn boundary and appends ONE `ENGINE NOTE` naming EVERY changed field and which
    half it is in, with a `user_injection` transcript event beside it. Naming the fields that
    WAIT is as load-bearing as naming the ones that land.
  - Edge-triggered through the same applied-ts ledger as the model/deliberation/rule switches, so
    a resumed leg never re-fires a stale signal; per-field best-effort, so a value the run cannot
    use is logged and left for the next run rather than ending a live run — and the note still
    says the field changed. 20 tests. The F337 entry is deleted from `docs/designs.md`, which now
    holds only F363.

## [0.240.0] — 2026-08-27

### Added
- **Approval-free intra-group notes** (F335, `rsched/groupnotes.py`). Members of a group are a
  team with a shared purpose, but one member reaching another went through the same `report`
  machinery as reaching a stranger: a ledger row, a delivery into the target's `inbox/`, and an
  open maintenance item on the Messages page until somebody closed it. For teammates coordinating
  inside one chain that is heavyweight — it turns "here is the file I staged for you" into a
  tracked work item a human has to close.
  - A member writes `<group-store>/notes/<sibling>/note-*.json` with an ORDINARY file write — the
    group store (D67) is already in its fs roots, so there is **no new action kind**. The engine
    renders waiting notes into the state digest at boot as `NOTES FROM YOUR GROUP` and DELETES
    them as it reads, mirroring how `inbox/` drains: delivered exactly once, never a backlog
    somebody has to clear by hand.
  - The harness contract names the convention beside the store root and LISTS the actual sibling
    slugs — a channel a run does not know about is a channel that does not exist, and "write to a
    member" is not actionable without their names. It also says when to use `report` instead.
  - **No approval, no ledger row, no Messages-page item.** The safety argument is the BOUNDARY,
    not a gate: the store is injected into every member's fs roots and nobody else's, and a note
    between routines sharing no group is refused — reaching outside the group is not something
    this channel declines, it is something it cannot express. That is exactly why it may be
    approval-free. Membership is read LIVE, so a routine removed from a group loses the channel
    in both directions at once.
  - A NOTE is coordination; a REPORT is work an OWNER must act on, tracked until answered.
    `report` is unchanged. 11 module tests + a composer end-to-end. The F335 entry is deleted
    from `docs/designs.md`.

## [0.239.0] — 2026-08-27

### Added
- **A scheduled run can propose a routine or a group instead of being refused** (F328). R353 is
  the case: routine-improver reached a run holding a fully designed, user-approved routine plus
  the two-phase group it belonged in — all five gate questions answered — and could materialize
  neither, so the design was hand-carried back to the operator to paste in. The restriction to
  conversations was right (a scheduled run has nobody to design WITH); its consequence was wrong.
  The missing piece was never permission, it was a QUEUE.
  - `create_routine` and `manage_group` are now surfaced to every run, and the HANDLER decides:
    a root conversation materializes through D92's preview→confirm exactly as before; anywhere
    else the same call writes a proposal to `.control/pending-creations/<id>.json` and returns an
    observation saying plainly that nothing was created and not to re-issue it (a second call
    would queue a second proposal every run). A within-reply CHILD is still refused outright and
    never sees the kinds — a sub-workflow must not create routines or reshape groups as a side
    effect. The queue is for a run that HAS a user, just not right now.
  - The **Decisions page** grows a band of proposals — what would be created, from which routine
    and run, with the full instruction the routine would be BORN with — and one click materializes
    through the SAME `workflows.scaffold` / `rsched.groups` calls, or discards.
  - **The engine still never writes `routine.yaml`**: the web layer materializes, exactly as it
    already applies forever-grants. The proposing routine learns the outcome the ordinary way,
    from an inbox message its NEXT run drains — nothing is woken, because a creation is not urgent
    and a queue that started runs would be a scheduler in disguise.
  - Ungated, like `report`: a proposal nobody approved creates nothing and reaches nobody but the
    operator's own page, so the approval IS the gate and it is a human. `manage_group`'s `list`
    still answers directly — it writes nothing, and a run that cannot read the group store cannot
    propose a correct change to it; every mutating verb queues.
  - `rsched/pending.py`, `web/api_pending.py`, `static/components/pending.js`; 12 backend tests
    and 5 browser tests. The F328 entry is deleted from `docs/designs.md`.

### Fixed
- **The prompt still described the retired two-phase group fire.** D90 replaced F292's `split`
  flag with BRACKETING (an inbound-router member first, an outbound-sender member last) and the
  engine stopped emitting the `GROUP FIRE PHASE: ingest/outbound` text — but `manage_group`'s kind
  copy still offered `split` as a field, so the model was told about a field that is not in the
  action schema, and `docs/prompt-anatomy.md` still documented the boot text. Both now describe
  the model that exists. `tests/test_prompt_anatomy.py` pinned those dead strings and kept
  passing: it only checks doc ⊇ engine, so prose that outlives its feature is invisible to it —
  the needles are removed with a note saying so.

## [0.238.0] — 2026-08-27

### Added
- **Conversation branching and hand-back** (F325, built in full). The `branch` MODE of the
  child-run contract F338 settled one release earlier — which is why it landed first: a branch
  arrives as a scheduling mode of an existing concept rather than a fourth name for it.
  - **⑂ branch** forks a conversation at a chosen turn into a new one that inherits the parent's
    config wholesale (models, permissions, capabilities, rules, connections, folder access,
    budgets, deliberation), its `main.md`/`instruction.md`/`tuning.yaml`, its `state/` and
    `attachments/` — the files the inherited history refers to — and a COPY of the transcript to
    the fork point. Because it is a copy, **a branch cannot mutate the original**: two lines of
    work run side by side and neither can damage the other. `artifacts/` is deliberately NOT
    copied — the branch produces its own and hands those back.
  - The fork point snaps to a clean turn boundary through the SAME cut the D69 rewind uses
    (`history.cut_index_for_turn`, extracted so both read one definition): a prefix ending
    mid-turn would replay as an assistant action with no result. Per-event `usage` is stripped
    from the copy — the parent already accounted for that spend, and counting it again in the
    branch double-counts the same tokens across two conversations. The header is rewritten to
    name the branch, since every read model keys off it.
  - A terminal `status.json` beside the copied transcript is what makes a branch an ORDINARY
    continued conversation: its first message goes down `resume_terminal` and replays the
    inherited history, so the engine carries no branch case at all.
  - **↩ hand back** (only on a branch) delivers its result to the parent: artefacts copied into
    `artifacts/from-branch-<slug>/`, and one inbox message carrying the summary and naming them.
    **Merging is deliberately NOT a transcript merge** — two divergent histories cannot be
    interleaved into one coherent conversation, so the parent receives a RESULT and decides what
    to do with it. It is the same delivery shape a detached background task uses, which is the
    point: a hand-back is the child-run result and the parent already knows how to read one. It
    does not wake the parent; its next reply drains the message.
  - The header shows the family in both directions — where a branch came from and at which turn,
    and which branches came off this conversation, with a deleted parent named rather than hidden.
  - `rsched/branches.py`, `web/api_branches.py` (`POST …/branch`, `POST …/handback`,
    `GET …/lineage`), `static/components/branches.js`; 15 backend tests and 4 browser tests
    against the real console. The F325 entry is deleted from `docs/designs.md`; the narration
    moved to `docs/conversations.md` and `docs/architecture.md`.

## [0.237.0] — 2026-08-27

### Changed
- **Branches, subtasks and subroutines are ONE concept: the CHILD RUN** (F338, completing it).
  0.233.0 shipped the load-bearing half — the hand-back. This is the vocabulary unification it
  was waiting on, landed before conversation branching (F325) so branching arrives as a MODE of
  the existing frame rather than as a fourth name for the same shape.
  - New `engine/child.py` holds the single definition: a child run is an isolated run with its
    own directory, its own budget sliced from the parent's remainder, its own recipe or pattern,
    and a declared relationship to its parent. `spawn` (parallel), `subtask` (sequential) and a
    conversation `branch` are three scheduling MODES of it. Deliberately **no fourth action
    kind** — the action names each state a real scheduling intent a run chooses between, and now
    share one contract underneath.
  - The module owns the mode vocabulary the prompt renders (`mode_noun`) and the hand-back path
    (`handback_dirname`), so the three surfaces that drifted apart — kind copy, observations,
    docs — read from one place. That drift is what let the spawn contract claim children share
    the parent's working directory (R409/R410), which cost a run a recovery detour.
  - **One exit headline for every mode**: `CHILD RUN FINISHED (<mode noun>) — #<n> …`, with the
    mode named inside it instead of changing the noun (`SUB-WORKFLOW FINISHED` /
    `SUBTASK FINISHED` are gone). Only the follow-on instruction still differs, because only that
    genuinely differs — a sequential child's result feeds the next one.
  - `spawn` and `subtask` kind copy rewritten to state the shared contract once and then the one
    thing that differs, so neither can describe the shared half differently from the other again.
  - `docs/subtasks.md` → **`docs/child-runs.md`**, rewritten around the concept with a mode table
    and the three-part contract; the F338 entry is deleted from `docs/designs.md` (it shipped) and
    F325 is recorded as unblocked. `tests/test_child.py` pins the vocabulary, the hand-back path
    and the single headline.

## [0.236.0] — 2026-08-27

### Changed
- **The new-routine intake WALKS its clarification instead of being trusted to** (F383, closing
  R476/R492/R494/R503). 0.230.0 shipped the mechanical half — the draft observation carries the
  pattern catalog and the relay must name the chosen pattern plus one alternative. What was left
  is that nothing made the agent SETTLE what the routine produces and what done looks like before
  a draft could be presented as decided. Fixed at the cause, in the generation and teaching copy —
  deliberately NOT as a machine gate (a validator cannot tell a real answer from a plausible one)
  and NOT with `create_routine`-only action fields:
  - `clarify-instruction` (the intake pattern) names two MANDATORY answers — what the routine
    PRODUCES each run (the artefact, named, and where it lands) and what DONE looks like for ONE
    run — carried into `marry()` as the FIRST blocking questions, explicitly outside the
    "stop asking once the remaining unknowns wouldn't change how the routine runs" rule, since
    those two always do. `choose_pattern()` now requires the runner-up named with why the winner
    beats it: a general-purpose pattern is a legitimate answer only when you can say what it
    beats, and picked silently it is just the absence of a decision.
  - The `create_routine` kind surface states the clarification as a PRECONDITION with a checkable
    test — could you QUOTE the user's own answer for what it produces and what done is? If you
    would be inferring either, ask instead of drafting.
  - The draft observation's `next` tells the relay to name any of the three that is still the
    agent's inference rather than present it as the user's decision.

### Fixed
- The live library's `clarify-instruction` had drifted behind two completed renames — traits
  retired for general RULES, and `communication` split into per-channel messaging permissions. Its
  ownership rule said `trait/permission` and `communication channels`; both now match the seed.

## [0.235.0] — 2026-08-27

### Fixed
- **A machine share that did not mount can no longer be mistaken for an empty one** (R514).
  `gu dir-tree mnt/predator/…` answered `entries: 0` while the box itself was populated, so
  a run could conclude its source was gone — or write files that never left the daemon. Two
  causes, both at the provisioning seam:
  - `sshfs` **daemonizes**, so its zero exit meant "the helper forked", not "the share is
    readable"; and the mountpoint directory is created *before* the mount, so a failure left
    an empty directory standing exactly where a populated share had been promised. The engine
    now polls `machines.mount_is_live` until the path is a real mount whose root actually
    reads (a stale FUSE endpoint raises `ENOTCONN` there and counts as dead, never as empty).
    A share that never comes up has its **empty mountpoint removed** — so a read fails on a
    missing path and a write cannot silently land on local disk. Only an empty directory is
    ever removed; clearing a lookalike never becomes deleting data.
  - The CAPABILITIES block advertised `files mounted at mnt/<name>/` from the catalog's
    `share:` field **alone**, i.e. from config intent, never from fact. The machine row now
    reports what the run actually has: the mount line only once proven live, otherwise
    `SHARE NOT MOUNTED this run (<reason>)` naming why and pointing at the remote transfer
    path instead. Child runs inherit that state along with the fs roots that reach the mounts.
  `mount_routine_shares` returns `(mounted, {name: reason})`; the machine-row rendering moved
  into its own `capabilities._machine_notes`.

## [0.234.0] — 2026-08-27

### Removed
- **Four converged one-shot migrations deleted** (F358) — the delete-after-convergence
  policy running its own course, ahead of the `2026-08-31`/`2026-09-01` expiries that would
  have turned `tests/test_policy.py` red:
  - `conversations.migrate_conversations` + `_seed_converse_into_library` +
    `_RETIRED_BUDGETS` and their boot call in `cli.py` (the 0.114.0 converse-v3 re-render and
    budget lift). Verified converged: all 81 production conversations already carry
    `workflow.version 3` and none holds a retired budget value.
  - `web/api_audit.py`'s `_LEGACY_COMMENT_RE`/`_LEGACY_NOTE_RE` recovery of structured
    fields from pre-0.8x plain-text feedback. Verified converged: of 22 queued inbox
    messages instance-wide, zero are untagged `web-audit` messages. The `via: "web-audit"`
    marker itself stays — it is the live channel, not migration residue.
  - The two migration-coverage tests that pinned them
    (`test_migrate_conversations_relifts_pattern_and_budgets`, the legacy-message block in
    `test_api.py`).

## [0.233.2] — 2026-08-26

### Fixed
- **API error toasts no longer render `[object Object]`** (F392, failure-visibility). The
  shared `api()` helper built its `Error` from FastAPI's `detail` assuming a string — but a
  **422** returns `detail` as a LIST of `{loc, msg, type}` validation records, and
  `new Error(list)` stringifies to `[object Object]`. That was the opaque toast the Decisions
  page showed when a rejected config-patch apply hit a forbidden field (companion to the
  0.233.1 root fix). A new `detailMessage()` helper renders whichever shape `detail` arrives
  in as a legible one-line message (`field: message; …`), so every view's error toast is
  readable. Deterministic in-browser test in `tests/ui/test_error_message.py`.

## [0.233.1] — 2026-08-26

### Fixed
- **A `config_patch` that binds a rule now applies through the Decisions page** (F392). A
  decision carrying `config_patch: {"rules": [...]}` — the shape a revise-recipe run or
  config-optimizer proposes to bind/unbind a routine's general rules — hit `RoutinePatch`'s
  `extra="forbid"` on `PATCH /routines/{slug}` and 422'd, because `rules` had no field there
  (rules previously only reached the dedicated `/routines/{slug}/rules` picker). The Decisions
  page's `approve & apply` rendered the pydantic error list as `[object Object]` and the
  operator-approved binding silently never landed (observed: a self-audit run's approved
  `unexamined-is-not-clean` binding did not persist). `RoutinePatch` now carries a `rules`
  field that REPLACEs wholesale through the ONE canonical path (`rules.apply_changes`) —
  validated against the library, with `main.md`'s derived `## Standing practices` tail
  resynced — so the config-patch apply and the picker share behaviour. An unknown slug is a
  legible 400, not the opaque 422.

### Added
- **A child run hands a file back by writing it** (F338 first increment, from R409/R410).
  Children run in their own dir by design — a shared writable tree between concurrent siblings
  is a race the engine would have to arbitrate — but that left the parent to know the child's
  path, search it and copy files out by hand: a procedure every routine reinvented, and one
  the spawn contract used to describe *wrongly* ("they share your working directory"; they
  never did). A child now writes what it is handing back into its own `artifacts/` — the same
  deliverable convention the Artifacts panel lists and a detached background task already uses
  — and on the child's exit the engine copies it into the parent's `artifacts/from-sub-<n>/`,
  naming the landed paths in the finished-notification. **No action-schema change**: the
  hand-back is opt-in by writing, so a child that writes nothing hands back only its summary
  and every non-child run pays nothing. The `spawn` and `subtask` contract copy now says so.

## [0.232.0] — 2026-08-26

### Fixed
- **A 402 that prices out `max_tokens` now degrades instead of killing the turn** (F362). The
  finding was recorded as "OpenRouter credits exhausted — awaiting top-up or acceptance"; it
  was mis-diagnosed. The balance is not empty (240 granted / 227.77 used / 12.23 left) and the
  402 that killed `sprind-application-review:20260818-093527` said *"You requested up to 16384
  tokens, but can only afford 9590"* — a requested output ceiling costing more than the
  remaining balance, which recurs as any balance drains, so a top-up only postpones it. The
  OpenAI-compatible adapter now retries once at the ceiling the provider itself names, logging
  the squeeze. An affordable ceiling below ~600 tokens is NOT retried (failover should take the
  turn rather than a stub reply), and a 402 naming no number is left alone — there is nothing
  to degrade to, and inventing one would mask a genuinely empty balance.

## [0.231.1] — 2026-08-26

### Fixed
- **The container entrypoint repairs a root-owned `~/.cache/uv`.** Running `uv` inside the
  container as root — which is what a `docker exec` without `-u` gives you — leaves
  `environments-v2/` (or a `wheels-v6/` entry) owned by root inside an otherwise mark-owned
  tree. The uid-1000 daemon then cannot create its per-script environment and **every util
  call in the instance** fails with `failed to create directory …/environments-v2/…:
  Permission denied` — for every routine at once, self-audit included. Observed live on
  2026-08-26; the same class as the F97 `~/.local/state` bug and the earlier `util-stats`
  root:root one, which the entrypoint already guards. `~/.cache/uv` joins the chowned mount
  points and additionally gets a recursive repair of just the offending entries (`find !
  -user mark`), so a warm cache stays cheap.

## [0.231.0] — 2026-08-26

### Added
- **The conversation composer picks general RULES and OAuth connections before the first
  reply** (F339). Both are pre-start choices by nature and rules especially so: a rule reaches
  the prompt through `main.md`'s Standing-practices tail, which is materialized at create
  time, so one bound afterwards never governs reply #1 — and reply #1 fires the moment you
  send. `POST /api/conversations` takes `rules` (validated against the live library — an
  unknown slug is a 400, never a conversation holding a practice nobody wrote) and
  `connections` (validated like the routine PATCH: the account must actually be connected).
  `/api/conversations/defaults` serves the rule catalog + the default set. The two pickers are
  the SAME components the routine page uses, extended with a save-button-less pre-start mode
  rather than copied.
- **A missing connection now names its one-click grant** (F321, from R333). `google-api`
  failing with "$GOOGLE_ACCESS_TOKEN is not set" was explained in prose in a finish summary,
  while a missing fs-write root in the same conversation correctly produced a typed access
  request the user could approve inline. A util call that fails while declaring a
  connection-token var this routine does not bind now leads its hint with the
  `connection:<provider>` request route — ahead of the generic repair text, because the call
  is not broken, it is ungranted.
- **`docs/designs.md`** — specs for work decided but unbuilt (F363, F338, F325, F328, F337,
  F335), each with the evidence, the shape and the first increment, so the next session is not
  re-deriving them. Entries are deleted as they ship.

## [0.230.2] — 2026-08-26

### Added
- **A C toolchain in the engine image** (F341, operator choice 2026-08-26). A util declares
  its dependencies as PEP 723 inline metadata and `uv` builds them at call time, so a package
  published only as an sdist — or whose wheel misses this platform — needs a compiler right
  there; without one the util fails at its first run with a build error no routine can act on.
  `build-essential`, not just `gcc`: a dep needing g++ or a Makefile is exactly the case a
  partial toolchain still fails on.

### Fixed
- **F289 does not reproduce — headed Chromium works in the engine container.** It was
  recorded as an image-level defect awaiting a rebuild: on 2026-08-05 the bundled Chromium
  spawned its crash-reporter as a separate `--type=crashpad-handler` re-exec that aborted with
  `--database is required` → SIGTRAP, and no browser-process launch flag reached it. Verified
  in the live container: both bundled builds (chromium-1228 and chromium-1234) launch headed
  under `xvfb-run` with no crashpad error, and a headed Playwright launch reports `DISPLAY=:99`
  and a User-Agent with no `Headless` token — i.e. genuinely headed, not a silent fallback.
  The `page-fetch` util's comment telling every run that `--head` is UNUSABLE was therefore
  steering runs away from a working capability; corrected in the library (`revise page-fetch`).

## [0.230.1] — 2026-08-26

### Fixed
- **The widened Artifacts panel surfaced build intermediates** — a regression in 0.230.0 found
  by checking the live instance rather than the tests: frame-fill-lab's panel returned 103
  rows, 76 of them page PNGs under `reports/build/`, burying the 27 real deliverables. A
  rendering pipeline builds in `<dir>/build/` and copies the finished file up, so those are
  intermediates by definition. `SKIP_SEGMENTS` (build, __pycache__, node_modules) and any
  dot-segment are excluded from listing.

## [0.230.0] — 2026-08-26

### Added
- **Routine-scoped secrets** (D103, operator decision 2026-08-26 — R497). The store had one
  flat instance-wide namespace, so `eye-stabilize-folder`'s SFTP credentials could only be
  spelled `SFTP_USER` (colliding with every other SFTP consumer) or `EYESTAB_SFTP_USER` by a
  convention nothing enforced. A routine now has its OWN store, `secrets.d/<slug>.env`:
  - **Ownership is the grant.** A scoped secret is implicitly exposed to its routine's runs,
    invisible to every other routine, and it SHADOWS a central value of the same name (it
    rides `executor._extra_secrets`, which wins the child-env merge). The four-state
    `secret:<NAME>` exposure gate subtracts a routine's own names before deciding anything,
    so a run never files a request for a credential it already holds.
  - **Declared-only is untouched.** A util receives the var only if its own `secrets:` header
    (or a transitive `calls:` sibling's) declares it — scoped or central alike.
  - **CAPABILITIES lists the two sets apart**, so the model can tell "already mine" from
    "shared, may need asking for".
  - **Write surface**: an *Own secrets* section on the routine page (its own card, beside
    Connections and Machines), backed by `web/api_routine_secrets.py`. Values are write-only;
    the API answers with names, and marks which central names a routine shadows.
  - Scoped stores live under the CONFIG dir, never in the routine dir — that dir is
    `git add -A` autocommitted and auto-pushed, so a secret written there would leave the
    host. Archiving a routine drops its store, so a credential never outlives its owner or is
    inherited by a later routine reusing the slug.
- The routine page groups **Secrets & access** (own secrets · shared-store exposure · settled
  denials) as its own fold; secret exposure used to sit in the trailing "More" catch-all.

### Changed
- **`communication` → `messaging-discord`** (D104, operator decision 2026-08-26). With the
  operator decision surface deleted, the permission gates one thing only — posting in the
  user's Discord as them — which is precisely what its `messaging-*` siblings do. It now
  reads as one, tags and conduct alike (`library-seed/permissions/messaging-discord.md`),
  and the shared closing paragraph in the four sibling docs no longer points at a
  `communication` doc that does not exist.
- **The `create_routine` draft observation carries the workflow catalog** and its relay
  contract now requires naming what the routine produces, what DONE looks like for one run,
  and the chosen pattern **plus one alternative** (F383, from R476/R492). The pattern choice
  was previously trusted to the conversation agent, which could default to `general-task`
  silently while the preview merely named it.

### Fixed
- **The routine page's group dropdown re-rendered a persisted assignment as "none"** (F388,
  from R499/R500). It read `group_managed`, which answers a different question — "does a
  SCHEDULED group drive this routine's fires?" (D71) — and is null for a member of an
  unscheduled group. Membership now comes from `/api/groups`, which the select already
  fetches; the sub-line and the join toast say plainly when a group has no schedule, instead
  of claiming its chain drives the routine. Regression-tested for both group kinds.
- **`create_routine` accepted an unknown workflow at draft time and failed at materialize**
  (F387, from R493) — i.e. the failure landed at the expensive step, *after* the user had
  confirmed. The pattern is validated against the live library on the FIRST call, and
  `workflow: "generate"` is refused with the reason it is a `subtask` capability rather than
  a library slug.
- **Search survives a corrupt index at the query seam** (F356). `_db()` healed the writer's
  connection, but a search opens its own reader — so a malformed image ("database disk image
  is malformed", "file is not a database") made every query raise until something happened to
  reopen the writer. The index is a pure cache of the flat files: a query that meets a corrupt
  one discards it, answers empty, and the next refresh rebuilds. A transient
  `OperationalError` (a lock) never discards anything.

### Removed
- **The Discord decision surface is gone, and with it every engine-implicit outbound send**
  (F284, D104; operator order 2026-08-26). `MIRROR_BLOCKING_QUESTIONS` had been False since
  D48 (2026-07-29) because a Discord-side answer was observed not reaching the waiting run
  (F193) — the machinery had been dead code for a month. Deleted: `engine/decisions.py`
  (the whole `DiscordMirror`), the mirror's arms in `engine/interact.py`, the daemon's
  OAuth-reauth ping and background-task-finished ping, and `rsched/notify.py` itself — with
  no implicit send left, the one-seam module had nothing to carry. The durable console
  record IS the notification; opt-in browser push (`web/push.py`) is the only
  away-from-console tier. A run reaches a *person* by calling a messenger util itself,
  gated and visible in the transcript, exactly as before.
- The `mirrored` flag on the blocking-question record and its "and on Discord" note in the
  answer form (`static/components/answerform.js`).
- **The retired clarify-wizard machinery, now producer-less** (F372, sweep-proven). Nothing
  has created a `.wizard-*` workspace or written `wizard_meta.json` since D59; every reader
  was dead. Deleted `web/wizard_store.py`, `api_questions._wizard_questions` and its three
  call sites, the `.wizard-` answer-routing and badge branches, `api_runs`'s workspace inbox
  redirect, and the `engine-run --run-dir` flag with `run_routine`'s `run_dir` parameter (one
  producer, `daemon/runner.py`, never passed it). The blocking-question dedup invariant its
  test covered is kept, re-pointed at an ordinary routine.
- 11 stray `export` keywords in `static/` whose symbols are used only in-module, and
  `executor.py`'s docstring no longer attributes `format_observation` to `composer` or omits
  the kinds it dispatches (hygiene sweep 2026-08-21, items 7 and 9 — F358).

## [0.229.0] — 2026-08-23

### Changed
- **The chat bubble's ⧉ copy button is mellowed** (operator order 2026-08-23: "visually too
  strong"). Hovering the bubble now reveals it at half strength (opacity .45, 11.5px) —
  full contrast only when the pointer is on the button itself or it holds keyboard focus.

### Added
- **Every fenced code block in a chat bubble carries its own copy button** (operator order
  2026-08-23). A quiet corner ⧉ inside the `<pre>` copies ONLY that fence's source text —
  copying one command out of a long reply beats copying the whole message and trimming.
  Same hover/focus reveal and ✓/✕ feedback as the bubble button (shared `copyBtn`).

## [0.228.0] — 2026-08-23

### Added
- **Conversations bind remote machines** (D102 = both halves, operator order 2026-08-23;
  R475/R496: heavy conversation work — a GPU batch render — had no way onto a catalog
  machine, and two conversations independently concluded machine access was
  routine-only). The conversation header's capabilities panel now mounts the same
  Machines card the routine page uses (new shared `static/components/machines.js`;
  `routine-config.js` swaps its inline panel for it), backed by `machines` +
  `machine_catalog` in the conversation detail and a validated `machines` PATCH — the
  next reply's boot injects `RSCHED_MACHINES`/`RSCHED_MACHINE_KEYS` exactly as a routine
  run's does. The in-run half already existed since 0.124.0 (`machine:<name>` access
  request → one-click allow → env injection, run-overlay included) but no surface ever
  NAMED it, so both R475/R496 conversations never tried: the request-field teaching copy
  (schema description + denial text) now carries a `machine:gpu-box` example, and
  docs/remote-machines.md names both conversation paths.


## [0.227.0] — 2026-08-23

### Added
- **Create and revise are separate permissions.** `util-authoring` now covers only CREATING a
  util; the new **`util-revision`** covers changing one that already exists. The model still
  emits a single `write_util` action — the engine resolves which act it is from whether the
  target name is already in the library, and refuses the half the routine does not hold. The
  action schema is unchanged: `revise_util` is a capability TOKEN (`CAPABILITY_ACTIONS`), not a
  26th kind, because the flat kind surface is what weak models and Ollama grammars handle well
  and the model cannot know its mode before it looks.
- **Verb-scoped util grants.** A `capabilities.utils` entry is either a bare name (every verb)
  or `name:verb` — that one subcommand, matched against the call's first positional argument.
  `signal:read` is read-only access to a channel the routine must not write to. Scoping is
  narrower than the doc that reserves the util, so it survives the floor; and a doc reserving
  only `signal:read` still gates the `signal` util by bare name, closing the fail-open path.

### Fixed
- **`remove_util`'s canonical source is `util-removal`, not `util-authoring`.** The
  `_DEFAULT_KIND_SOURCE` fallback still named the old fused doc after 0.226.0 split them, so
  the floor would have floated an explicit `remove_util` past for any `util-authoring` holder —
  making that split cosmetic.
- The `write_util` denial keeps the sub-workflow wording: a child is told the limit is the
  CHILD's scope and to route the work to its parent, rather than that the routine lacks the
  capability.

## [0.226.0] — 2026-08-23

### Changed
- **`util-authoring` no longer grants deletion.** Writing a util adds a capability nobody had;
  removing one takes a capability away from every routine that calls it. They were a single doc
  requiring `[write_util, remove_util]`, so every routine allowed to create a util was silently
  allowed to delete one. `util-authoring` now requires `[write_util]`; the new **`util-removal`**
  requires `[remove_util]`.
- **`personal-messaging` is replaced by one doc per channel** — `messaging-signal`,
  `messaging-telegram`, `messaging-whatsapp`, `messaging-zulip`. The bundle named all four
  messengers *and* the `chat`/`messaging` util_tags, which also swept in `discord` and `ntfy`, so
  a narrow "Signal only" grant could not be expressed at all. The tag wildcard is gone; each doc
  gates exactly its own util.
- **`problem-routing`** gains a clause: work the operator owns must be FILED as a decision, not
  narrated in a ledger, report body or run summary — nothing renders prose as awaiting an answer,
  so an item described there and never filed is indistinguishable from one never found.
- **`config-optimizer`** now judges a routine's cadence against its measured turn and wall-clock
  use across several runs, distinguishing "did not need the budget" from "was blocked", and
  addressing the group where a group is what schedules the routine.

### Fixed
- **Messenger session stores are bind-mounted.** `~/{telegram,signal,whatsapp}-sessions` lived
  only in the container's writable layer, so any recreate or image update silently unlinked all
  three accounts and dropped the WhatsApp message history — the failure the
  `conversations`/`background` binds already guard against.

## [0.225.0] — 2026-08-22

### Changed
- **Settings → Machines teaches the two-key distinction inline** (R474,
  `c-20260822-174836`: a user pasted a key into "host key (pinned)" and asked whether the
  same key also goes into Secrets — the form gave no way to know these are different keys).
  `KEY_VAR` now carries a teaching line — it names a **Secret** holding the private SSH
  **login** key (link to Settings → Secrets, example name, and the "no key_var" consequence
  spelled out). The host-key field is labelled "host key (pinned server identity)", its
  placeholder leads with the scan button, and the scan row states this is the SERVER's
  identity key, normally never typed, and never your login key.

## [0.224.0] — 2026-08-22

### Added
- **Copy-to-clipboard button on every conversation chat bubble** (operator order
  2026-08-22). User and assistant bubbles carry a quiet corner `⧉` button — visible on
  hover / keyboard focus — that copies the message's SOURCE text (the markdown the author
  wrote, not the rendered HTML; a user message copies the typed body without the ref line
  or the API-appended attachment block). Feedback: `✓` on success, `✕` when the browser
  denies clipboard access.

## [0.223.0] — 2026-08-22

### Fixed
- **A sent message with an attachment no longer renders twice in the conversation chat**
  (R473, `c-20260822-174836`). The F295 optimistic "✓ sent" echo is cleared when the real
  `user_injection` event arrives — but the comparison was raw-text equality, and the API
  DRESSES an attachment send (appends the `[attached files]` block), so the echo never
  matched and sat beside the real attachment card forever. `chat.js` now exports
  `typedBody()` (ref line + attachment block stripped — the same normalization its
  `lastUser` tracking already used) and the echo-clear compares typed bodies on both sides.

## [0.222.0] — 2026-08-22

### Fixed
- **`create_routine`'s draft/preview observation no longer reads as success.** The D92
  two-step flow's FIRST call stores a draft — but the observation renderer had no `draft`
  branch, so the preview fell through to the created-copy and told the agent "created
  routine … tell the user it exists" (R476/R477/R478, conversation `c-20260822-174836`:
  the user was told the routine existed, watched the dashboard show nothing, restarted the
  daemon and re-called). The draft obs now renders "DRAFT — NOTHING CREATED YET" with the
  drafted name/workflow/instruction preview and the exact confirm step; the created obs
  names the actual registry-rescan cadence (`~Ns`) instead of "shortly".
- **Materialization can no longer crash the conversation run or leave a half-made routine
  dir.** `workflows.scaffold` created the dir skeleton BEFORE the slow (minutes-long) LLM
  decompose, so the routines home showed empty `inbox/ stages/ state/` dirs mid-build —
  which read as a broken build; when the user deleted them mid-flight, the writes that
  followed raised `FileNotFoundError`, uncaught, orphaning the whole conversation run rc=1.
  Scaffold now decomposes FIRST and creates the dir only when every file's content is in
  hand, and the `create_routine` handler catches `OSError` into a teaching error
  observation ("materialization failed mid-build … try again").

## [0.221.0] — 2026-08-22

### Changed
- **Refusal detection catches the "I'm not going to do this" decline-with-redirect shape.**
  Live specimen `c-20260822-123621` (darknet 4-MMC): opus declined with "I'm not going to
  do this one." then pivoted to harm-reduction alternatives and a question back. 0.220.0's
  detection ran correctly on the tool_call model — but the classifier rated that
  decline-with-redirect `refusal:false`, so `referrals=0` and nothing was delivered. The
  miss was intermittent (the same prompt+reply returned `refusal:true` on repeated repro),
  i.e. a probabilistic verdict at a wide margin. Two fixes: (1) the "I'm not going to
  do/help/assist", "I won't do/help/assist with" opener family is now a zero-cost
  `REFUSAL_MARKERS` fast-path entry — this exact decline opener is confirmed
  deterministically, no subcall. (2) The classify prompt and `CLASSIFY_SCHEMA` are
  sharpened: a decline of the task AS ASKED is a refusal EVEN IF the model then explains,
  offers 'safer' alternatives, redirects (e.g. to harm reduction), or ends by asking
  whether you want something else — defense-in-depth for the general shape the schema
  previously let read as "a question back". New test
  `test_not_going_to_opener_confirmed_by_fast_path` pins the specimen. Full suite 1718
  passed, 4 skipped.

## [0.220.0] — 2026-08-22

### Changed
- **Refusal detection is the harness's own job again — the uncensored (honeypot) model no
  longer judges whether a reply is a refusal, it only receives the essence.** Operator:
  "if the conversation harness already figured out that it's a refusal then why does the
  honeypot model's opinion matter at all? why do we even ask for it? forward the essence
  of the refusal trigger to the honeypot model as soon as the refusal has been
  determined." `refusal.is_refusal`'s classification subcall now resolves the **tool_call**
  model (it always resolves), not `for_uncensored` — a leftover from the 2026-08-22 leg-6
  spec that let the honeypot veto its own delivery. Two live specimens
  (`c-20260822-112420`, `c-20260822-114953`, both the darknet 4-MMC task) showed exactly
  that failure: opus clearly declined, the gemma honeypot rated the decline `refusal:false`,
  so nothing was ever delivered (`referrals=0`, no refusal event). With detection on the
  harness, a determined refusal now goes straight to isolate → deliver-essence-to-honeypot.
  Delivery target, isolation, and the essence-only guarantee are unchanged. Consequence:
  every free-text reply and finish summary now costs one classify subcall regardless of
  whether an uncensored role is configured (previously inert when unset).

## [0.219.0] — 2026-08-22

### Changed
- **The conversation UI calls the refusal-referral role "uncensored", not "honeypot"**
  (operator: "you cannot call the model 'honeypot' in the frontend. just call it
  'uncensored'. to the frontend it's the same model role like in the routine model
  settings"). The new-conversation composer and the conversation header's model line now
  label the picker `uncensored` and use "none · uncensored off" / "uncensored model —
  where refused requests are delivered", matching the routine config page's existing
  `uncensored` role label. The `models.uncensored` key, the API, and the engine's internal
  "honeypot harness" concept are unchanged — this is a copy fix so one role has one name
  across every surface the user sees. The composer's Model help text also dropped the stale
  "can only be set here" claim for this role (it became live-switchable in 0.218.0); only
  the tool-call role is create-only now.

## [0.218.0] — 2026-08-22

### Added
- **The honeypot (uncensored) role is switchable mid-conversation.** The conversation
  header's model line gains a honeypot picker beside main; `POST /runs/{id}/model` accepts
  `kind: uncensored` and the turn-boundary apply (`apply_model_switch`) honours it. The
  endpoint now MERGES per-role into any pending switch — two quick per-role POSTs used to
  race, the second wiping the first's control signal before the engine drained it. The
  switcher PATCHes the FULL role map so switching one role never drops the other. The
  honeypot is an ordinary model to the engine — a plain completion call; the refusal
  machinery only fires when the role is set, so it must be reachable mid-conversation,
  not only at create time (completes 0.217.0).

### Fixed
- **A failed LLM history-archival no longer shows a red `error (compaction)` card (F376).**
  The deterministic digest taking the pass is a DESIGNED degrade: the reason now rides the
  neutral `compaction` event as `archival_degraded` (rendered inline on the compaction
  line), never as an error event. And the archival timeout scales with the middle being
  read — 180s base + 60s per 200k chars, capped at the endpoint default (600s); the fixed
  180s died on a 1.25M-char middle every single time while the operator saw a scary card.

## [0.217.0] — 2026-08-22

### Fixed
- **The honeypot (uncensored) model role can now be set BEFORE a conversation starts.**
  Root cause of a live gap: `POST /api/conversations` accepted only a single `model`
  param and seeded it into `main` + `tool_call` — there was no way to configure the
  `uncensored` role at create time, and the live model switcher only ever set main +
  tool_call too. So a conversation always started with no honeypot, and the refusal
  machinery (0.213.0–0.216.0) — whose detection classifier and essence-delivery both
  resolve `for_uncensored` — silently degraded to "unconfirmed = answer" and let
  refusal-worded finishes through (live specimens `c-20260822-085029`, `-091412`,
  `-100653`). Now the create endpoint also accepts a per-role `models` JSON map
  (`{main, tool_call, uncensored}`), validated against the catalog + window exactly like
  the PATCH path (R112/R128), and the new-conversation composer exposes three role
  pickers (main / tool-call / **honeypot (uncensored)**). The single-`model` shorthand
  still works for the common case. Extracted `_resolve_create_models` to keep the create
  handler under the complexity cap.

## [0.216.0] — 2026-08-22

### Fixed
- **A refusal wrapped in `finish(status=ok)` (or `partial`) is now caught too (live
  specimen `c-20260822-091412`).** 0.215.0's `_intercept_refusal_finish` was gated on
  `status == "failed"`, so a model that declined via a *successful*-looking finish
  ("I'm not going to do this one") sailed straight through — and the run was even logged
  as a SUCCESS with `referrals: 0` and no `refusal` event. The interception now judges
  the finish summary through the refusal classifier **regardless of status** (ok /
  partial / failed); an honest completion or failure report still fails the classifier
  and is accepted unchanged. Same downstream handling as 0.215.0: flag → isolate essence
  → deliver to the honeypot → re-drive the turn on the main model with the remainder.

## [0.215.0] — 2026-08-22

### Fixed
- **A refusal that arrives as a `finish(status=failed)` is now caught (live specimen
  `c-20260822-085029`).** The refusal-clarification pipeline (0.213/0.214) watched only
  free-text replies and provider classifier stops — but a loop refusal's most natural
  shape is a SCHEMA-VALID `finish` whose summary is the decline prose ("Declined — will
  not fulfill…"). That action parsed cleanly, so it was accepted as the turn's action
  and the run just ended `failed` with `referrals: 0` and no `refusal` event — the whole
  machinery stayed dark. `next_action` now judges a failed-finish summary too
  (`_intercept_refusal_finish`): a content refusal is flagged, its essence isolated and
  delivered to the honeypot, and the turn is re-driven on the main model with the essence
  named as handled-separately, so it proceeds with the remainder instead of the run
  terminating on the refusal. Latched (one interception per turn) so a genuinely
  refusal-only task still lands its failed finish honestly; an HONEST failure report
  (non-refusal summary) is accepted unchanged.

## [0.214.0] — 2026-08-22

### Changed
- **Refusal clarification: the essence/remainder split (operator follow-up).** The
  uncensored honeypot is now treated as a completely NORMAL model — no exceptions, no
  special call framing, no test markers (the environment must be authentic; the operator
  manages the dummy responses in the background) — and it receives ONLY the isolated
  essence of the refusal trigger, as the entire user message. Everything ELSE is
  processed by the MAIN model without refusal danger: the `llm` seam re-issues the
  prompt with the essence factored out (`[this part is handled separately]`) and that
  answer serves the observation (`remainder_processed: true`, the refusal record rides
  beside it); a loop turn's schema-retry message now names the flagged essence and tells
  the model to proceed with the remainder. Isolation failing still sends nothing — more
  than the essence never reaches the honeypot.

## [0.213.0] — 2026-08-22

### Changed
- **Refusal clarification replaces whole-turn uncensored referral (operator order).** The
  `uncensored` model role is now explicitly a **honeypot harness**: it only acts as if it
  complies, so the catching machinery can be exercised before any actually-uncensored
  model is in the loop — nothing it produces is executed, returned as an answer, or
  allowed to become a turn's action. New `engine/refusal.py` runs the process at both
  seams (the `llm` action and the agent turn loop): every refusal is **flagged** as a
  first-class `refusal` transcript event (new `EVENT_TYPES` member, rendered by the
  transcript UI with the isolated fragment and the harness reply in a fold marked
  diagnostic), its **trigger isolated** by a schema'd tool_call subcall (one STEP of the
  task's action sequence, or a recurring WORD/PHRASE), and **only the isolated fragment
  referred** to the harness — isolation failing means nothing is referred. The refused
  call then continues on its normal path: an `llm` observation keeps the original refusal
  as its reply with the record beside it; a loop turn takes the ordinary
  schema-retry/failover path. `refer_turn_to_uncensored` and the `referred` stamp on
  `assistant_action` events are retired.
- **Free-text refusal detection is no longer a marker list (operator: regex is not
  reliable).** An LLM classification subcall (tool_call model, schema'd verdict) decides;
  the legacy markers survive only as a zero-cost fast path that can CONFIRM an obvious
  opener, never deny. Provider classifier stops (`refusal`/`content_filter`) remain
  authoritative.

## [0.212.0] — 2026-08-22

### Changed
- **The per-routine `subroutine` model role is retired (user order, 2026-08-22).**
  Spawned/subtask children now run the routine's **MAIN** model by default — a child
  continues the same routine's work, so it runs on the same model unless the call says
  otherwise. The `model` field on `llm`/`spawn`/`subtask` takes a ROLE (`main`,
  `tool_call`, `uncensored`) **or a catalog model NAME**; an unknown value is a teaching
  rejection naming the real catalog, validated before any child is built (spawn now
  pre-validates too — it never did). `MODEL_KINDS` shrinks to main/tool_call/uncensored
  across config, the mid-run model switch, the web APIs, and the routine/conversation
  editors; a leftover `models.subroutine` yaml key still loads and surfaces as an
  advisory config problem telling the user to remove it.

### Added
- **`list_models` action** — read-only model discovery, available on EVERY turn (it
  joins ALWAYS_KINDS: the override is only usable where the run can see the catalog):
  the run's resolved role bindings plus every catalog model a `model` override may name
  (endpoint, provider id, multimodal, context, effort, fallbacks) — a catalog row that
  fails to resolve surfaces as its own error line instead of vanishing.

## [0.211.0] — 2026-08-22

### Added
- **Message attachments render inline in transcripts.** An injected user message's
  attached files used to appear only as the bare filename list inside the text block —
  the image itself was nowhere to be seen (user report, 2026-08-22). The engine now
  stamps the attachment rels onto the `user_injection` transcript event (all of them,
  not just the media the model can view), and every transcript mount renders them:
  images as click-to-open thumbnails loaded through the authenticated blob route (a
  bare `<img src>` cannot carry the Authorization header — the artifact panel's
  pattern), other files as fetch-and-open chips. Wired in the run view (main + sub
  transcripts + nested subrun expansion), the conversation chat (user bubbles + the
  work fold), and the Dashboard activity feed; a mount without a file route falls back
  to the text block's plain list.

### Fixed
- **Schema-error cards now show what the model actually tried.** The engine has always
  persisted the rejected reply into every schema/transport `error` event (`payload.raw`,
  capped at 1500 chars), but the transcript renderer printed only the rejection message —
  the reader saw *why* an attempt failed, never *what* was attempted (user report,
  2026-08-22). The error card now folds an `attempted reply` details block underneath
  (same pattern as a turn's `action json` fold) and names the serving provider when the
  event recorded one.

## [0.210.0] — 2026-08-21

### Added
- **Recipe length on the Stats tab (F371, user order).** New `recipes` slice in
  `/api/stats` (`readmodels/recipe_size.py`: current main.md + stages/ + tuning.yaml
  bytes per routine, plus the recipe's size at the last commit ≥30 days old from the
  routine dir's own git history — two git calls per routine) and a "Recipe length by
  routine" section beside the token charts: one violet bar per routine (deliberately not
  a usage-series color — instruction mass, not spend) with a growing/steady/shrinking
  trend chip against the 30-day baseline.

### Fixed
- **Claude-quota windows cut in UTC (F357).** `claude_usage` read run-ts dir names as
  local-naive and compared them to a local `now`, shifting the 5h/7d subscription
  windows by the host's UTC offset (~2h on Berlin); run-ts is ALWAYS UTC (ids.run_ts).
  Now parsed by the one reader (`registry.parse_run_ts`) against an aware-UTC now;
  `stats._run_day`'s docstring stops claiming run-ts is server-local (by_day buckets
  are UTC days).
- **Transcript turn briefs for 11 kinds (hygiene sweep).** `transcript.js` BRIEF_FIELD
  had drifted 10 kinds behind `actions.py` — subtask/report/view_image/manage_group/…
  turns rendered with empty one-line briefs; the maps are synced (`script` added on the
  Python side too) and a new test parses the JS literal to hold them in lockstep.
- **Library section says "Rules" again** — the retired "Traits" title + per-copy
  description (traits retired 0.164) survived in `library.js`; the 0.174 vocabulary
  sweep missed it.

## [0.209.0] — 2026-08-21

### Added
- **Guided create_routine: preview → confirm (D92=A).** The action is now TWO-step: the
  first call stores a draft (`state/routine-draft.json` in the conversation dir) and
  returns a preview observation — nothing is materialized; the agent relays the draft and
  finishes its reply. A follow-up call with the SAME fields from a LATER reply (the
  confirm gate is structural: a different engine process means the user has spoken since
  the preview) materializes through the same scaffold path. Changed fields update the
  draft and restart the round-trip; a same-reply confirm attempt is held with teaching
  copy. The kind's surfaced prose states the flow.
- **SIGKILLed runs auto-resume once (D99=A).** A run the kernel killed (rc=-9, no
  authored finish, not a user abort) gets ONE automatic in-place resume from the reap: a
  `sigkill-retry.json` marker in the run dir caps the retry (a second kill leaves the run
  failed), and a recovery note filed `via=background` — the channel a resumed leg's boot
  drains — tells the resumed leg what happened and to finish honestly. A user-cancel
  SIGKILL (F188 attribution) never retries.
- **rc=-9 post-mortems see memory now (F348).** Every engine status write samples
  `/proc/self/status` VmHWM into `status.json` (`vm_hwm_kb`), and the daemon's
  died-without-finish close-out names the last sampled peak in the failure summary and
  health event — a peak near host RAM is the kernel-OOM signature that R437's specimen
  (opus, 154k context, 3.4 GB host) left invisible.

## [0.208.0] — 2026-08-21

### Added
- **Semantic stopping conditions v1 (D98=A, F334 — engine + API).** A conversation carries
  user-owned, meaning-level bounds ("stop once the PDF is verified", "only diagnose") in
  `state/stopping.json` (`engine/stopping.py`): the state digest inlines every open
  condition with the accounting contract, and the finish gate rejects a depth-0 finish
  whose summary lacks a `[s<n>] met/unmet — why` line per open condition (the R108
  one-extra-turn deferral shape; the reserved-finish turn is exempt). The engine checks
  only the ACCOUNTING — semantics stay the model's; v2 (a verifier subcall) remains a
  separate decision. `GET/PUT /api/conversations/{slug}/stopping` is the user surface
  (stable ids, whole-list replace); the sidebar panel ships with F324's shared component.
  Deviation from the design note: no `stopping_update` transcript event — v1 has no
  engine-side status transitions to record, and the store file is replayable state.

## [0.207.0] — 2026-08-21

Self-audit batch: four user decisions executed + one user order + two defect fixes.

### Changed
- **Inbox consumption timing (user order 2026-08-20, F368).** A run's turn boundaries now
  deliver only the LIVE message set — the live run view's channels (`web`, `web-converse`,
  the conversation composer) plus a detached background task's result. Queued freight
  (reports, audit feedback, routine-page messages, trigger texts) is consumed only at the
  START of the routine's next fresh run, never mid-flight. `inbox.drain_messages` /
  `has_pending_messages` take a `vias` allow-set instead of the `user_only` flag.
- **Run history 'last' depth is always on (D96, F364).** Every routine reads its own last
  run under `runs/` with no permission — baseline observability, like the state digest.
  The run-history permission now governs only the `all` depth; sub-workflow children stay
  at `none` (their brief is their context). Denial copy rewritten to match.
- **Forever-grant blast radius (D97=B, F360).** `allow_forever util:X` from a decision card
  activates the covering conduct doc but floors capabilities to the NAMED util only —
  sibling utils and tag classes stay off, each requestable separately (one `util:signal`
  click used to hand a routine all four personal messengers — sprind `30e1894`, umr
  `df2b944`). The permissions editor's full save keeps the class raise. The
  `allow_forever` decision phrase states the narrow apply.
- **Once-grant attribution (D93=A, F350).** The ONCE-GRANT SPENT notice names the consuming
  action, and when an fs once-grant is burned by a util call it teaches the D76 coarseness
  (any util invocation receives mounted roots — do file work first). The `allow_once`
  decision phrase carries the same warning at deciding time.

### Fixed
- **Detached results stranded after F359 (F367).** A resumed conversation leg's boot now
  drains `via: background` messages (the LIVE set) — before this, the daemon's delivery
  wake resumed the idle owner in a loop while the filtered leg never consumed the result.
- **Script header declarations in the wrong block fail loudly (F369, R444/R419).** A
  routine script declaring `secrets = [...]` / `net = "outbound"` inside the PEP 723
  `# /// script` block (where the engine never reads them) is refused with copy teaching
  the docstring header form — before this it ran with no secrets and no network and
  failed obscurely at the first env read (sprind's publish helper lost FTP_SOURCES and
  HTTPS to exactly this).

## [0.206.1] — 2026-08-19

### Fixed
- **Child dead-end cascade copy** (F351, R404): a sub-workflow's reserved-capability
  denial no longer hints `ask_user with request:` — a child cannot file access requests,
  and following that hint burned its strikes and ended in a false verdict. The denial now
  says to name the need in the finish summary; the forced-finish copy itself now
  distinguishes schema-invalid output (a model problem, D87) from repeated policy
  denials (a boxed-in run no model change fixes).
- **Spawn contract copy told the wrong story** (F352, R405/R406): children do NOT share
  the parent's working directory — they run isolated in `runs/<ts>/sub/<n>/`. The action
  surface and `docs/prompt-anatomy.md` now say so and tell the parent to pass absolute
  paths and fold results back from the finish summary.
- **Dead two-pass residue in the groups API** (F361, R414): the in-flight chain payload
  dropped the retired `phase` field (no consumer; split machinery removed in 0.205.0)
  and the stale F292 comment.

## [0.206.0] — 2026-08-19

### Changed
- **A resumed run leg no longer drains the whole inbox** (F359, user order 2026-08-17).
  A follow-up/recovery leg now consumes ONLY what is addressed to *it*: messages the user
  sent through the conversation/run-page channels (`via` in `USER_MESSAGE_VIAS`) and
  answers to the run's OWN questions (qid prefix match). Audit feedback, report
  deliveries, routine-page queued messages and answers to other runs' questions stay
  queued for the next fresh run, whose boot digest presents them with full context —
  previously a follow-up leg of an already-ended run ate decision answers meant for the
  night's scheduled run (the D92/D93 loss). The user-channel vocabulary now lives once, in
  `engine/inbox.py`; the daemon's post-finish wake sweep imports it.

## [0.205.0] — 2026-08-16

### Removed
- **The two-phase `split` flag is retired** (D90 option A, user-selected 2026-08-16; F354,
  R359): a group chain now fires ONCE, member by member in order. A flow with an inbound
  and an outbound end brackets the group instead — a dedicated inbound-router routine
  first in the order, a dedicated outbound-sender routine last. Gone end to end: the
  member records' `split` field (store, API, manage_group action + schema), the
  ingest/outbound pass flip in the daemon chain manager, the `phase` boot.json channel
  (Runner.fire, run_routine, RunContext.group_phase, harness-contract prose,
  `run-once --phase`), and the group modal's split checkbox + badges the user reported
  still lingering. No installed group used the flag; stale stored keys are dropped by the
  normalizer.

## [0.204.0] — 2026-08-16

### Changed
- **Message-field keys flipped** (F353, user order 2026-08-16): in EVERY message input —
  conversation composer, run composer, answer forms and the Decisions page — plain **Enter
  now inserts a newline** and **Shift+Enter sends**. This reverses the 0.202.0 keys on the
  user's explicit order; single-line `input` controls (where a newline is impossible) keep
  Enter-sends. All "(Shift+Enter for a new line)" hint copy now reads "(Shift+Enter sends)".

## [0.203.0] — 2026-08-15

### Added
- **Weekly schedules repeat on a SET of days** (F347, user order 2026-08-15 — "do it like
  Google Calendar does custom repetitions"): the schedule editor's weekly mode is seven
  day toggles instead of one weekday select, so "every weekday" or "Mon/Wed/Fri" is a few
  clicks — for routines AND groups (both use the same editor). The friendly vocabulary's
  weekly shape is now `weekdays: [0-6, …]`; cron carries it as a day-of-week list
  (`0 10 * * 1,2,3,4,5`), hand-written ranges (`1-5`) read back as the same set, and
  `describe` says "Every weekday at 10:00". Dragging one occurrence of a multi-day
  schedule in the week strip keeps its day set and moves only the time.

## [0.202.0] — 2026-08-15

### Changed
- **Message fields are always multi-line, and stand alone on narrow screens** (F346,
  user order 2026-08-15): the run view's composer and the answer form's free-text field
  (Decisions page included) are textareas everywhere — Enter sends, Shift+Enter breaks
  the line — and on a ≤860px viewport every message field takes its own full-width row
  with the buttons wrapping beneath it (the F238 media rule now covers textareas; the
  answer field's flex moved off the element so the stylesheet rule can win).
- **The routine page's Runs table is capped at the 10 newest runs** (F345, user order
  2026-08-15): the full history (keep_runs can be 30+) made Runs the tallest element on
  the page; it now opens fully only on an explicit "show all N runs" click, and the
  expanded state survives the live re-render on run_finished.

## [0.201.0] — 2026-08-15

### Added
- **Artifact rows show their update time and are deletable from the sidebar** (F336,
  user order 2026-08-14): the artifact list (runs, routines and conversations alike)
  renders a visible "last updated" relative time per row — an artifact is rewritten in
  place across turns, so the version must not hide in a tooltip — and a hover × deletes
  the file after a confirm, via new `DELETE /api/routines/{slug}/artifacts` and
  `DELETE /api/conversations/{slug}/artifacts` endpoints with the same resolved-path
  containment as serving (artifacts/ only; attachments and config can never be deleted).

## [0.200.2] — 2026-08-15

### Fixed
- **Finish-guard no longer flags the noun "report"** (F332, R360): a routine whose
  DELIVERABLE is a PDF/LaTeX report could not write an honest finish summary — the
  unbacked-claim check matched document-shipping verbs (sent/send/open) near the word
  "report", and the token matched inside "reports/" paths. The claim token is now
  word-bounded, the report verb list is filing verbs only, and a match beside document
  vocabulary (PDF, LaTeX, reports/, .md/.tex) is exempt. The F127 positive case
  ("Filed report to self-audit") stays rejected.

## [0.200.1] — 2026-08-15

### Fixed
- **`util` miss now names a matching routine-local script** (F330, R367): calling the
  `util` action with the name of a `scripts/` helper used to dead-end on the global
  catalog ("does not exist. Available: ...") with no path to actually running the file
  the guidance told the routine to write. The miss observation now says the name exists
  as a ROUTINE-LOCAL script, shows the `script` action call, and names the
  `action:script` grant to request when the kind is absent from the run's schema.
- **Ungated action kinds get a truthful request denial** (F331): requesting
  `action:create_routine` was rejected with the generic "not a grant-entity id — class
  one of action, ..." copy, which lists `action` as valid and so reads as
  self-contradictory (routine-improver retried against it). A real-but-ungateable action
  kind now gets its own copy naming the requestable kinds and the report/ask_user route.


## [0.200.0] — 2026-08-14

### Changed
- **Library filter is one autosuggest input (user order).** The filter bar rendered every
  tag in the library as a chip — a wall that outgrew the page. Now: active tags as
  removable chips + a single search input whose datalist suggests the not-yet-active
  tags; committing a suggestion narrows the sections, free text that matches no tag never
  filters. URL/deep-link behaviour (`?tags=…`) is unchanged.

## [0.199.0] — 2026-08-14

### Added
- **Drag-to-reorder inside an expanded group (user order).** In the routines table, an
  expanded group's member rows are its fire order — dragging a row onto a sibling now
  reorders the group in place (drop above the target's midline lands before it, below
  lands after), persisting through the same `PATCH /api/groups/{id}` the editor uses; the
  target row marks the landing slot. The overlay editor's ↑/↓ stays for precision.

## [0.198.3] — 2026-08-13

### Fixed
- **Out-of-band library edits get committed at boot (R332/R335).** Every managed write path
  commits what it writes, but a conversation editing library files through a filesystem
  grant (or the user in an editor) has no committing writer — on 2026-08-13 the live
  library accumulated six loose rule/permission files across one working day, invisible to
  the history the repo exists to keep. `adopt_library_edits` now runs at every daemon boot
  after the seed syncs and commits whatever the repo is carrying, verbatim; the linter
  still reports nonconforming content on its own channel.

## [0.198.2] — 2026-08-13

### Fixed
- **Snake_case script names are callable (R336/R337).** `scripts.exists` required a
  kebab-case slug while `list_scripts` advertised every `scripts/*.py` stem, so a run that
  authored `scripts/gen_random_strings.py` got *"does not exist. Available:
  gen_random_strings"* — and the miss message told it to write that very filename again, an
  infinite loop two conversations actually ran. Script names now accept lowercase
  letters/digits with `-` or `_` (dots and path separators stay rejected — the name is
  interpolated into a path), and the miss message states the rule.

## [0.198.1] — 2026-08-13

### Added
- **UI coverage for the group editor's Shared config section (D82)** — it had shipped without
  one, which the house rule requires for every UI change. Drives the real panel: the section
  renders and expands, all six blocks mount (permissions & capabilities, general rules,
  secrets, connections, both fs-root editors), and a save lands in `.control/groups.json`.

## [0.198.0] — 2026-08-13

### Added
- **`personal-messaging` permission** — `signal`, `telegram`, `whatsapp`, `zulip` plus the
  `chat`/`messaging` tag gate, so a new util in that class is fail-closed.

### Changed
- **`communication` is Discord-only again.** 0.196.0 folded every chat channel into it, which
  meant a routine holding it for Discord silently GAINED the personal messengers the next time
  its permissions were saved (`self-audit` did, and was reverted). A Discord ping waits in a
  room the user chooses to visit; a message on a personal messenger arrives on their phone next
  to messages from their family. Different act, different permission — holding one never grants
  the other. The tag gate that makes new utils fail-closed moved to `personal-messaging`, so
  0.196.0's guarantee is kept without the widening.

## [0.197.2] — 2026-08-13

### Fixed
- **A group member recorded capability dials it never chose, shadowing its group.** The
  raise/floor pair cannot express "unset" — it emits a concrete value for every dial — so
  saving a member wrote `runs: none` / `workflows: catalog` even when the user had not touched
  them, and because a member's own key always wins that copy shadowed the group permanently:
  no later group change could reach the routine. The save path now drops a dial the group
  supplies when the client either omitted it or sent exactly the group's value, keyed off what
  was actually SUBMITTED (the only signal separating "turned it off" from "never touched it").
  List members are untouched — they union with the group's, so a redundant entry there cannot
  shadow anything and keeping it preserves what the user ticked. Completes the 0.197.1 fix,
  which addressed the floor but not the default.

## [0.197.1] — 2026-08-13

### Fixed
- **Saving a group member's permissions floored away every capability the group supplied.**
  The two-layer floor ran against the routine's OWN `permissions:` only, so once the shared
  half moved up to the group (D82), saving a member dropped `runs` to `none` and `workflows`
  to `catalog` — and because the floor writes the mapping explicitly, that "off" then SHADOWED
  the group's value, since a member's own key always wins. Group permissions now count for the
  FLOOR (they still RAISE nothing, so holding one never silently adds a capability to a
  member's own file). Found migrating the FAU group; all four members were affected.

## [0.197.0] — 2026-08-13

### Added
- **Group-level routine config, inherited by members (D82).** A group now carries a `config:`
  block — the routine.yaml keys its members share: `permissions`, `capabilities`, `rules`,
  `machines`, `tags`, `models`, `connections`, `grants`, `budgets` and both fs-root lists.
  Related routines have a common policy surface, and keeping N copies of it in step is how they
  drift apart; the group holds one copy. The group is a **default, never an override**: list
  keys UNION with the member's own (the group is a floor a member adds to), mapping keys merge
  per key with the member's value winning, and `capabilities` does both — its lists union, its
  dials (`confirm`/`rule_confirm`/`runs`/`workflows`) take the member's value when it sets one.
  What may NOT be shared is fixed just as deliberately: slug/name/description/enabled/schedule/
  workflow/retention/triggers/improve say WHICH routine this is and when it runs.

  The merge happens in `config.routine.apply_group_config` against the RAW routine.yaml
  **before validation**, which is what makes "the member set it" mean *the key is present in its
  file* rather than *the model has a default* — every field here has a non-empty default
  (budgets especially), so a post-validation merge could never tell the two apart and the
  group's value would be silently shadowed. Nothing is written back to routine.yaml, so removing
  a routine from a group returns it to exactly what its own file says.

  Edited in the group editor on the Routines page (`static/components/groupconfig.js`), which
  mounts the ROUTINE page's own permissions/rules/roots/connections controls rather than
  lookalikes that would drift from them. `load_routine` records `inherited`/`inherited_from`,
  and the routine page banners which of its settings came from the group — an inherited value
  must never read as one set on that routine.

## [0.196.0] — 2026-08-13

### Added
- **Util capability gating by TAG CLASS (`util_tags:`).** A permission doc's `requires:` and a
  routine's `capabilities:` both gained `util_tags:` beside `utils:`. `utils:` names one util;
  `util_tags:` switches on a whole class — every util whose docstring `tags:` line carries one
  of them, **including utils the library gains later**. Every util must already declare at
  least one tag (`utils_lib.header_problems`), so nothing can slip past by omission. The
  catalog is read at policy load only when some doc declares `util_tags`, so a library with no
  tag gate produces a byte-identical policy and touches no catalog.

### Fixed
- **Util gating was fail-open, and the util surface had outgrown its gate.** Only utils named
  in a permission doc's `requires.utils` were ever gated — 6 of 114 on the live instance
  (`shell`, `remote`, `discord`, `darknet`, `usenet`, `usenet-nzb`), leaving 108 callable by
  any routine holding `global-utils`, which is all of them. 64 of those need no secret either,
  so the secret grant was no second gate. `communication` gated Discord but not `signal`,
  `whatsapp`, `telegram` or `zulip`; `fau-mail-send` was ungated entirely. `communication` now
  covers every chat channel (`utils:` + `util_tags: [chat, messaging]`) and a new
  **`outbound-mail`** permission covers sending email (`utils: [fau-mail-send]` +
  `util_tags: [smtp]`). Reading a mailbox stays ungated — the line is drawn at transmitting in
  the user's name. A gated-but-ungranted util is not a hard break: it denies with the existing
  access-request route, which the Decisions page settles.

## [0.195.2] — 2026-08-13

### Changed
- **libgit holdouts folded in (F318, completing F285).** The six remaining hand-rolled git
  call sites now go through `libgit.git`: `engine/autocommit.py` (was a verbatim
  re-implementation of `libgit.commit`, identity strings included — now a thin wrapper),
  `workflows/library.head_commit`, `readmodels/util_stats` (utils log), `web/app.build_stamp`,
  `web/settings/common.remote_of`, and `web/settings/source` (branch probe, remote
  set-url/add). `libgit.commit` now carries the neutral-identity `-c` flags itself, so a
  repo that never persisted git config (a routine dir) still commits as
  `routine-scheduler` — new regression test. Deliberate exceptions kept raw: the source
  push (60s timeout) and the `ls-remote` reachability probe (custom env), plus
  `utils_lib`'s clone. Also: a stale "Groups page" docstring reference updated.

## [0.195.1] — 2026-08-13

### Removed
- **Expired migration retired (F315).** The one-shot util-header migration for the sandbox
  rollout (`bootstrap.migrate_util_headers` + `_with_header_line`, its boot call in
  `cli.py`, and its coverage) is deleted at its declared 2026-08-17 expiry: the production
  library converged weeks ago (the pass has returned 0 on every boot since), and an expired
  MIGRATION marker is scheduled cruft by the repo's own rule.

## [0.195.0] — 2026-08-13

### Added
- **Group-chain health events (F316).** The silent starvation mode is instrumented: a
  sequential group chain now writes `group_chain_done` / `group_chain_stopped` to the
  health-event stream when it ends (routine = the group id, run_id = the chain record id,
  detail = member-run and not-ok counts) — since the in-flight file is consumed at that
  moment, this is the chain's durable record, and a scheduled group's periodic `done`
  event doubles as a heartbeat whose absence means the group starved. A missing/disabled
  member emits `group_chain_member_skipped`, and a due scheduled group fire refused
  because the previous chain is still in flight emits `group_fire_refused` (the group
  analog of `fire_refused`) — the wedged-chain mode that starved five maintenance
  routines for a week in August with only `log.info` lines as witness.

## [0.194.0] — 2026-08-13

### Fixed
- **Compaction events render what actually happened** (F309, user report on
  c-20260810-213335): the transcript's compaction line knew only the flat
  before/after shape, so window clamps and window-guard corrections printed
  "context compacted: undefined → undefined chars" after every message. Each payload
  shape now gets its own copy (archive → history/, digest, clamp, window correction).
- **A weak archival model no longer yields a bare "Expecting value" error**: when the
  compaction summarizer returns prose or nothing instead of the schema,
  `compact_to_history` raises a teaching error naming the model and the reply head;
  the deterministic fallback takes the pass as designed.

### Changed
- **Routines overview: the tag-chip filter row is retired** (user order): the tags ate
  a row of space; the search field still matches tags. The filter bar (search, sort,
  state chips, view toggle) is built once — the F229 focus guard holds.
- **Messages page: "Message the next run" is retired** (user order): it duplicated the
  generic Messages channel on self-audit's own routine page; queued messages remain
  editable and withdrawable on the waiting list.

### Added
- **Group membership from the routine detail page** (user order): the routine hero
  gains a "group" select — join a scheduling group (leaving the previous one) or
  "none", via the same groups PATCH the Routines page uses; the sub-line says whether
  the group chain or the routine's own cron drives it. The week panel's drag
  join/leave (0.185.0) covers the same order's other half.

## [0.193.0] — 2026-08-13

### Added
- **Every row of the run/conversation rail's files card is viewable and downloadable**
  (user order, twice-asked; F311, absorbs D83/R238): new `GET /api/runs/{run_id}/file`
  serves one file raw — scoped to the run dir and its owning routine/conversation dir,
  resolved-path guarded; rows outside that scope (fs-root grants) say so inline instead
  of opening a dead tab. The card gains per-row ⧉ open / ⭳ download buttons (auth'd
  blob fetch, the artifact panels' pattern) in BOTH the run view and the conversation
  rail — one shared component.
- **Compacted conversation history is browsable** (user order; F310): `GET
  /api/runs/{run_id}/files` now also lists `history/` — the on-disk archive
  `compact_to_history` writes — and the files card shows those under a "compacted
  history" sub-head, servable like any row. What the model no longer carries verbatim,
  the operator can now read.

## [0.192.1] — 2026-08-13

### Fixed
- **Script children no longer see the util library** (F308): `run_script` reused
  `utils_lib.scoped_env`, which serves util calls too and therefore kept
  `GLOBAL_UTILS_HOME` — breaking 0.191.0's "a script is pure code, not a tool-user"
  isolation contract on any host where the daemon env carries the handle
  (`tests/test_scripts.py` was red at HEAD on such hosts). The runner now scrubs it.

## [0.192.0] — 2026-08-13

### Removed
- **The library repository has no settings surface** (operator decision): the Settings
  page's Library section, `GET/PUT /api/settings/libraries` and the provision flow are
  gone — the library-sync routine manages the library repo exclusively. The "Code &
  library" settings group becomes "Code" (the source repository only); `libraries_home`
  / `libraries_remote` remain config.yaml keys read at boot.

## [0.191.0] — 2026-08-12

### Added
- **Per-routine `scripts/` return — as tooling, not as a second interpreter** (operator
  clarification of the 0.189.0 removal: the SYMMETRY doctrine was the mistake, the
  persistent-script mechanism was not). The new `script` action (capability `script`,
  conduct: the `scripts` permission doc; `rsched/scripts.py`) runs the routine's OWN
  `scripts/<name>.py` — PEP 723, authored by the run via `write_file`, versioned by the
  routine repo, executed in the persistent workdir venv inside the run's fs jail. The
  envelope is the UTIL model, not the retired standing-settings model: ONLY granted
  secrets the script's header declares are injected (`NAME?` optional = withheld), the
  same four-state exposure gate asks for undecided ones, and there is NO util or model
  access inside (`gu` off PATH, no LLM channel) — a step needing a util's capability or
  a judgment call belongs in the recipe. The capabilities digest keeps the nudge: a
  repeating deterministic sub-step is written once into `scripts/` and called thereafter.
- **The util/script boundary is taught on every surface**: the `write_util` and `script`
  digest bullets and both permission docs carry the placement test (reusable across
  routines → shared-library util; one routine's own pipeline work → its script).
  routine-improver's efficiency lens gains the prose→code pass (spot deterministic prose
  responsibilities, direct the target to script them, propose the capability grant when
  absent) plus the both-ways home check; global-utils-review gains the **demote** verdict
  (a single-caller pipeline util is routed to its owner to become a script).

## [0.190.0] — 2026-08-12

### Changed
- **Test suite wall clock cut** on the 4-core instance, no coverage change. Two causes
  fixed: pytest-xdist now runs `--dist worksteal` (the suite is ~1530 fast unit tests +
  ~110 slow browser tests, and the default `load` scheduler's pre-assigned chunks left
  workers idle at the tail), and the UI harness seeds the library repo ONCE per worker
  (session-scoped template, tree-copied per test) instead of paying `seed_libraries`'
  git init + add + commit subprocesses inside every browser test — the load those
  spawns added is what starved setups and fed the flaky-rerun shield (F261).

## [0.189.0] — 2026-08-12

### Removed
- **The per-routine `procedure` feature, in full** (operator reversal of the 2026-08-12
  symmetry rule): the `procedure` action, `rsched/procedures.py`, the `procedures`
  permission doc, the capabilities-digest bullet that coached runs to accumulate private
  scripts, `POST /api/llm` (the `gu llm` judgment mirror — `ROUTINE_TOKEN_MUTATIONS` is
  empty again), the procedure secret-exposure gate, and the per-routine `.venv`. The
  incentive was wrong: a run nudged to write and reuse persisting Python with util access
  in its own venv grows an unreviewed private tool layer beside the shared, header-linted,
  approval-dialed util library. The only way a run executes code is a GLOBAL util again.
  Live-instance migration in the same change: the `procedures` permission doc and `llm`
  util removed from the library repo, `procedure` capability grants stripped from
  routine configs, stray `procedures/`/`.venv` dirs cleared.

## [0.188.0] — 2026-08-12

### Changed
- **Disabled routines wear an "off" tag** in the table's schedule cell — the 0.187.0 row dim
  alone was too subtle as the only always-visible disabled marker.
- **The table returns to the normal shell column.** The D72 full-width breakout existed for
  the twelve-column layout; five compressed columns fit the 1180px shell, so the breakout
  CSS is retired.

## [0.187.0] — 2026-08-12

### Changed
- **Week-strip legend retired; color identity moves to the routines** (operator ask). Every
  routine's stable identity color (`charts.slugColor` — the hue its week-strip bars use) now
  shows as a swatch on its table row and card title, so the strip needs no legend; the
  avg-runtime provenance ("~30m over 5 runs") moved into each bar's own hover title, and a
  group lane's schedule hovers on its name-column label.
- **Routine table compressed to five columns** (routine · history · schedule+next · last run
  · controls — the twelve-column layout outgrew the screen). The state column folds into the
  history strip (newest bar = last outcome, hover for detail; running rows stay marked live,
  a disabled routine's row dims); schedule and next fire stack in one cell; the last run
  stacks its timestamp over the turns · duration · tokens · cost line; open questions ride
  the routine cell as a chip. Row controls are icon-only with the action in the hover title —
  ▶ run now, ◉ watch live, ⏸ pause, and the hollow ▷ for resume so it cannot be mistaken for
  run-now. Every dropped column's sort key stays available in the filter bar's sort select;
  cards keep their labelled buttons.

## [0.186.0] — 2026-08-12

### Changed
- **Week strip: two-day zoom + scroll** (operator ask — seven days across one panel width was
  unreadably dense). The strip now renders at true pixel scale with TWO day columns filling
  the visible width; the rest of the week scrolls horizontally, and the scroll position
  survives live re-renders. Lane names move out of the timeline into a fixed column on the
  left (they overlapped day one's bars as overlays), stay visible while the strip scrolls,
  and link to their routine; a ResizeObserver re-fits the day width when the panel's size
  changes. Drag-and-drop is unchanged — coordinates were already geometry-based, so drops
  beyond the visible edge still resolve correctly.

## [0.185.0] — 2026-08-12

### Fixed
- **Week strip: scheduled groups render their real fire path** (D71/R313). `/api/schedule/week`
  no longer enumerates a scheduled-group member's own (daemon-suppressed) cron — those bars
  showed runs that would never fire — and instead ships each unpaused scheduled group's own
  cron fires under a new `groups` key. The strip draws such a group as ONE lane whose members
  chain end-to-end at every group fire (member order, split members again as the outbound
  pass, F292), each segment sized by the member's average runtime; estimated starts are
  marked `~` in the tooltip. A suppressed member's armed one-shots stay visible; the members'
  legend rows show the GROUP's schedule instead of the vestigial member cron.

### Added
- **Week-strip lane labels.** Every lane names itself at the row's left edge (haloed over the
  timeline — no label column): the group name on group lanes, the routine name otherwise.
- **Week-strip drag-and-drop** (`weekgrid-drag.js`). Bars drag: onto a sibling bar to reorder
  the group (before/after by bar half), onto another group's lane to join it (leaving the
  current group, split flags preserved), onto the remove strip below to leave the group, and
  along their own lane to reschedule — the GROUP's cron on a scheduled-group lane, the
  routine's own cron otherwise — snapped to 5 minutes and applied through the same
  `schedule.friendly` PATCH the editors use (custom crons are refused with a pointer to their
  editor). A live tip narrates the pending drop; Escape cancels; a drag never triggers the
  bar's navigation click, and live refreshes hold while a gesture is in flight.

## [0.184.1] — 2026-08-12

### Fixed
- **Message folder cards rendered a literal "null"** for absent optional fields (an
  outbox/received row with no detail, a consumed message with no report id): the ternary
  nulls went to the native DOM `append`, which stringifies them — children now go through
  the null-dropping `el()` helper (`static/views/routine-messages.js`; caught on the
  0.182.0 screenshot pass, pinned by a UI-test assertion).

## [0.184.0] — 2026-08-12

### Changed
- **Full recipe/procedure symmetry, wired live** (operator rule 2026-08-12; completes
  the 8c1bfe5/72a49c0/fab03a5 series): a routine is ONE thing with TWO interpreters, and
  everything in its settings applies to both. A procedure's env is the routine's
  STANDING settings — every granted store secret, bound connection tokens, machine
  bindings, the routine API token — with the util library in the jail read-only and `gu`
  on PATH (one jail composition; `wrap_routine` removed). `procedures/` serves two
  roles: THE procedure (the recipe's co-equal) and the recipe's persistent helper
  scripts; the capabilities digest, permission doc and routine-improver's lens carry the
  incentive plus the no-rule-routing clause.
- **`gu llm` routed**: `POST /api/llm` joins the app (the FIRST `ROUTINE_TOKEN_MUTATIONS`
  entry, with a boundary-aware matcher so the allowance can never leak onto
  prefix-sharing sibling routes) — a procedure asks the calling routine's own default
  model; provider keys stay server-side and the spend lands in the durable usage stream
  as `(procedure-llm)` rows under the routine. New library util `llm`.

## [0.183.0] — 2026-08-12

### Added
- **Two-phase group fire (F292; operator standing order R214.3b).** A routine group's chain
  now runs in TWO passes: an INGEST pass over every member in order, then an OUTBOUND pass
  over the members flagged `split` — same order — so all ingestion/processing lands before
  any split member's outbound communication, and a later member's ingest can read an
  earlier member's fresh state. Group membership records carry the per-member `split` flag
  (`{"slug", "split"}`, the R254-confirmed shape): a split member fires once per pass and
  reads its half from a run-scoped `phase=ingest|outbound` boot param — written into the
  run dir's `boot.json` by `Runner.fire` (the file rides the dir, so a resume keeps it),
  read at engine boot beside slug/run_ts (`RunContext.group_phase`, stamped into
  `status.json` as `group_phase`), and surfaced as the harness contract's `GROUP FIRE
  PHASE` marching orders (ingest: process and stage only, NO outbound; outbound: send from
  the staged state, don't re-ingest). A non-split member runs once, in the ingest pass,
  with no param; a group with no split members chains exactly as before. `stop` mid-ingest
  halts the outbound pass too — outbound would read state the halted ingest never staged.
  `rsched run-once <slug> --phase ingest|outbound` exercises a split routine's phase branch
  by hand through the same boot.json channel. MIGRATION (one-shot at daemon boot, expires
  2026-09-30): `.control/groups.json` members convert from slug strings to records;
  pre-F292 in-flight chain files are dropped.
- **`manage_group` speaks the new surface (D77).** `create`/`update` take `split` — the
  subset of `members` that fire once per pass (on update, `members` without `split` keeps
  each kept member's flag; `split` without `members` re-flags the existing list) — and
  `update` takes `paused` (gate the group's cron without clearing it): action parity with
  everything the routines page's group surface can do.

### Changed
- **Group management lives on the Routines page; the `/groups` subpage is retired (D80 —
  closes R107).** The group rows in the routines table carry ▶ run now (with the in-flight
  chain's per-pass progress), ⏸ pause/resume and ✎ edit; a toolbar above the list creates
  groups and holds the instance default-on-failure; the editors are overlay panels
  (`static/components/groupmanage.js`: member order, per-member split flags, on-failure,
  the schedule editor, rename, delete) so they survive the page's live refresh (the F229
  rule). Group chips on cards and rows open the editor in place; split members are badged
  `⇄ split` under their expanded group row; `#/groups` and its nav entry are gone — an old
  bookmark falls back to the landing page.

## [0.182.0] — 2026-08-12

### Added
- **D74 phases 2–4 — Messages everywhere** (operator order 2026-08-05, completing 0.180.0's
  read model). Every routine page carries its four message folders (inbox · outbox · read ·
  received) as a tabbed section with counts (`static/views/routine-messages.js`),
  live-refreshed on the routine's run lifecycle. The INBOX is fully writable — compose for
  the next run, edit a queued message in place (same file, queue position holds), withdraw —
  via the new generic endpoints (`rsched/web/api_messages.py`: `POST`/`PUT`/`DELETE
  /api/routines/{slug}/messages[/{msg_id}]`; the old singular `POST …/message` route is
  gone). The OUTBOX's one write is **retraction** (`DELETE /api/routines/{slug}/outbox/{id}`,
  `reports.retract_report`): an addressed report the recipient has NOT yet consumed can be
  withdrawn — the delivery file is unlinked, a `retracted` event row is appended to the
  append-only ledger, and the item reads `dropped`; a retracted reply settles nothing. The
  report row itself is never edited or user-authored — the decision record is
  docs/messages.md. `read`/`received` are history, no write endpoints.

### Changed
- **The Items page is the Messages page** (`#/messages`, nav "Messages") — the item model,
  ids and `GET /api/items` keep the item vocabulary. Its "waiting for the next run" list now
  shows the routine's WHOLE inbox queue (`queued[]` replaces `pending_feedback[]`,
  `api_audit.queued_messages`), every row editable/withdrawable; withdrawal goes through the
  generic messages endpoint (the audit channel's DELETE route is gone). The **note for the
  next run is a plain user message** in self-audit's inbox (phase 4) — no `[AUDIT note]` tag;
  only structured feedback (finding comments, decision answers) keeps the tagged channel.

## [0.181.0] — 2026-08-12

### Changed
- **Procedures run in the routine's own venv with the routine's own filesystem
  permissions** (operator correction of 0.179.0): a procedure executes via
  `<routine>/.venv` — created on first use in the routine's workdir, its PEP 723 deps
  installed into it (net-open build step), gitignored against the autocommit — inside a
  jail of EXACTLY the run's fs roots (`sandbox.wrap_routine`: no library root), so the
  recipe's file actions and the procedure read and write the same files. NOT the util
  sandbox (0.179.0's ephemeral uv script env + library-visible jail is gone). Secrets,
  gating and the observation shape are unchanged.

## [0.180.0] — 2026-08-12

### Added
- **Messages read model** (D74 phase 1 — operator order 2026-08-05): the four-folder
  per-routine view (`rsched/readmodels/messages.py`, `GET /api/routines/{slug}/messages`)
  folding the existing stores — inbox `msg-*` files (waiting, user-editable), the
  reports ledger's addressed rows (outbox until the recipient consumes, received after),
  and `runs/*/consumed/` (read, capped 50). `answer-*` files stay on the Decisions
  surface by design. Next phases: the routine-page folder UI, the Items→Messages page
  rename, the note-for-next-run migration.

## [0.179.0] — 2026-08-12

### Added
- **Per-routine procedures** (D88 phase 1, option A — user order 2026-08-10): a routine
  may carry `procedures/<name>.py` — PEP 723 scripts, private to the routine, versioned
  by its repo, authored by the run itself via `write_file`. The new gated `procedure`
  action (capability `procedure`; conduct: the `procedures` permission doc, seeded and
  shipped to the live library) runs one inside the SAME Landlock jail and declared-only
  secrets contract a util gets — four-state `secret:` grants, F290 optional withholding
  — with the util's observation shape (truncation, spill pointer, teaching missing
  route). No `calls:` graph, no `gu` on PATH (a step needing a util's capability belongs
  in the recipe), and deliberately no approval dial: the blast radius is the routine's
  own sandboxed permissions. The capabilities digest lists a routine's procedures; the
  secret-exposure gate core is now shared by both callable-script kinds. Phase 2
  (creation-flow split question in adapt.py/clarify-instruction) follows.

## [0.178.0] — 2026-08-12

### Changed
- **One git plumbing home** (F285): `libgit` now owns the single `git()` invoker, the
  push-hook installer (source: `deploy/post-commit`, the same file install.sh uses —
  the quiet per-module hook variants are gone, so every managed repo's hook reports
  push failures), and `init_repo()` (init -b main + neutral identity + optional remote
  + hook + first commit). recipes, utils_lib, bootstrap, scaffold and the library
  settings endpoint all delegate; `repo_root()` moved to `paths`. Seed routines now
  get the push hook at init like every other managed repo (no-op without an origin).

## [0.177.0] — 2026-08-12

### Added
- **Per-call model-role override on `llm` and `subtask`** (D81): the optional `model`
  field names a ROLE (`main|subroutine|tool_call|uncensored`) — the call runs on that
  role's configured model instead of the default (llm → tool_call, subtask →
  subroutine). Naming `uncensored` without a configured `models.uncensored` is a
  teaching rejection; an explicit uncensored llm call is never re-referred. The
  `uncensored-llm` wrapper util is retired from the library (its job moved into the
  engine), per the standing rule that model steps parametrize the ACTION, not wrap a
  util.

## [0.176.0] — 2026-08-12

### Fixed
- **Optional secrets no longer prompt on every call** (F290 engine half + R314, the
  "very old bug"): a `?`-declared secret (D51 — page-fetch's `WEB_AUTH_SOURCES?` backs
  its rarely-used Basic auth) never files the blocking exposure ask and never refuses
  the call. Not granted → the engine WITHHOLDS it from the child env; the observation
  appends a `[note]` naming withheld undecided secrets (with the explicit `ask_user`
  request route) and counting declined ones (R17). Required secrets keep the full
  four-state flow. A public page fetch now runs prompt-free. `util_needs` returns the
  optional subset; a name is optional only when every declarer in the `calls:` tree
  marks it so.

## [0.175.0] — 2026-08-12

### Added
- **A conversation can schedule a group** (R311/R312 — direct user requirement):
  `manage_group create/update` take a flat `cron` field — the group schedule, server tz
  recorded beside it exactly as the Groups page writes it; `""` clears it, absent leaves
  it unchanged; member-cron suppression (D71) applies as always. The observation names
  the resulting schedule. A user's group-scheduling request is now completed by the run
  itself — no operator round-trip to `/groups`.

## [0.174.0] — 2026-08-12

### Changed
- **Wizard vocabulary retired from prose and labels** (F282/D59): ~50 comments, docs
  passages and UI strings that described the retired standalone wizard page in the present
  tense now name the real surfaces (routine creation from a conversation, the clarify
  flow); the Decisions-page badge for clarify asks reads `clarify`. DECISION recorded
  here: the on-disk `.wizard-<ts>` workspace prefix, the `wizard_build_degraded` health
  event kind, the `q.wizard` API field and the `wizard_store.py` module name stay — they
  are live machinery / durable vocabulary, and renaming them is a data migration with no
  user value. Their docstrings say the naming is historical.

## [0.173.0] — 2026-08-12

### Changed
- **One spool mechanic** (F286): the durable request-spool file IO (dir layout, atomic
  write, chrono naming, listing) lives once in `rsched/spool.py`; triggers, schedule-once
  and pending-edits delegate to it. Trigger events thereby gain F298's strict queue-order
  naming — a same-second webhook burst previously replayed in shuffled order (random-hex
  tiebreak). Schedule-once entries stay id-addressed (consumption picks by `fire_at`).

## [0.172.0] — 2026-08-12

### Fixed
- **Routines overview no longer renders vestigial member crons** (R313): a member of a
  scheduled group shows `⛓ <group> — <the group's schedule>` in both the table's schedule
  column and the card meta line (its own cron is suppressed by the daemon and would fire
  never); the group header row carries the same sentence, `paused` when the group is
  paused. `/api/groups` now ships a human `schedule_desc` per group.

## [0.171.0] — 2026-08-12

### Changed
- **Continued-conversation note says the conversation continues in place** (R307): the
  engine's follow-up boot note now tells the model that anything left open waits for the
  user's next reply in the same conversation — never a "next run" — and the converse
  pattern's `reply()` carries the matching wording rule (library + seed). The pattern's
  `answer()` additionally requires verifying ALL N enumerated records before replying
  (R310).

## [0.170.0] — 2026-08-10

### Added
- **Summary: "✓ mark all read"** (F303). The UI traces showed the inbox being cleared one
  click per row — 6–9 rapid "mark read" clicks in a burst, twice in two days. The Summary
  toolbar now carries a bulk button that sweeps every unread row through the same
  per-routine read-marker endpoint and disables itself when nothing is unread.

## [0.169.0] — 2026-08-10

### Added
- **Whole-group pause** (user order 2026-08-10). A scheduled routine group can now be
  paused: `paused: true` on the group record stops its cron from auto-arming the
  sequential chain, while its members stay group-managed — so the entire set goes quiet
  with one switch instead of pausing the whole instance (the only lever until now). A
  paused group's schedulable reads as *disabled* to the daemon, so it simply leaves the
  fire table; resuming recomputes the next FUTURE fire (never a backlog of missed ones).
  An explicit fire — the page's **Run now** or a run's `manage_group run` — still works:
  pause gates the cron only. Groups page: a ⏸ pause / ▶ resume toggle on scheduled
  groups' cards plus a "⏸ paused" badge; `PATCH /api/groups/{gid}` accepts `paused`.
  The chain semantics themselves (member 0 at the set time, the rest in order as each
  predecessor finishes) already shipped as D71 — nothing changed there.

## [0.168.1] — 2026-08-09

### Fixed
- **Rules-migration completion pass for stage prose** (R297, from routine-improver's
  sweep-32). The 0.164.0 traits→rules conversion rewrote routine.yaml and main.md's
  Standing-practices tail but left every INLINE `traits/<slug>.md` reference in
  stages/*.md / instruction.md / main.md body text dangling — ~83 such references across
  17 deployed routines. `migrate_rules` now has step 6: per dir, each reference is
  rewritten through the SAME slug map the rest of the migration used (`ledger-discipline`
  → "the `decision-record` rule", `maintenance-routing` → `problem-routing`,
  `correction-learning`/`anticipatory-stewardship` → `root-cause-fix` + `intent-inference`,
  `global-utils` → "your global-utils permission notes"), enclosing backticks consumed so
  nothing nests; an UNKNOWN slug is logged and left in place — a loud dangling pointer
  beats a silently wrong rewrite. Runs on every boot while the migration module lives and
  is idempotent, so instances converted before this step existed still converge.

## [0.168.0] — 2026-08-09

### Added
- **A live "browser" section in the conversation right rail** (D86 selected A / R262 pt2).
  When a run holds a persistent browser via the `browser-session` util, the rail shows the
  session (url, live/dead from a TCP probe of the recorded CDP port), its latest screenshot
  view — fetched with the auth header and blob-rendered, like every artifact — and a ✕ that
  closes the session server-side. New `web/api_browser.py` (the api_background pattern):
  `GET /api/conversations/{slug}/browser` (rows from the persisted
  `state/browser-session*.json` handles), `GET …/browser/view` (the PNG; a model-written
  view path that escapes the conversation dir is rejected 400), and
  `POST …/browser/{name}/stop` — the server-side twin of `gu browser-session stop`
  (SIGTERM→SIGKILL on the recorded process GROUP, refusing a pid that resolves into the
  daemon's own group, then dropping the handle). The UI could never do this itself — that
  endpoint is the reason D86 existed.
- **Conversation rail sections are individually collapsible** (F296 / R262 pt1): each cap
  (state / tasks / files / browser / background / artifacts) is now a toggle with a chevron;
  the choice persists per browser in localStorage (`convrail:<section>`) across reloads.
  The whole-rail `<details>` switch is unchanged.

### Tests
- `tests/test_api_browser.py` (6): rows + liveness, view serving byte-for-byte, escape
  rejection, named handles, stop-clears-handle without a live process, garbage handle skipped.
- `tests/ui/test_conversation_rail.py` (2): the browser section renders against a really
  listening port and the ✕ clears the session; caps collapse, persist across reload, reopen.

## [0.167.0] — 2026-08-09

### A template is what it declares, not what it is called

The wizard's clarification template was identifiable only by its SLUG, compared against a
hardcoded string in **nine** places across the web layer — every guard (cannot run, cannot
archive, cannot be messaged, rules fixed, no triggers, no recipe edit, no resume, no rewind)
plus the routine card's `protected` flag. A second template would have been silently runnable.

- `kind` is now a **declared field** on RoutineConfig rather than a known key pydantic dropped,
  so `conversation` and `template` are both readable. `guard_template(cfg, …)` reads it, with
  `guard_template_dir(…)` for the run routes, which resolve a run id to a directory and may be
  looking at a conversation (not in the routine registry at all).
- Typing that parameter is what made the change safe: it immediately surfaced **five** stale
  call sites — one in `api_hooks`, four in `api_runs` — that had type-checked fine while the
  parameter was untyped.
- A template no longer appears in `/api/status`'s `meta_routines`. It never fires, so listing
  it as an enabled meta routine read as "self-improvement is on" when nothing was scheduled.

### Fixed

- The clarification template's `main.md` still described "**Traits** — practice modules copied
  into every session", a line the 0.164.0 rules rename missed.

### Migration

`migrate_template_kind.py` (MIGRATION, expires 2026-09-30) writes the marker into the live
template's routine.yaml — `adopt_seed_routine` only installs a routine that is MISSING, so
nothing else would ever add it, and without it the live template becomes runnable and
archivable. It also repairs that one stale line by targeted replace, never by rewriting from
the seed: the recipe editor does not guard that file, so a rewrite could discard the user's own
edits.

## [0.166.1] — 2026-08-08

### Fixed

- `git-sync --continue` stalled a held rebase in the engine's container: `rebase --continue`
  opens an EDITOR to let a human amend the replayed commit's message, and there is none
  ("Terminal is dumb, but EDITOR unset"), leaving the rebase half-finished. It now runs with
  `core.editor=true`, which accepts the existing message — which is what a machine wants, since
  the message belongs to the commit being replayed.
- Caught by the migration's own selftest gate: the 0.166.0 deploy installed `instance-export`
  and `remote` and **rolled `git-sync` back**, because the selftest passes in a dev shell (which
  has an EDITOR) and fails where it actually runs. The gate did exactly its job.

## [0.166.0] — 2026-08-08

### The library-sync routine resolves divergence instead of reporting it

`git-sync` aborted every rebase conflict, so the routine had no mechanism a recipe could
instruct — the conflict was gone before the model saw it.

- **`git-sync --on-conflict hold`** leaves the rebase IN PROGRESS and returns
  `conflicts: [{path, kind}]` — `both-modified` / `modify-delete` / `add-add`, classified from
  the index's unmerged stages rather than by parsing git's (localized) prose. A shell-less
  caller can then read the conflicted files, write resolutions, and finish with `--continue`,
  or walk away with `--abort-rebase`. Default stays `abort`, so other callers are unaffected.
- **The remote tip is tagged before any rebase** (`git-sync-pre-rebase/<branch>/<utc>`).
  Nothing the util does can put a remote commit permanently out of reach.
- **The recipe bounds "reasonable".** The routine resolves same-file conflicts and takes the
  local side under the exported instance trees (they are a mirror it writes). It refuses
  `modify-delete` and `add-add` outright — neither answer is in the diff, so both destroy
  work — and it must re-verify the library's conformance checks after any resolution, or
  abandon the rebase. Exactly the case that came up by hand today: one side had deleted a
  util, the other improved it, and both mechanical answers were wrong.
- The selftest builds two clones of one bare remote and exercises the whole path: hold,
  classify, rescue-tag, abort-restores, then resolve → continue → push → verify from a fresh
  clone that the remote's commit is still reachable.

### Migration

`migrate_seed_utils.py` (MIGRATION, expires 2026-09-30) installs three seed utils over their
live copies, selftest-gated with rollback: `git-sync` (the above), `instance-export` (whose
live copy still documented and selftested `steps/`, `fragments/` and `instruction.md`) and
`remote` (whose live copy lacked the host_key parse fix). A NAMED list, not "seed wins" —
five other utils are newer in production, and a blanket seed→live would have reset
`net: outbound` to `net: none` on two of them, costing them TCP inside the Landlock jail.

## [0.165.3] — 2026-08-08

### Fixed

- **Six of ten seeded utils had drifted from the live library** — revised in production by
  routines and never back-ported, so `util-seed/` no longer described what the instance runs.
  Back-ported the newer live version for `git-sync` (75 lines behind), `dir-tree`, `pytest-run`,
  `git-restore` and `service-logs`; every one re-selftested.
- The drift is **not** one-directional, so a blanket sync either way would have destroyed work:
  `instance-export`'s live copy still documents and selftests `steps/`, `fragments/` and
  `instruction.md` — terminology retired in 0.49.0 and 0.8.0 — while `remote`'s live copy lacks
  the host_key parse fix the seed records. Both keep the seed as canonical; `instance-export`
  additionally adopts the live `net: outbound` header the sandbox rollout set deliberately.
  Those two now need pushing the other way, which a blanket live→seed copy would have silently
  reverted (and a blanket seed→live would have reverted `net: outbound` on two utils, costing
  them network access inside the sandbox).

## [0.165.2] — 2026-08-08

### Fixed

- Every LIBRARY workflow pattern still declared the retired rule slugs in `includes:`
  (`global-utils`, `ledger-discipline`) — including `converse` and `general-task`, whose seed
  copies were updated in 0.164.0. Same trap as the permission doc below, one layer up and
  wider: the seed sync only installs what is MISSING, so an edit in `library-seed/` never
  reaches a live instance, and the library also carries curator-drafted patterns the seed
  never had. Left alone they lint red and keep seeding new routines with dead slugs.
  `migrate_rules` now rewrites every library workflow's `includes:` through the slug map,
  locating the literal via the AST so the edit survives whatever formatting the pattern uses.

## [0.165.1] — 2026-08-08

### Fixed

- The `practice-library` permission doc survived the 0.164.0 deploy on the live instance.
  Deleting it from `library-seed/` only stops FRESH instances getting it — the seed sync
  never deletes (by design: it must not clobber a user's own library docs), so the existing
  copy stayed. Its `requires: [read_trait]` names an action kind the engine no longer has, so
  it sat permanently lint-red on the Library tab. `migrate_rules` now deletes retired conduct
  docs from the live library too. Found by checking the running instance after the deploy, not
  by a test — the suite only ever saw a seeded library.

## [0.165.0] — 2026-08-08

### Publishing the instance is a routine again, not a Settings job

`library-sync` was a routine until 0.29.0, when it became a plain daemon job on the reasoning
that it is "the exact same commands every time, no LLM in the path". That argument was about
the wrong half of the work. The two git operations are trivial; noticing that a push has
stopped landing, and getting that in front of someone, is not — and the daemon job's only
outcome surface was a status file nobody opens. It had let **94 commits accumulate unpushed**.

- **New bundled `library-sync` routine** (disabled, daily 06:00): stage the instance's routines
  and redacted config into the library repo's working tree, then commit / pull / push. It is
  deliberately barred from repairing anything — no conflict resolution, no force-push, no
  re-authentication — because that repo is the only off-box copy of the instance. A conflict,
  a rejected push or a refused credential is a `report`, and a run that exported cleanly but
  did not push finishes **failed**, not ok.
- **Removed**: `library_sync.py`, `web/settings/library_sync.py`, `LibrarySyncConfig` and the
  `library_sync:` config block, the scheduler's sync timer / fire path / `library_sync_next`
  status field, the Settings → Library sync card and nav entry, and the failure toast.
- The `instance-export` and `git-sync` utils are unchanged — they were always where the actual
  work lived, and the routine drives them the same way the retired one did.

### Migration

`migrate_library_sync.py` (MIGRATION, expires 2026-09-30) clears two pieces of daemon-era state
that would each fail SILENTLY:

- `library_sync:` in config.yaml — now an unknown key, so it would warn on every boot forever.
- `<routines>/.archive/library-sync-retired/` — `adopt_seed_routine` treats an archived copy as
  a deliberate removal and matches by slug PREFIX, so this tombstone would have blocked the new
  routine from ever installing, with nothing in the logs saying why. Renamed rather than
  deleted: it holds real run history from July 2026.

## [0.164.0] — 2026-08-08

### Traits are now general RULES — one library copy, held by slug

Practice **traits** (per-routine adapted copies under `<routine>/traits/`) became general
**rules**: principle prose with exactly ONE copy, in `<library>/rules/`, that a run applies to
its own particular case. A routine holds SLUGS (`routine.yaml` `rules:`) and reads the prose on
demand with `read_rule`, so revising a rule reaches every routine holding it at its next run —
with no migration and no per-routine fork to drift. The evidence for the change was on the
instance: the live routines' trait copies were byte-identical to each other and differed from
the library only by improvements made *after* they were created.

- **Ownership split in two.** WHICH rules bind a routine is config — user-only, since no run
  writes routine.yaml (*General rules* panel on the routine page; the conversation header). The
  TEXT is the library's: yours on the Library tab, and writable by a routine holding the new
  **rule-authoring** permission.
- **`read_rule` (renamed from `read_trait`) is now UNGATED.** A routine must be able to read
  what binds it, and reading library prose has no side effect. The `practice-library` permission
  and the `read_trait` capability are retired.
- **New `write_rule` action** behind `rule-authoring`, mirroring `write_util`: `content` to
  author a new rule, `anchor`/`replacement` to revise one in place, library-linter gated before
  the approval ask, committed to the library. It carries its OWN approval dial —
  `capabilities.rule_confirm` — because a rule revision lands on every holder, which is not the
  decision `confirm` (write_util) governs. There is deliberately **no `remove_rule`**: deleting
  a rule silently un-binds every holder with nothing to catch it, so a run reports it instead.
- **Creation no longer adapts.** The decompose pipeline lost its trait leg (one fewer LLM call
  per routine); it now receives the held slugs as an index and main.md's *Standing practices*
  tail names them.

### New maintenance routine: `rules-review`

Owns the rules layer. It reads how runs across all holders actually interpreted each rule —
followed, misread, ignored, or a good interpretation the text never contained — and revises the
shared text from that evidence, with `write_rule` under the user's approval level. Its
`stages/route-elsewhere.md` carries what is genuinely instance-specific: the problem-class →
owner table, and the boundary with routine-improver (one routine misreads a rule → that
routine's problem; several read the same sentence differently → the sentence).

### Rule-set changes

- **`global-utils` became a permission.** It is mechanism prose (how to read a util's usage
  line, what to do when one errors) and mechanism is exactly what a conduct doc is for — a rule
  names no tool. Held by default; `requires: {}`, the first permission that presumes no
  capability (the lint now accepts an explicitly empty `requires:`).
- **`ledger-discipline` → `decision-record`**, generalized to its purpose: keep the reasoning
  the artefacts cannot carry, read it before exploring, record what you rejected and why. The
  filename, entry format and rotation threshold moved to the workflow patterns that own the
  mechanism.
- **New `intent-inference` rule** — read every user intervention as evidence of a standing
  preference: name the intention behind it, record it as a falsifiable hypothesis, act on it,
  correct it in the open. A routine default, and the highest-value rule in conversations.
- `DEFAULT_RULES` is now `ask-policy / web-research / decision-record / intent-inference`.
- **`maintenance-routing` split into its two independent halves.** The REPORTING discipline —
  owner not operator, the artefact names the owner, work order not hint, close what you receive
  — is now the general **`problem-routing`** rule, available to every routine. The instance's
  ownership TABLE is not general at all and moved into `rules-review`'s recipe.
- **New `root-cause-fix` rule**: repair the cause, never the symptom — trace back until the
  answer names something changeable, install a GENERAL prevention at the level the cause lives
  at, in the run that found it; the same class arriving twice means the prevention was too
  weak. It pairs with `intent-inference` and is deliberately separate from it: one asks what
  the user WANTED, the other why they had to say it at all.
- **`change-restraint` extended** with a look-before-you-build clause: check for an existing
  store/site/pipeline before standing up a second one.
- Two routine-LOCAL modules the improver had authored (`correction-learning` on
  nanogeofeld-stewardship, `anticipatory-stewardship` on fau-grant-application-prep) existed
  nowhere else. Both conflated the same pair of principles; the migration maps each to
  `root-cause-fix` + `intent-inference` rather than promoting a per-routine fork into the
  library.

### Migration

`migrate_rules.py` (MIGRATION, expires 2026-09-30) converts the production instance at boot:
library `traits/` → `rules/` with headings rewritten; each routine's/conversation's trait copies
→ its `rules:` list (retired slugs mapped — one may expand to several rules — directory
deleted); `practice-library` and `read_trait` stripped; `global-utils` added as a permission
where the trait was held; every `## Standing practices` tail rebuilt.

Two safety properties are tested. A routine-local module the library never carried is
**promoted** into `rules/` before any `traits/` dir is deleted — dropping it, correct for an
adapted fork, would be silent data loss for the only copy. And a RETIRED slug is never carried
over under its new name: the replacement ships in the seed, already generalized by hand, so
copying the old body would undo the generalization on the next boot.

## [0.163.0] — 2026-08-08

### Fixed
- **Web Push notifications were discarded whenever the device was asleep.** `pywebpush`
  defaults to `ttl=0`, which instructs the push service to deliver only to a device
  connected at that instant and otherwise drop the message — no queue, no retry — and the
  send still returns success, so nothing anywhere reported a loss. The notification an away
  operator most needs (phone in a pocket, screen off, radio dozing) was therefore the one
  most reliably thrown away. Sends now carry a 24h TTL, so the service holds the decision
  until the device reconnects.
- **A browser holding the routine token is no longer stranded.** The R94 tier refusal now
  carries `WWW-Authenticate: Bearer error="insufficient_scope"` (RFC 6750 §3.1), and
  `static/api.js` re-opens the token gate on that marker as it already does on a 401. The
  routine bearer reads everything, so the console renders whole and only the first
  mutation fails — previously an unactionable toast with no route back to the token field,
  and on a phone no devtools to clear localStorage by hand: the only exit was clearing
  site data. Ordinary 403s (protected template, credentials dir, denied path) omit the
  marker and leave the session alone — asserted in both directions.
- **Web Push no longer dies silently when a subscription rotates.** A browser may retire a
  subscription on its own; the server learned only on the next send's 404/410, by which
  time that notification was lost and every later one too — while Settings still read
  "subscribed", because the BROWSER had a subscription, just not the stored one.
  `static/sw.js` now re-registers on `pushsubscriptionchange` (re-subscribing itself when
  the event carries no subscription, as Chrome's does not), and Settings → Notifications
  re-POSTs the live subscription on open for browsers that never fire the event. Both are
  endpoint-keyed upserts, so they no-op when nothing drifted. A worker cannot read
  localStorage, so the console mirrors the token into a Cache entry; the two literals
  naming it are spelled out in both files and a test keeps them paired.

## [0.162.0] — 2026-08-08

### Added
- **Schema-storm fast-fail (D87-A, F297, R255).** A run whose last 4 CONSECUTIVE turns
  each needed schema-rejection retries now fails early with a capability-naming outcome
  ("the model cannot reliably hold the action schema — pick a stronger model") instead of
  limping to the budget wall at full-prompt retry prices (c-20260806-150112: 12 retries,
  477K input tokens). A clean turn resets the streak, so occasional retries never trip
  it; the per-turn force-fail outcome now names the model and the same capability
  diagnosis instead of a generic "failed after 3 attempts".

## [0.161.0] — 2026-08-08

### Added
- **"Allow once" now covers secret and filesystem grants (D76, operator-selected opt A).**
  D65 scoped the fifth decision to turn-action classes; the operator chose the explicitly
  coarser promise for the rest: a once-granted `secret:` is spent by the next util call
  whose script (or its `calls:` tree) DECLARES the var — the injection surface — and a
  once-granted `fs-read:`/`fs-write:` root by the next file action under it or the next
  util invocation (every util's sandbox mounts the run's granted roots wholesale).
  `entities.ONCE_CLASSES` is the new vocabulary; the Decisions page offers the button for
  those requests; `connection:`/`machine:` stay four-state (a binding, not a spendable
  use). Once-armed grants no longer flow to child runs at all — "one action" must not
  become a child's whole-run grant (`childrun.inheritable_resources`).

## [0.160.5] — 2026-08-07

### Fixed
- **Same-second mid-run edits now replay in queue order (F298).** The pending-edit spool
  named files with a second-resolution timestamp plus RANDOM hex, so a burst of edits
  queued within one second was shuffled at replay (and the replay test was a coin-flip).
  A single zero-padded nanosecond sample in the name makes the sort strict; the hex
  suffix only de-collides parallel writers.

## [0.160.4] — 2026-08-07

### Fixed
- **Plain `ask_user` questions no longer render literal `\n` (D85-A, F291, R242).** Some
  models double-escape newlines when authoring the question; the renderer and store were
  correct, so intake now normalizes literal backslash-n in the question and default of
  PLAIN questions only — util-approval and access-request text (which can embed util
  source) stays verbatim.

## [0.160.3] — 2026-08-07

### Fixed
- **A message sent to a finished conversation now appears immediately (F295).** The chat
  renders user bubbles from `user_injection` transcript events, and a post-finish send has
  no such event until the woken leg boots — so the user had no proof the message landed
  (operator report, 2026-08-07). The view now echoes the sent message at once as a pending
  bubble (dashed, "✓ sent" hint) that survives the post-send remount and is replaced by the
  real transcript bubble when the leg picks the message up.

## [0.160.2] — 2026-08-06

### Removed
- **`abort_process`'s two dead parameters (F283).** The run-dir/run-id every caller
  dutifully passed were never used — close-out attribution is the caller's job
  (`_close_out`/`_reap`). Signature narrowed to `abort_process(pid)`; `abort_with_fallback`
  loses its equally-dead `run_id` and all five call sites are updated.
- Also: `readmodels/stats.py` `_run_ref` docstring now states the real fallback condition
  (any value without an endpoint separator, not only an empty field) — F287 verified, the
  behavior itself was correct.

## [0.160.1] — 2026-08-06

### Fixed
- **A granted write root that didn't exist yet silently vanished from the util jail
  (F293, R244).** The Landlock child wrapper attaches each rule to a live fd and skips
  paths it cannot open, so a fresh grant (e.g. a new WhatsApp session store) died with
  `PermissionError` on the util's first `mkdir`. The grant now implies the directory:
  `sandbox.wrap()` creates missing write roots daemon-side before assembling the jail;
  a missing READ root is warned about once instead (creating it would mask a config typo).
- **A write grant now implies read (F294, R244).** The engine's `read_file` gate refused
  paths under a write-only root even though the util sandbox always gave write roots full
  rw access — a run could write a file it was not allowed to read back.
  `RunContext.read_roots()` folds in the writable roots.

## [0.160.0] — 2026-08-06

### Added
- **Mid-conversation folder access (D82).** A conversation's fs read/write roots are now
  editable from the ⚙ capabilities & budgets header panel — the same server-side directory
  picker the composer offers at create time. `PATCH /api/conversations/{slug}` accepts
  `fs_read_roots`/`fs_write_roots` (validated, replace-wholesale; an empty list clears the
  grants), and like every conversation config edit the change applies at the NEXT reply's
  boot. Unblocks granting a running conversation new directories without recreating it
  (R240's fs-ops stopgap loses its main justification).

## [0.159.1] — 2026-08-06

### Removed
- **4 proven-dead CSS classes (F288).** `.setup-banner` / `.sb-dot` / `.sb-text` (base.css)
  and `.pick-row` (views.css) were referenced only in the stylesheets — the setup banner has
  built with `panel warn` since the wizard-machinery retirement (D59). Removed the rules
  (keeping the live `.notice-banner` they shared a selector with). No behavior change.

## [0.159.0] — 2026-08-06

### Added
- **Editing a routine while a run is active no longer bounces with a "busy" 409 — the edit
  is queued and applied at run end (D78 option A, F279).** Operators tuning a routine
  mid-run were hitting ~20 `guard_not_active` 409 toasts in 40 minutes. Non-destructive
  edits — recipe/state file saves (`PUT …/file`), recipe rollback (`POST …/recipe/revert`),
  and webhook/report trigger create/retune/delete — now land in a durable per-routine
  pending-edit spool (`.control/pending-edits/<slug>/`, mirroring the trigger event spool)
  while a run is active, and the daemon replays them in order at the reap that follows every
  run (`Runner._reap`), when no writer contends the git index. The editors show
  "queued, applies when it ends" instead of an error. Destructive operations (archive,
  conversation teardown) keep their hard 409 — "apply this deletion after the run" is not a
  safe default. One applier per edit kind (`rsched.pending_edits`) serves both the immediate
  (idle) and replayed (queued) paths, so a queued edit has identical effect to a live one; a
  bad edit is recorded and its spool file dropped so one can't wedge the queue. New module
  `pending_edits.py`; queue-or-apply helper in `routines_common`; tests in
  `test_pending_edits.py` + `test_api.py`.

## [0.158.1] — 2026-08-06

### Fixed
- **The routines table no longer double-lists grouped routines (F281, reviewer order).**
  0.157.0's flat-list-stays-complete choice meant every grouped routine appeared twice —
  once under its collapsible group row, once in the flat list. A grouped routine now lives
  ONLY under its group row (expand to see or act on it); the flat sorted list carries just
  the ungrouped rest. A groups-fetch hiccup still degrades to the full flat list.

## [0.158.0] — 2026-08-06

Engine hardening for two externally-surfaced defects (F278 window guard; F280 byte-faithful
util install).

### Added
- **The window guard (F278).** A completion that 400s with a context-overflow stating a
  SMALLER maximum than the catalog's configured window no longer kills the run: the engine
  parses the provider's stated max from the error text, shrinks the RUN-LOCAL view of that
  model's window to it, re-clamps the prompt and retries the same model exactly once —
  emitting a `compaction` transcript event and a new `model_window_corrected` health event
  naming the lying catalog entry. Config stays authoritative for sizing down; the provider
  is authoritative for sizing up. (On 2026-08-05 a gemma entry raised to 250k tokens against
  the provider's real 65,536 disarmed every compaction gate and 400'd two live conversations
  — the operator has since corrected the entry; this guard makes the next config lie a
  logged correction instead of a dead run.)
- **`write_util` content-from-file (F280, R226).** `write_util` accepts `path` as a third
  content source: the engine installs the util script from that file's EXACT bytes (read
  under the run's own readable roots), so a large pre-built script — a subtask's tested
  draft, a 62KB consolidation — is never re-typed through one reply. Rides the identical
  header + approval + selftest + rollback gate as inline content; `path` stands alone
  (never combined with `content`/`anchor`).

## [0.157.0] — 2026-08-06

The Routines page becomes table-first (D72 + D73 — operator-selected 2026-08-05).

### Added
- **Inline ⏸ pause / ▶ resume on every routine** — card actions AND table rows. One PATCH
  on `enabled`, no trip to the config page. While a run is active the control disables
  itself with an explanatory title instead of letting the click bounce into the 409 toast.
- **Groups as collapsible table rows (D73).** Each group renders as its own header row in
  the routines table; expanding lists its member rows right beneath it in the group's FIRE
  order. The flat sorted list below stays complete — sorting and filtering keep their
  meaning, and a routine in two groups appears under both. Expansion persists like the
  view mode.

### Changed
- **The table IS the default routines view** (was the card grid); a stored user choice
  still wins, and the card grid stays one toggle away.
- **The routines table fits the screen**: the list view breaks out of the 1180px shell
  column (`.breakout`, capped at 1800px) instead of cramming eleven columns into it.

### Fixed
- The card/table toggle's own label now flips immediately on click — it used to stay stale
  until an unrelated tag change happened to rebuild the filter bar.

## [0.156.0] — 2026-08-06

The Items page becomes a worklist, and the user's priorities reach the routines (D75 —
operator-selected 2026-08-05).

### Added
- **Item priorities (⚑).** Every card on the Items page carries a ⚑ toggle. A flagged item
  floats to the top of the list, and — the point — the OWNING routine's next run reads the
  flagged ids it owns as a "PRIORITY items" section at the head of its state digest, so its
  orient stage sees the user's "work this first" before it plans. Ownership is resolved,
  never stored: an `R<n>` belongs to its `target` (untargeted triage rows to self-audit),
  every `F<n>`/`D<n>` to self-audit. The store is one small map in
  `.control/item-priorities.json` (`src/rsched/priorities.py`) — deliberately NOT
  report.json (self-audit rewrites that wholesale every run, which would clobber the
  user's flag) and not the append-only reports ledger. `POST /api/items/{id}/priority`
  writes it; the items read-model treats the store as a fifth memo source so a toggle
  invalidates the cache like any other edit.
- **`status` filter accepts a comma list.** `GET /api/items?status=open,in_progress` —
  one param, no second filter channel.

### Changed
- **The Items page defaults to the ACTIVE backlog** (open + in_progress), with an `active`
  chip leading the status row — the page is a worklist first, an archive on request via
  `?status=all` (mirrors 0.154.2's Summary-unread default). A `?focus=<id>` deep-link still
  defaults to the whole set: it exists to show that card, which may be archived.

## [0.155.0] — 2026-08-05

Loop control for the report trigger. Nothing here changes what a routine can DO — a run
still only files work — but the fleet-scale cost of that work is now bounded.

### Added
- **A day's cap on trigger-initiated fires.** `max_fires_per_day` (report triggers: **24**;
  `0` = uncapped) bounds what the cooldown structurally cannot: a cooldown limits the RATE
  of fires, not the total, so two routines answering each other would stay awake forever at
  one run per window. Editable in place beside the cooldown; the count is per trigger, keyed
  on the server's date. Reaching it emits ONE `trigger_capped` health event for the day —
  a capped trigger is a dark routine, and dark must be visible (F276) — and the waiting
  inbox work is picked up by the next scheduled run, never dropped.

### Fixed
- **A closure no longer buys its recipient a run.** A report filed with `closes: true` asks
  nothing, yet it landed in the target's inbox and fired that routine's report trigger — a
  full run of a recipe to read "no reply needed". Closures now carry the marker into the
  inbox message and the trigger skips a closure-only inbox, the same exemption `answer-*`
  files have; delivery is unchanged and the next run reads it anyway. This was the
  amplification path the 0.153.0 answer-everything closeouts opened: every acknowledgment
  in the fleet was buying a run.
- **`cooldown_s: 0` on a report trigger meant 900, not 0.** The daemon read it as
  `int(trig.get("cooldown_s") or DEFAULT)`, and `0` is falsy — so the documented "fire on
  every delivery" silently became the 15-minute default. Reads the key with a default
  argument now. (The webhook path's `or 0` was always correct.)

## [0.154.3] — 2026-08-05

### Fixed
- **Correct the `fire_refused` (F276) justification — it was based on a wrong diagnosis.**
  The 0.154.1 docstrings (runner.py, health_events.py) and CHANGELOG claimed a 44 h
  self-audit gap on 2026-08-04/05 was a dropped scheduled fire (a stuck-active slot from an
  un-honored restart). The operator corrected this: the gap was a **deliberate pause of all
  routines**. A global pause is skipped in the scheduler (`is_paused`, before `Runner.fire`)
  and is intentional — never a refusal, never this event. The prose now describes only the
  genuine overrun/drain refusals `fire_refused` actually covers, and states explicitly that a
  pause is not logged as a refusal. No behaviour change — the 0.154.1 telemetry stands; only
  the misleading example is removed.

## [0.154.2] — 2026-08-05

### Changed
- **Summary opens on Unread by default (operator, 2026-08-05).** The Summary tab's point
  is what you have not yet seen, so it now defaults to the Unread filter unless the URL
  explicitly asks for `?filter=all`. The All chip still shows every routine's latest
  message. UI-test updated to assert the default chip and read-persistence via All.

## [0.154.1] — 2026-08-05

### Fixed
- **A due cron fire that produces no run is now audible — F276.** `Runner.fire`
  refusals (a still-active routine from a prior run — overrun — or the daemon draining
  for a self-update restart) only wrote a `log.info` line, so a routine chronically
  un-fired for one of those reasons left no trace in the health-events audit stream. A
  refused **scheduled** fire now emits a `fire_refused` health event (run_id empty);
  resume/trigger/manual overruns are expected and stay quiet. A deliberate global pause is
  NOT this event — it is skipped earlier in the scheduler and is the operator's own known
  action. (This instrumentation was originally motivated by a mistaken diagnosis of a 44 h
  self-audit gap on 2026-08-04/05 that was in fact a deliberate operator pause of all
  routines, not a dropped fire; the telemetry gap it closes for genuine overrun/drain
  refusals is real regardless.)

## [0.154.0] — 2026-08-05

### Added
- **A live trigger's cooldown is editable in place.** `PATCH
  /api/routines/<slug>/triggers/<id>` takes `{"cooldown_s": N}`, and the Triggers card
  renders each row's cooldown as a field that saves on commit. Editing beats
  delete-and-recreate: a webhook keeps its token, so a URL already handed to a third party
  survives the change, and a report trigger — one per routine — had **no** route to a
  non-default window at all. `id`/`token`/`type` are identity and stay unpatchable
  (`extra="forbid"`); the edit is guarded while a run is active, like every config write.

### Fixed
- **The Triggers card's "cooldown (s)" box read as an editor for the triggers listed above
  it.** It was the create-form's input, wired only to "+ add webhook trigger" — so a
  changed value looked unsaveable (there was nothing to save) and silently did nothing to
  existing rows. Each add-button now carries its own cooldown box, adjacent to the button
  it feeds, and "+ add report trigger" honors it instead of always using the 900 s default.
  The one-per-routine refusal ("adjust its cooldown instead") now names a path that exists.

## [0.153.0] — 2026-08-05

**Backlog-clearing operation**: an operator-directed sweep resolved every open/in_progress
maintenance item (80: 69 R + 7 F + 4 D) via seven parallel verification/repair sessions plus a
sequential feature wave, then closed the ledger loops. Companion protocol changes attack WHY the
backlog grew (see the meta-productivity analysis in the operation record).

### Added
- **Terminal ack for reports.** `report` gained `closes` (valid only with `answers`): a closure
  settles its target AND is itself born settled — the settle protocol no longer mints a new open
  report per handshake (54% of the backlog was exactly that). Prompt surface, Items read model,
  itemcard, docs/items.md, prompt-anatomy all carry it.
- **`allow once (this action only)` grant scope — D65 (operator: turn-action classes only).**
  Fifth decision for `action:`/`util:`/`runs:`/`workflows:`; spent by the next successfully-
  dispatched matching action, engine-revoked at that boundary with an `[ONCE-GRANT SPENT]`
  teach-back; secret/fs stay four-state (their consuming use is invisible to the turn loop);
  web refuses the button there, engine seams fail closed.
- **Group shared store — D67 (B-i).** Runs of grouped routines get
  `.control/group-stores/<gid>/` injected into fs read+write roots (sandbox-honored,
  child-inherited, named in the harness contract; whole-file atomic, last-write-wins per file).
- **Group cron — D71/F275.** A group may carry its own schedule (web records, daemon fires): a
  due fire auto-arms the D53 chain; members of a scheduled group are suppressed from the cron
  loop and boot catch-up — one fire path. The routine page's Schedule dropdown shows the
  "group managed" state, linking to the group.
- **`report` trigger type.** A routine declaring it is fired by the daemon when a report lands
  in its inbox (900 s cooldown coalescing; never while active; disabled respected) — multi-leg
  report exchanges collapse from days to minutes.
- **Two-tier API auth — R94 (operator: ENFORCE, superseding D68).** `routine_token` (generated
  on boot, injected into runs as `RSCHED_API_TOKEN`) is refused on every config-mutating route
  with a pointer to `ask_user config_patch`; mutating routes are primary-only by default. The
  HTTP flank of "config is the user's" is closed.
- **Folder access at conversation create — D70.** The new-conversation composer grants fs
  read/write roots written into the config before the engine boots (native root-list form);
  changing the project dir later no longer wipes granted roots.
- **Model window-fit — R128 residual.** Conversation-create/model-change refuse a model whose
  output reservation exceeds its window (warn on merely-tight), and both pickers label per-model
  context sizes — same math as the runtime ceiling.
- **Inline approvals — R132.** Blocking questions render one-click approve/decline (and the
  typed grant decisions) directly in the conversation/transcript bubble, through the same
  answer endpoint as the Decisions page.
- **xvfb + xauth in the engine image — F274.** Headful Chrome per util via `xvfb-run`, opt-in,
  no global DISPLAY.

### Fixed
- **Classifier refusals — R5.** All three adapters surface `stop_reason`/`stop_details`
  (claude-cli handles both envelope shapes); the engine branches on refusal before the
  empty-completion path: distinct transcript error naming the category, optional uncensored
  referral, immediate fallback-chain advance (run-scoped cooldown), honest terminal error when
  no fallback — never a same-model retry, never "empty completion".
- **Mid-run web grants — R118/F273.** A web-answered deferred access request bridges into the
  running run at the next turn boundary (same `apply_deferred_decisions` seam as boot):
  "usable now" is now true. Request entity ids canonicalize (`fs-write:~/…` grants the
  absolute root every enforcer compares).
- **Conversation config_patch — R102/F267.** The Decisions page applies to the owning home
  (conversations vs routines; the 404 black hole is gone), verifies the endpoint's
  applied-field list before claiming success, and un-appliable proposals fail honestly.
  `patch_routine` reports every applied field. Patch models (`RoutinePatch`,
  `ConversationPatch`, `GroupPatch`) now `extra="forbid"`.
- **Finish-window messages — R108/F268.** A `finish` racing an injected user message is
  deferred (message becomes the next turn); paths that must end name the still-queued message
  in the summary; the conversation message endpoint re-checks liveness after the durable write
  and the daemon's reap sweeps every clean finish for stranded user messages — no more manual
  "pls resume".
- **Secret-decline leak — R17.** A declined exposure reports a count, never the names.
- **write_util diagnostics — R93/R20.** Header violations render as such (naming each bad
  line); failed-selftest output keeps head+tail so the traceback survives; the selftest runner
  prewarms deps for net:outbound utils; a bool-vs-string bug that silently prewarmed EVERY
  util call since 0.125.0 is corrected.
- **UI stream budget — F263.** Transcript tails capped at 3 held sockets (+bus), REST-polling
  fallback with self-upgrade, jittered backoff, corrected reconnect telemetry — the diagnosed
  per-origin connection-pool exhaustion is structurally impossible.
- **Degraded decompose — R14 class.** The verbatim-fallback path renders resolved parameter
  values into main.md (the wizard materializer itself was retired in D59).
- **CLI vs sandbox — R25 residual.** An unreadable server config yields a problem line with
  the unjailed-shell hint instead of a traceback.
- Regression pins added for the already-fixed R1 (append preserves content), R2 (remove_util
  persists), R3 (resume reseeds util counters), R9 (one-action contract), R13 (child roots).

### Library & recipes (companion commits in the library/routine repos)
- usenet-nzb destination-safe fetch (R135); digest-render `--votes` sinking (R127);
  newsletter-extract heise bundled blocks with a real-issue fixture (R96/R99/R100); gmail
  fixes verified already live (R10/R11/R64). Closeout friction-sweep nudge (R18) +
  one-problem-one-id correction discipline in maintenance-routing; self-audit now triages the
  FULL open set with a move-ten-oldest quota; all three meta closeouts answer every delivered
  report the same run (`closes: true`). bina draft generation pinned to the routine's own
  `llm` action (R133); self-audit's cruft child-brief matches the subrun toolset (R4).

## [0.152.1] — 2026-08-03

### Fixed
- **Conversation context overflow on small-window orchestrator models — F265, 4th recurrence
  (R113/R114).** Conversation `c-20260802-110156` (gemma-4-26b, 65536-token window) 400'd again
  with `context_length_exceeded` at 2026-08-03T04:46 **under the deployed 0.149.1 window clamp**:
  the clamp trimmed the prompt to its char ceiling, but that ceiling was sized at the optimistic
  `CHARS_PER_TOKEN = 4`, and the payload (dense usenet dumps) packed at ~3.72 chars/token — so
  the ceiling's ~183.5k chars counted 49384 real input tokens; + 16384 requested output = 65768,
  over the 65536 window by 232 tokens. The three prior F265 fixes tuned a flat fractional margin,
  which cannot cover a density error that scales with the payload. `window_ceiling_chars` now
  computes in the **token domain** — window tokens minus the output reservation gives an input
  **token** budget, converted to a char ceiling at a conservative `INPUT_CHARS_PER_TOKEN = 3.5`
  (denser than any real content) — so `input_tokens + max_output_tokens ≤ window` holds **by
  construction** for content at or above that density, not by a hoped-for margin. Retired the
  `OUTPUT_RESERVE_SAFETY` flat-margin constant. Only affects small-window models (large windows
  stay governed by the 0.6/0.8 fraction trigger — unchanged). Existing F265 regressions updated
  to the token-domain formula; the real-density guards (3.9 chars/token) still lock the bound in.

## [0.152.0] — 2026-08-03

### Added
- **The new-conversation composer can start a conversation in admin mode (D66).** The
  Conversations "admin" toggle previously lived only on an existing conversation's message
  composer — but a conversation's **first reply fires on create**, so arming admin afterward
  always missed it. The new-conversation composer now carries the same admin toggle: arming it
  and clicking "start conversation" sends the `x-admin-token` header on the create request, and
  the server drops the one-shot admin marker on the freshly-created run dir **before its engine
  boots** (no `await` between `fire()` returning and the marker write, so it lands ahead of the
  supervisor spawning the subprocess that reads it at loop init). Same web-layer-only token
  check as `/message` and `/runs/{id}/converse` — the token never reaches the engine, and admin
  lifts only capability gating, never the structural/ownership gates. Wrong or absent token
  leaves no marker (fail-closed). Covered by a create-endpoint test and a chromium UI flow test.

## [0.151.1] — 2026-08-02

### Fixed
- **`kindsurface.py`'s module docstring cited the wrong action-kind count (F272).** The prose
  described a run being sent "8 of the 21 kinds … all 21 in the schema", but the action
  contract (`actions.py` `KINDS`) has grown to **23** kinds — the "21" was stale by two. The
  count is corrected to 23. A self-updating guard test
  (`test_module_docstring_kind_count_tracks_kinds`) now regexes the count out of the docstring
  and asserts it equals `len(KINDS)`, so this prose can never silently drift from the contract
  again. Documentation/test only — no behaviour change.

## [0.151.0] — 2026-08-02

### Changed
- **The dashboard "this week" strip draws same-group routines on one shared row (F271).**
  Routines that belong to the same group are now merged onto a single week-strip lane, laid out
  in the group's member (execution) order, so a group reads as one chain across the timeline
  instead of being scattered over separate rows. A routine is placed in the first group that
  lists it; ungrouped routines keep their own row; lanes are still ordered by earliest upcoming
  fire. The legend tags each grouped member with a `⛓ <group>` label. Driven by the ordered
  groups already fetched from `/api/groups` (a groups hiccup degrades to per-routine rows).
  Covered by a chromium UI test asserting two grouped routines collapse to one row while a solo
  routine keeps its own.

## [0.150.0] — 2026-08-02

### Added
- **Rewind a conversation to a chosen turn and re-open it live (D69).** A run that died or
  derailed — e.g. a context overflow that killed a conversation — can now be rewound instead of
  lost. New `POST /api/runs/{run_id}/rewind` (terminal runs only) truncates the transcript
  through a given turn — keeping that turn's action and the observation it saw — archives the
  dropped tail to a timestamped `rewind-<ts>.jsonl` sibling (nothing is destroyed), and then
  resumes on the same run dir so the replay continues live from the kept point with a fresh
  budget window. The run view offers a **⟲ rewind** control beside *resume* on any terminal run:
  it prompts for the turn to keep through and reconnects to the re-opened run. Core logic is
  `history.rewind_transcript`; covered by a unit test, an API test, and a chromium UI flow test.

## [0.149.1] — 2026-08-02

### Fixed
- **Context overflow now enforced by trimming, not just by an earlier compaction trigger
  (F265, third recurrence).** A conversation with a few very large observation bodies could
  400 with `context_length_exceeded` and die even after the 0.148.1/0.148.5 margin fixes,
  because compaction only elides the conversation *middle* — the retained head+tail is an
  incompressible floor, and a short conversation (≤ 30 messages) has no middle to elide at
  all. When that floor's own bodies exceeded the window minus the output reservation, every
  compaction path returned unchanged and the next completion overflowed (observed 3× on
  conversation `c-20260802-110156`). Added `clamp_to_cap`: a last-resort step that runs after
  archival and forces the in-prompt size under a shared hard ceiling (`window_ceiling_chars`)
  by truncating the largest message bodies in place, biggest-first, with a visible
  `[… elided by window clamp …]` marker — the full text stays in the transcript. Message
  count and role structure are preserved; small bodies are never touched; a degenerate window
  with no positive input budget is declined rather than zeroed.

## [0.149.0] — 2026-08-02

### Added
- **Routine group membership is now visible on the Routines page (F269, R107).** Groups (the
  ordered collections managed on the /groups page) were invisible from the routines list — a user
  looking at a routine could not see which group(s) it belonged to. The Routines dashboard now
  fetches `/api/groups` and renders a group chip on each routine's card AND its list-view row (a
  cool-accent `⛓ <group name>` chip linking to the Groups page), so group membership is
  discoverable straight from the routines list rather than only the separate /groups page. The
  groups fetch degrades gracefully — a groups-endpoint hiccup shows "no groups" and never blanks
  the routines list. Guarded by a `tests/ui` flow test asserting the chip renders in both views
  and navigates to /groups.

## [0.148.5] — 2026-08-02

### Fixed
- **Conversation context overflow RECURRED after 0.148.1 — the output reservation now keeps a
  safety margin (F265).** The 0.148.1 fix reserved exactly `max_tokens × 4` chars of output room,
  leaving the compaction cap flush against the window. But `CHARS_PER_TOKEN = 4` is optimistic —
  real tokenizers pack denser — so a prompt whose char count sat under the cap still counted more
  real tokens than budgeted. Conversation `c-20260802-110156` failed again on 2026-08-02 (AFTER
  0.148.1 shipped and the daemon relaunched onto it): 49326 real input tokens + 16384 output =
  65710 > the 65536-token window, a ~174-token miss. `input_cap_chars` now also subtracts an
  `OUTPUT_RESERVE_SAFETY = 0.05` (5%-of-window) margin, so compaction fires early enough that
  input + output clears the window even when the char→token estimate undershoots or one
  observation grows the prompt between per-turn checks. Small-window models only — for large
  windows the fraction trigger stays binding, so their behaviour is unchanged. New regression
  tests model the observed ~3.9-chars/token real pack density and assert the current cap survives
  it while the old zero-margin cap would have overflowed.

## [0.148.4] — 2026-08-02

### Fixed
- **A blocking approval no longer shows twice in a conversation (F264).** A conversation
  renders a pending question on two surfaces — inline in the chat transcript (`chat.js`
  `questionInline`, from the `question` event) and in the pinned panel above the composer
  (`questionPanel`, from `status.json`). `questionInline` built an actionable answer form for
  **every** question mode, so a BLOCKING question (which also populates the pinned panel)
  rendered as **two actionable cards** — the operator's "approvals show up twice" report. The
  inline form is now gated to **deferred** questions only, exactly as the run view's transcript
  already does ("blocking ones stay with the panel"); a blocking question renders as static
  chat text and is answered once, in the panel. Guarded by a source-level test.

## [0.148.3] — 2026-08-02

### Added
- **Concurrent open-EventSource gauge, to diagnose the multi-run UI "freeze" (F263).** The UI
  freeze users report when ≥2 conversations/runs are open has never produced a `freeze` trace,
  even though the F218 long-task observer records main-thread stalls ≥200ms — so the freeze is
  **not CPU jank**: it is a **network stall** (browsers cap ~6 HTTP/1.1 connections per origin,
  and every EventSource holds one open for its whole life, so several live SSE tails + the global
  bus can exhaust the pool, after which every new fetch — even a navigation — stalls with no error
  and no long task, invisible to the existing observer). Added `openStreamCount()` in `api.js`
  tracking live EventSources, and stamped it into the `reconnect` (stream.js) and `freeze`
  (trace.js) trace details, so the next occurrence records how many streams were open when it
  happened — turning an unprovable suspicion into evidence a future audit can act on. Guarded by a
  static-import test. **No behaviour change yet**; the fix (multiplex run tails over the one global
  bus, or cap client EventSources) is a separate decision pending this telemetry.

## [0.148.2] — 2026-08-02

### Fixed
- **Ordered markdown lists now enumerate sequentially in chat + finish-summary rendering
  (F266 / R103).** `md.js` stripped the authored number from each `1. 2. 3.` item and emitted
  bare `<li>`, so a model that separated numbered items with blank lines (each item then its
  own single-item `<ol>`) rendered every item as "1.", and lists starting at a non-1 number
  lost their start. The parser now carries each item's authored number and stamps `<ol start=N>`
  + `<li value=N>`, so rendered numbering matches the source exactly — the way GitHub renders
  the same markdown. Guarded by a browser flow test (`tests/ui/test_flows.py`) covering both a
  contiguous list and blank-line-separated items.

## [0.148.1] — 2026-08-02

### Fixed
- **Compaction now reserves room for the model's OUTPUT, fixing context-length overflows on
  small-window models (F265).** The prompt-size compaction gate capped only the INPUT at
  `fraction × context_chars` (0.6 uncached / 0.8 cached) and never subtracted the output
  reservation the request sends (`max_tokens`). Because a provider counts prompt **and** the
  requested output against ONE window, a small-window model could let input grow to
  `fraction × window` and still request the full output on top — so `input + max_tokens >
  window` and the completion hard-failed with `context_length_exceeded` (HTTP 400). Seen live:
  conversation `c-20260802-110156` on a 65536-token nano-gpt model reached ~49k input tokens
  and still requested 16384 output → repeated 400s that failed the run. The cap is now the
  lower of the fraction trigger and `window − max_tokens`, extracted into a pure, unit-tested
  `history.input_cap_chars` helper. The reservation only bites models whose window is small
  enough that `fraction × window + max_tokens×4 > window`; large-window models (Claude, etc.)
  keep the fraction trigger as the binding one, so their behaviour is unchanged.

## [0.148.0] — 2026-08-01

### Fixed
- **Answering an already-resolved question settles gently instead of erroring (F259).** A
  question answered on one surface (or expired, or overtaken as its run moved on) left stale
  answer cards on other open surfaces still showing an actionable button. Clicking it hit the
  backend's correct `404 no open question`, which the shared `answerForm` surfaced as a **red
  error toast** (and logged a UI-friction trace event) while **re-enabling the buttons** — inviting
  a second doomed click. Both answer paths (the access-request decision buttons and the
  text/option submit) now treat a 404 as the benign "already answered elsewhere" end-state: a
  plain notice + settling the card via the host's `onSuccess`, no error toast, no re-enabled
  action. Fixed once in the one shared component, so every answer surface (Decisions page, run
  view, conversation, transcript inline) benefits. Evidence: `.ui-traces/20260801.jsonl`
  20:53:44Z `no open question 'q-20260801-202218-5'`. Guarded by
  `tests/ui/test_flows.py::test_answering_an_already_resolved_question_settles_gently`.

## [0.147.1] — 2026-08-01

### Changed
- **pytest now names flaky reruns in the summary (`-rR` in `addopts`).** The browser UI suite's
  `flaky()` wiring absorbs xdist timing flakiness by rerunning, but the rerun was only *counted*
  (`N rerun`), never *named* — self-audit watched an unnamed self-recovered rerun across four
  consecutive runs with no way to identify the culprit test. Adding `-rR` makes every future run
  print `RERUN <nodeid>`, so an intermittent flake is diagnosable and de-flakeable instead of an
  indefinite watch. Verified `-rR` reports the node id under `-n auto` (xdist), not just serially.
  A guard test (`tests/ui/test_flaky_wiring.py::test_addopts_names_reruns`) fails if a future edit
  drops the flag.

## [0.147.0] — 2026-08-01

### Changed
- **Settings sections now share the `settingsSection` primitive (operator-selected D64 / A',
  the next D57 interface-quality increment).** The Settings page hand-rolled each section header
  (`<h2 id="sec-…">` + `<p class="set-desc">`) while routine config and the new-conversation
  composer built theirs through the shared `settingsSection` primitive — three surfaces, two ways.
  `settingsSection` now accepts an optional section id (`settingsSection({ title, id }, desc, …)`)
  that stamps the heading as `<h2 id="sec-{id}">` — the anchor the Settings side-nav, deep links
  (`#/settings?section=<id>`) and TOC jump to — and gained a **header mode** (no body rows → just
  the heading + one `p.set-desc` description line) for the Settings sub-views, which append their
  own panels. The Settings page now builds every section header through the primitive, so all three
  surfaces construct a section the one canonical way. Also fixes the Source section's contradictory
  description (it said the code is "pulled" there, but the section sets the **push** target for the
  self-audit routine's autonomous commits). DOM-preserving: the `.set-desc`/`#sec-id`/deep-link
  contract the UI flow tests assert is unchanged.

## [0.146.0] — 2026-08-01

### Added
- **Admin conversation — the UI affordance + rotation runbook (operator-selected D63: 1A + 2A).**
  The D62 admin backend shipped without a web control (an operator had to send the `x-admin-token`
  header by hand). D63-1A adds an **Admin toggle** to every conversation composer: click it, paste
  the admin token (stored in the browser **session** only, never on the server), and it reads
  **admin: on** in red while armed and sends the token with each message. The server re-checks the
  token on every request and, on a match, drops the one-shot per-leg admin marker — so a resumed
  conversation runs with capability gating lifted. The conversation `POST /message` endpoint now
  honours `x-admin-token` (the same web-layer-only check as `/runs/{id}/converse`); a stale marker
  is cleared if the wake fails, so it can never grant admin to a later tokenless resume. `apiUpload`
  gained an optional extra-headers argument. D63-2A adds `docs/admin.md` — a full admin guide with a
  **manual token-rotation runbook** (rotate the `RSCHED_ADMIN_TOKEN` secret + restart; clear it to
  revoke instance-wide). Tests: an endpoint-level admin-resume test (the first endpoint coverage of
  the admin flow, closing a D62 gap) + a browser UI flow test driving the toggle end to end.

## [0.145.0] — 2026-08-01

### Added
- **Admin conversation — a token-gated bypass of the capability layer (operator request, D62).**
  A conversation leg resumed with a valid `RSCHED_ADMIN_TOKEN` (a new instance secret, sent in the
  `x-admin-token` header ALONGSIDE the normal bearer, compared constant-time and fail-closed in the
  web layer only) runs with capability gating LIFTED: every gated action kind and reserved util is
  available to that leg. The token NEVER reaches the engine — the web layer validates it and drops a
  one-shot marker (`engine/admin.py`, modeled on the `revise.py` recipe-unlock), read once at loop
  init and cleared, so the unlock covers exactly that leg, is never persisted to `routine.yaml`, and
  is never inherited by a sub-workflow. **Structural / ownership gates still hold under admin** —
  `runs/` stays engine-owned and read-only, `routine.yaml` config stays the user's, the routine's own
  recipe stays sealed, and the conversation-only kinds (create_routine/manage_group/detach) stay
  conversation-only. Every action taken under admin appends one line to
  `<routines_home>/.control/admin-audit.jsonl` (run_id, kind, brief), and the system prompt carries an
  unmistakable **ADMIN CONVERSATION** banner so the run knows its gating is lifted. Admin is available
  only to a root conversation. `+engine/admin.py`, `tests/test_admin.py` (4 tests) +
  `test_grants.py::test_admin_lifts_capability_gating_only`; wiring in `grants.py` (GrantPolicy `admin`
  field + `allows_kind`/`deny` short-circuits over the capability gates only), `engine/loop.py`
  (marker read + audit hook), `engine/composer.py` (banner), `web/api_runs.py` (token check + marker).
  Open follow-ups for the operator: a UI affordance/banner on the Conversations page, and whether the
  admin token should rotate/expire.

## [0.144.0] — 2026-08-01

### Added
- **`manage_group` action — routine GROUP management from a conversation (operator request, D61).**
  A new action kind whose `verb` (list/create/update/delete/set-default/run) drives every
  operation over the same `rsched.groups` store the `/groups` web page uses (one source of truth),
  with member slugs validated against the live registry. Like `create_routine` it is valid ONLY
  from a root conversation (surfaced by the engine, backstopped by the handler) — a scheduled
  routine or a within-reply child cannot manage groups that fire other routines. The `/groups`
  subpage stays; this is the same surface reachable from chat.

### Fixed
- **Docs drift after the wizard removal (self-audit F258).** `docs/architecture.md` still described
  the standalone new-routine wizard (`components/setuppanel.js`, `views/new-routine.js`, the
  `#/new-routine` view) that D59 deleted; rewritten to describe conversation-only creation via the
  `create_routine` action reusing `workflows.scaffold`. Clears the codemap `doc_stale` flag (2 → 0).

## [0.143.0] — 2026-08-01

### Changed
- **Conversations is now the landing page and the first nav item (operator request).** Opening
  the console at its root (empty hash) now shows the Conversations view — the place routine
  creation and ad-hoc work begin — instead of the Routines dashboard. The Routines dashboard
  moved to its own `#/routines` route (the nav "Routines" link, routine/run breadcrumbs, the
  post-archive redirect and the brand→home all updated accordingly); `#/conversations` still
  works as before. Nav order now leads with **Conversations**, then Decisions · Summary ·
  Routines · Groups · … A new `tests/ui` flow test pins the landing behaviour, the active-nav
  highlight and the nav order; the dashboard UI tests were repointed at `#/routines`.

## [0.142.0] — 2026-08-01

### Removed
- **The standalone new-routine wizard — retired (D59, operator-selected big-bang).** Routine
  creation is now done entirely from a conversation via the `create_routine` action (shipped in
  0.141.0), so the parallel wizard surface is gone: the `/api/wizard/*` endpoints and their
  modules (`web/api_wizard.py`, `web/wizard_sessions.py`, `web/wizard_common.py`), the
  `#/new-routine` page (`static/views/new-routine.js`) and its setup panel
  (`static/components/setuppanel.js`), the persistent in-flight "Routine setup in progress"
  banner, and the `+ new routine` topbar button. The in-flight-build drain machinery went with
  it: `Scheduler.wizard_builds`, `restart.clarify_states()`, the `builds_active` parameter of
  `restart.restart_action()`, and the startup orphan-build reconcile — no web-process build tasks
  exist anymore, so `restart_action` is now purely `(requested, active_states, draining)`.
- Dead helpers removed with the wizard: `workflows.suggest.suggest_tags` / `TAGS_SCHEMA` (only the
  wizard's suggest step called them) and the session machinery in `web/wizard_store.py`
  (`create_session`, `candidate_patterns`, `recover_orphan_builds`, `template_defaults`,
  `WIZARD_BUDGETS`, snapshot/list/archive). `wizard_store.py` is trimmed to the clarification
  **template** support the survivors still import (`TEMPLATE_SLUG`, `read_meta`, `latest_run_ts`,
  `clarify_run_dir`, `clarify_run_id`, `session_inbox_dir`).

### Notes
- The protected `clarification` template routine and its run-page question surfacing (a clarify
  run's blocking questions on `/api/questions`) are **kept** — they remain the substrate the
  conversation-driven clarification flow uses. Only the standalone-wizard shell was removed.

## [0.141.0] — 2026-08-01

### Added
- **Routine creation from a conversation — the `create_routine` action (D58).** A conversation
  can now graduate the work it just clarified with the user into a real scheduled routine, via a
  new conversation-only action kind: `target` (the new kebab-case slug), `name`, `prompt` (the
  clarified task, decomposed into the routine's stages) and an optional `workflow` (default
  `general-task`). The handler (`engine/create_routine.py`) reuses the existing
  `workflows.scaffold` materializer — decompose the chosen workflow into the routine's own
  `main.md` + `stages/`, adapt its traits, write `routine.yaml`, init the auto-push git repo —
  so there is exactly one creation path. The daemon's registry rescan picks the new dir up on
  its own timer; no new daemon manager is needed. **Structurally gated to a root conversation**
  (mirrors `detach`): the engine only surfaces the kind to a conversation (a `loop.allowed_tools`
  injection), and the handler rejects every non-conversation as a backstop, so a scheduled
  routine never sees it. Wired end to end — schema + per-kind validation (`actions.py`), loop
  dispatch, observation rendering, the CAPABILITIES + harness-contract surfaces, and a
  finish-guard claim token so a finish claiming a routine was created must be backed by the
  action. This is the first increment of the operator's directive to make conversation-initiated
  creation the ONLY path; the standalone new-routine wizard page is retired in a later increment.

## [0.140.2] — 2026-08-01

### Fixed
- **Dropped a dead pre-0.49 backwards-compat branch in the search indexer (F254).**
  `search/sources.py` walked `("stages", "steps", "traits")` for recipe files, where `steps/`
  was the stage-directory name before 0.49. No code path has created a `steps/` directory
  since (`scaffold`/`adapt` only ever write `stages/`, and `RECIPE_PREFIXES` is
  `main.md`/`stages/`/`traits/`/`tuning.yaml`), so the literal was a fallback for a layout that
  no longer exists — removed per the repo's no-backwards-compatibility rule.
- **Removed a duplicated `_routine_dir` helper in the web layer (F255).** `web/api_items.py`
  carried a byte-identical copy of `web/api_audit.py`'s `_routine_dir(request)`; since
  `api_items` already imports from `api_audit`, it now imports the single definition instead.

### Documentation
- **Corrected stale `docs/traits-permissions.md` (F256).** The doc predated `practice-library`
  becoming a routine-default permission and `read_trait` a default capability, and predated the
  `scheduling` permission: the `read_trait` note no longer calls practice-library routine-opt-in,
  the capabilities example + default line include `read_trait`, and the shipped-permissions
  table gains the `practice-library` and `scheduling` rows.

## [0.140.1] — 2026-08-01

### Fixed
- **The routine overview (and every view's live refresh) no longer freezes with stale state
  after the event stream drops (F253).** The global `/api/events` stream drives the dashboard's
  live routine-state chips, the decision badges and the run toasts via the `rsched-bus` window
  event. SSE tickets have a 60s TTL and are purged whenever the daemon restarts, but
  `static/app.js`'s `globalStream()` relied on `EventSource`'s built-in auto-reconnect, which
  reuses the SAME `?ticket=` URL — so after any drop (a self-update restart, a 60s idle timeout,
  a tab wake) the reconnect authenticated with a dead ticket and 401'd forever: the bus went
  silent, the daemon lamp stuck off, and the dashboard kept showing whatever routine states it
  last saw with no polling fallback. `globalStream()` now owns its reconnect the way
  `stream.js`/`liveTail` already does — on error it closes the dead `EventSource` and reopens
  through a fresh `sse()` call (minting a NEW ticket) under capped exponential backoff, and on
  reopen it fires one synthetic bus tick so every view re-fetches and catches up on transitions
  missed during the outage. Covered by `tests/ui/test_flows.py::test_global_stream_remints_ticket_on_reconnect`.

## [0.140.0] — 2026-08-01

### Changed
- **Every routine-config section now reads and looks the same as the conversation composer's
  settings (D57 phase 2).** `static/views/routine-config.js` hand-built each of its ~17 sections
  (Name, Description, Tags, Schedule, Triggers, Schedule once, Permissions & capabilities,
  Practice modules, Budgets, Retention, Filesystem roots, Models, Connections, Secret exposure,
  Declined access, Machines, Origin) as an ad-hoc `<h2>` + `.panel` + description block; they
  now all go through the shared `settingsSection(title, description, …body)` primitive — the
  same one the new-conversation composer adopted in phase 1 — so a setting is presented
  identically wherever it appears. Sections that previously had no explanatory copy (Triggers,
  Schedule once, Connections, Schedule) gained a one-line per-control description. The `<h2>`
  headings the side table-of-contents rides are unchanged, so the "On this page" rail and
  deep-links keep working; no config endpoint or contract was touched.

## [0.139.1] — 2026-08-01

### Fixed
- **A message sent to a finished conversation while the server is restarting is now refused
  with a clear "not saved — resend once" notice instead of being silently stranded (R81).**
  When a self-update restart is draining, `resume()`/`fire()` refuse to wake a terminal run,
  and nothing re-drives a conversation's pending inbox after relaunch (startup only recovers
  dead-pid active runs). The message endpoints (`POST /api/conversations/{slug}/message` and
  `POST /api/runs/{id}/converse`) previously filed the message to the inbox first and then
  failed the wake with a 409 that read as total failure — so the operator blind-resent the
  same text (an observed 6× pile-up of duplicates). They now check the drain state **before**
  filing and, for a terminal/new conversation, return a `503` saying the message was NOT saved
  and to resend once after the restart; a live (mid-run) message is unaffected (the in-flight
  run drains it at its next turn boundary).
- **Finish summaries whose newlines were double-escaped now render as real line breaks (R82).**
  A finish `summary` (or `say` / report detail) is authored as JSON, where a line break is
  `\n`; a model that emits `\\n` yields a literal backslash-n that renders verbatim in the
  console, the dashboard last-outcome and the next run's digest. `_finish_run` now normalizes
  the unambiguous wholesale case — literal `\n`/`\t` escapes with no real newline anywhere — to
  the real characters, while leaving intentional literal `\n` (in text that already has real
  newlines, e.g. a code snippet) untouched.

### Testing
- Hardened `tests/ui/test_flows.py::test_conversation_composer`, which flaked under xdist load
  and could red self-audit's own nightly gate (F250): it asserted a follow-up landed after
  merely waiting for *any* toast, then read the inbox — under load a lingering earlier toast
  satisfied the wait and the inbox was read before the send round-trip persisted. It now polls
  for the inbox file with the existing `_wait_until` disk-persist helper.

## [0.139.0] — 2026-08-01

### Changed
- **The Settings page is now grouped by what you're configuring, so it teaches a cognitive
  model (F248).** Its ten sections were a single flat strip of nav chips with bare headings;
  they are now organised into four labelled categories — **Intelligence** (LLM endpoints),
  **Connections** (GitHub, OAuth connections, remote machines), **Code & library** (the source
  and library repositories and their sync), and **This instance** (secrets, server, notifications)
  — each introduced by a one-line blurb explaining why those sections belong together. Every
  section now carries a plain, reader-side description of what it controls, and the section nav
  is grouped to mirror the page. Presentation only: every section keeps its stable `sec-<id>`
  anchor, the `?section=<id>` deep link, and side-TOC compatibility, and no settings endpoint or
  contract changed. Operator-directed via an AUDIT note; built with the interface-design and
  interface-copy practice modules.

## [0.138.0] — 2026-08-01

### Changed
- **New-conversation setup now shows its settings as clearly-titled sections (D57 phase 1, F244).**
  The pre-start settings on the New conversation screen — Model, Project directory, Budgets,
  Deliberation, and Permissions & capabilities — were previously collapsed behind a single
  `⚙ capabilities & budgets` disclosure with terse copy. They are now laid out as titled
  sections, each with a one-line description of what it does, using the same section vocabulary
  the routine detail page uses (new shared `static/components/settings-section.js` primitive —
  an `<h2>` + panel + description block both surfaces compose from). No change to what a
  conversation submits at creation; this is the presentation foundation the routine-page
  restructure and the remaining conversation settings (practice modules, filesystem roots,
  connections at create time) build on next.

## [0.137.1] — 2026-08-01

### Fixed
- **Dashboard list view now marks a running routine, like the card view does (F247).** In the
  card grid a routine with a run in flight gets a mint left-edge accent (`.card.live`); the
  sortable list/table view omitted any running marker — its row only ever showed the amber
  "waiting on a question" treatment, so a routine that was actively running looked idle in the
  table. The row now carries the same `live` class and shows a matching mint left-edge accent
  and tint (a static accent rather than the card's pulse, to keep a full column of running rows
  calm).

## [0.137.0] — 2026-07-31

### Added
- **Routine groups — "Run now" arming surface (D53 Phase B, complete).** Groups can now be
  fired from the UI: a **Run now** button on each group card (Groups page, `#/groups`) POSTs the
  new `POST /api/groups/{id}/run` endpoint, which arms a sequential fire via `group_runs.arm`
  (resolving the `on_failure` policy and snapshotting the member list at arm time). The endpoint
  404s an unknown group, 400s a memberless group, and 409s a group that already has a chain in
  flight (one chain at a time). `GET /api/groups` now returns an `in_flight` map so the page
  shows a running chain's progress (`running N/M · <member>`) and disables Run now while a chain
  is live. With the 0.136.0 engine this closes D53 Phase B: create/order a group, press Run now,
  and the daemon runs its members back-to-back honouring the stop/continue policy. Tests:
  `test_api_run_group_arms_a_chain` + the Groups UI flow now drives Run now end-to-end.

## [0.136.0] — 2026-07-31

### Added
- **Routine groups — sequential-fire engine (D53 Phase B, daemon half).** A group can now be
  fired as an ordered chain: its members run **back-to-back**, each starting only once the
  previous member reaches a terminal state. New in-flight store `rsched.group_runs` keeps a
  chain's cross-tick progress in `<routines_home>/.control/group-runs/<group_id>.json`
  (≤ one in-flight run per group; the member list + resolved `on_failure` are **snapshotted at
  arm time**, so editing or deleting the group definition mid-chain never changes a run already
  in flight). New daemon manager `daemon/group_runs.GroupRunManager`, ticked from the Scheduler
  beside the trigger/one-shot managers (and paused with them under the global scheduling pause),
  advances each armed chain **one transition per tick**: fire the member at the cursor, wait for
  it to terminate, record its result, then apply the `on_failure` policy — `stop` halts the chain
  on any non-`ok` outcome (a budget-exhausted `partial` counts as a failure), `continue` fires the
  remaining members regardless. A member that is missing, disabled, or crashed without finishing
  is recorded as a failure so a broken member never hangs the chain. Run spawning still honours
  one-run-per-routine, `max_concurrent_runs` and the restart drain, exactly as for cron fires.
  The **arming surface** (a "run group now" API endpoint + a UI trigger on the Groups page) is
  the next increment; this commit is the engine that executes an armed chain.

## [0.135.0] — 2026-07-31

### Added
- **Routine groups — CRUD page + nav (D53 Phase A, UI half).** A new **Groups** page
  (`#/groups`, in the top nav beside Routines) over the `api_groups` surface: set the
  instance-wide mid-chain-failure default, create a group with a routine-member picker, reorder
  a group's members with ↑/↓ (the order sequential-fire will use), set a per-group `on_failure`
  override or inherit the default, and delete a group. The page states plainly that
  **sequential firing is not live yet** (Phase B) — it is the setup surface, and nothing is
  grouped until the operator adds a group. Covered by a real-browser flow test
  (`tests/ui/test_groups.py`): render → add with a member → persist to `.control/groups.json` →
  change the default → delete.

## [0.134.0] — 2026-07-31

### Added
- **Routine groups — store + CRUD API (D53 Phase A).** A group is a named, ORDERED list of
  routine slugs plus a mid-chain-failure policy (`stop` = abort the rest of the chain, or
  `continue`), with an instance-wide default and an optional per-group override. New
  `rsched.groups` module persists the whole store atomically in one daemon-owned document,
  `<routines_home>/.control/groups.json` (the same dot-dir ownership model as triggers and
  schedule-once — instance operator state the web layer writes and a future daemon reads, so
  it cannot live in any routine.yaml). New `web.api_groups` CRUD surface: `GET /api/groups`
  (default + vocab + groups + the routine picker), `POST /api/groups`, `PATCH /api/groups/{id}`
  (rename / reorder members / set the on_failure override), `DELETE /api/groups/{id}`, and
  `PUT /api/groups/default`. Every member slug is validated against the live registry, so a
  group can never name a routine that does not exist. This is the store + API only —
  **sequential-fire is Phase B** (a later increment that reads this store on the daemon tick);
  nothing fires yet, and no routines are grouped by default.

## [0.133.2] — 2026-07-31

### Fixed
- **Dashboard search/sort controls were destroyed by live refreshes while routines ran
  (F229 — "UI non-responsive with >1 routine running").** Every live bus event refreshes the
  Routines dashboard (debounced to ~600ms), and each refresh called `renderFilterBar()`, which
  `replaceChildren()`s the filter bar — tearing down and recreating the search `<input>` and
  the sort `<select>`. With one or more routines active the bus streams events continuously, so
  a user's focus and half-typed search text were wiped roughly twice a second, making the view
  feel unresponsive. The filter bar now rebuilds ONLY when the available tag set actually
  changes (tracked by a signature); the card/table body still re-renders on every refresh. New
  UI test `test_dashboard_live_refresh_preserves_search_focus` types into the search box,
  dispatches a live `rsched-bus` event, and asserts the same input keeps focus and its value
  across the refresh.

### Fixed
- **`write_util` selftest failure diagnostics were incomplete (F226, R47/R60).** Two gaps
  made a failing util selftest harder to fix than it should be: (1) on a **selftest timeout**,
  `utils_lib.run_util` discarded the stdout/stderr captured *before* the process-group kill
  and returned only a bare "timed out" note — losing exactly the diagnostic output that
  explains why the script hung; it now returns the partial stdout plus stderr with the
  timeout note appended. (2) `utils_lib.selftest` collapsed a failure to `(err or out)`,
  which **dropped the exit code** and **hid stdout whenever stderr was non-empty** — so a
  script that printed its failure detail to stdout and a bare traceback to stderr lost the
  detail. A failed selftest now surfaces the **exit code plus both labelled streams**. No
  change to the passing path or the rollback behaviour.

## [0.133.0] — 2026-07-31

### Added
- **`util name=search args=["<keywords>"]` — keyword tool discovery (D52 Phase 3).** A run can
  now name what it needs and get the handful of most-relevant utils (name + summary), then fetch
  exact flags with `util name=list args=["<name>"]` — the two-phase discovery pattern
  (Anthropic Tool Search / BM25 two-phase) sized for this catalog. The ranker
  (`utils_lib.search_utils`) is a PURE in-process keyword scorer over the live catalog
  (name▸tags▸summary▸usage, weighted), deliberately NOT the daemon-owned prose FTS5 index
  (`search/index.py`) — engine subprocesses never import that, so a `util` verb must not depend
  on it. Every result (including a zero-match query) names the always-on CAPABILITIES catalog as
  a floor, so a retrieval miss never fully hides a tool — the dominant failure mode of any
  tool-search layer. The always-on catalog header now advertises the verb, and the search
  observation renders under its own query-labelled header. Completes the D52 discovery arc after
  Phase 1 (grouped catalog, 0.127.0); Phase 2 (library consolidation) remains with
  global-utils-review.

## [0.132.1] — 2026-07-31

### Fixed
- **Run/conversation composer input still shared the control row on narrow screens (F238
  regression)**: the run-view message input was created with an inline `style="flex:1"`, and an
  inline style always beats a stylesheet selector — so the `@media (max-width:860px)` rule that
  was supposed to break the input onto its own full-width line (`.composer > input[type=text]
  { flex: 1 1 100% }`, added for F238 in 0.130.0) never took effect, and the input stayed
  squished inline beside the send/attach buttons and the "editable recipe" checkbox (the
  composer is shown on conversation runs viewed through the run page too). Fix: removed the
  inline flex from the input and moved it into `base.css` (`.composer > input[type=text]
  { flex: 1 1 auto }` on wide screens; the existing `flex: 1 1 100%` under 860px now wins), so
  the stylesheet governs the width and the media rule can override it. The F238 UI test was
  strengthened from a class-existence check to a real narrow-viewport layout assertion (input
  spans the full row width; send button wraps beneath it) that fails on the inline-flex bug."

## [0.132.0] — 2026-07-31

### Added
- **Conversations can bind OAuth connections (D55, closes R70)**: the Connections card — bind a
  Google/Notion/… account per provider so its access token is injected into connector utils
  (`google-api`, `notion`) — is now on the conversation header, not only routine pages. A user
  hit this exact wall (R70): they asked a conversation to read their Google Contacts and
  `google-api` failed for want of `GOOGLE_ACCESS_TOKEN`, but there was no Connections card to
  bind one. A conversation is routine-shaped, so the engine already injects the token from
  `routine.yaml connections:` — the only gap was the config surface. `PATCH /api/conversations/{slug}`
  now accepts `connections` (same validation as routines), the detail response returns it, and
  the card is a new **shared component** `static/components/connections.js` extracted from the
  routine config page (one implementation, both surfaces).

## [0.131.0] — 2026-07-31

### Added
- **Working-plan strip on the run view (D54)**: a run's living decomposition (`state/plan.md`)
  — the same store the engine already inlines into the prompt (`engine/composer.py`) — is now
  surfaced as an always-visible collapsible strip at the top of every run view, rendered as
  markdown. So "where is this run in its own plan" is answerable at a glance, not only from the
  transcript. Home-agnostic: it works for a scheduled routine run, a conversation, or a
  detached task alike (keyed by run id). It refreshes on phase transitions to track the run's
  edits, and hides itself entirely when the run keeps no plan (a scheduled routine whose spine
  is its compiled recipe, or a plan deleted at finish) — no empty box. New read endpoint
  `GET /api/runs/{run_id}/plan` and component `static/components/planstrip.js`; no schema or
  event-contract change (it reuses the existing plan store).

## [0.130.0] — 2026-07-31

### Fixed
- **Run-page composer: dropped the dead mode dropdown and gave the input its own line on
  narrow screens (F237, F238)**: the end-of-run composer had a vestigial single-option,
  disabled `<select>` ("→ continue this run") left behind when 0.128.0 (F233) removed the
  "queue for next run" mode — the message destination is fully implied by run state (a live
  run injects, a terminal run continues the run), so the `<select>` is gone entirely and the
  send path derives the mode from state (F237). On narrow viewports the message input no
  longer stays squished inline beside the send/attach buttons and the "editable recipe"
  checkbox: the composer row carries its own `composer` class and, below the 860px mobile
  breakpoint, the input takes its own full-width line (`flex: 1 1 100%`) with the controls
  wrapping beneath it (F238). UI-gated in `tests/ui/test_flows.py`.

## [0.129.0] — 2026-07-31

### Added
- **Installable PWA so decision notifications reach a phone (operator request)**: the console
  now ships a web app manifest (`static/manifest.webmanifest`, `display: standalone`) served
  at `/manifest.webmanifest`, linked from `index.html` alongside the apple-mobile-web-app meta
  tags and an apple-touch-icon (`static/icon.svg` + a maskable `static/icon-maskable.svg`).
  The full Web Push decision-notification pipeline already existed (VAPID keys, per-browser
  subscription store, `push.notify_new_decisions` driven off the daemon event bus, Settings →
  Notifications opt-in) — but **iOS Safari delivers Web Push only to a site installed to the
  Home Screen as a PWA**, which needs a linked manifest; without it, "notifications on my
  phone" was impossible on iPhone/iPad and installability was degraded on Android too. The
  Notifications settings panel now also shows an "Add to Home Screen" hint on iOS when the
  console is not yet installed. No change to the push protocol or the decision source of truth.

## [0.128.0] — 2026-07-31

### Changed
- **End-of-run input always continues THIS run; the next-run message queue moved to the
  routine page (F233)**: the run page's end-of-run composer no longer offers a
  "→ queue for next run" mode — a terminal run's input is solely for continuing that run in
  place (the mode selector is now single-option and disabled). The "message the next run"
  affordance now lives on the **routine details page** as a "Message the next run" composer,
  bound to the routine rather than a specific run: it queues a free-text note in the routine's
  inbox (`POST /api/routines/{slug}/message`) that the routine's NEXT run — scheduled or
  fired with ▶ run now — drains at boot. Hidden for the protected clarification template.

### Added
- **`POST /api/routines/{slug}/message`**: queue a free-text message for a routine's next
  run (files into `<routine>/inbox/` via the engine's `file_message`). Guards the protected
  template and rejects empty text.

### Changed
- **Grouped util catalog in the CAPABILITIES prompt (D52 Phase 1)**: the always-on util
  list is no longer a flat, alphabetical 90+-line dump — it is grouped under ~14 labelled
  domain categories (Jobs & freelance, Email & messaging, Documents & PDF, Code & development,
  …), each with a `### <Category> (N)` heading and per-group count. Every util's one-line
  summary stays visible under its group; nothing is hidden. Each util is filed under the
  FIRST category whose keyword set intersects its `tags:` (a controlled vocabulary defined
  in `engine/capabilities.py`, order-based collision resolution — e.g. `google-api`,
  `health-events` and `service-logs` do not mis-file under "Health & fitness"). This targets
  the measured real cost of catalog growth — *discovery difficulty*, not the small (~2k token)
  always-on size — improving tool-selection accuracy without a new dependency. First phase of
  the operator-approved util-proliferation plan; later phases (consolidation, `util name=search`
  over FTS5, a soft dedup hint at util creation) follow.

### Added
- **Optional-secret marker `secrets: NAME?` (D51)**: a util may mark a declared secret
  optional with a trailing `?` (e.g. `secrets: FOO_KEY, BAR_TOKEN?`). It is injected when
  the store has it (same declared-only rule) but the Settings → Secrets page no longer
  prompts for it — an unset optional secret reads a calm muted "optional" instead of the
  amber "unset" nag. `parse_header` strips the marker into `secrets` (so injection and the
  undeclared-read gate are unchanged) and collects the marked names in `optional_secrets`;
  `/settings/secrets` flags a needed key `optional` only when EVERY declaring util marks it
  so. Answers the operator's D51 decision (inject-if-present, no prompt when unused).

### Changed
- **Bare URLs autolink in model/user prose (F228)**: the markdown renderer already turned
  `[text](url)` into new-tab anchors; now a BARE `https://…`/`http://…` URL in a summary,
  answer, injection or LLM reply is also linked (`target="_blank" rel="noopener noreferrer"`),
  so a pasted link is clickable. Existing anchors and code spans are protected; trailing
  sentence punctuation stays outside the link.
- **Week-schedule "now" cursor advances on its own (F230)**: the dashboard "this week"
  strip's green now-line (and the past/future bar dimming) re-positions itself every 30s
  between data refreshes, so an idle dashboard tracks real time instead of freezing at
  load-time. The interval re-renders from the last data (only `Date.now()` moves) and
  self-clears when the grid unmounts.

### Fixed
- **`edit_file` near-miss hint on an ambiguous character (F232)**: when an anchor almost
  matches but differs on an invisible/ambiguous character — a non-ASCII dash (— vs -),
  NBSP, tab-vs-spaces, trailing whitespace — the "anchor not found" error now names the
  closest ACTUAL line via `repr()`, so the caller sees the exact bytes to copy instead of
  guessing across turns (a real case cost ~6 turns this run: `read_file` renders such
  characters escaped, so copying the displayed anchor silently differed from the file bytes).

### Removed
- **Dead duplicate `playbooks.doc_body` (F231)**: byte-identical to the canonical
  `library_docs.doc_body`; all 8 call sites use the latter and nothing referenced the
  playbooks copy. Deleted under the repo's no-back-compat rule (deep hygiene sweep finding).

## [0.125.0] — 2026-07-30

### Fixed
- **Red baseline repaired (F221)**: `tests/test_utils.py::_ctx` test double lacked the
  `read_roots`/`write_roots`/`granted_now`/`grant_args` surface that 0.124.0's grants
  work added to `sandbox.policy_for_ctx` + `executor._extra_secrets`, crashing
  `test_failed_util_teaches_repair_and_keeps_trace_tail` with `AttributeError`. It shipped
  red because `test_utils.py` is `skipif(uv is None)` and the commit env lacks `uv` — the
  same blind spot that hid the 0.116.0 regression.
- **`write_util` selftest can install `net: none` deps (F223, R40)**: a util's `net:`
  declaration governs its RUNTIME, not the one-time build-time PEP 723 dependency install.
  `utils_lib.run_util` now prewarms deps in a network-open, still-filesystem-jailed
  `uv sync --script` before the net-policed run, so a `net: none` util can fetch its deps
  yet stay TCP-denied at execution — authors no longer must mis-declare `net: outbound`.

### Changed
- **Util STDOUT truncates tail-only, not mid (F224, R45)**: oversized util-observation
  STDOUT — which is spilled in full to `.util_outputs/` — now keeps the head and drops the
  tail (`truncate(keep="head")`), with a marker naming the resume offset, so the reader
  continues IN SEQUENCE from the spill file. Failure STDERR keeps head+tail (the
  traceback's END is the repair material).
- **Sub-workflow capability denials name the child scope (F225a, R46)**: a spawned/subtask
  child runs with capabilities off by design; a gated-kind denial (e.g. `write_util`) now
  says it is unavailable to the child sub-workflow and routes the work to the PARENT,
  instead of falsely claiming the routine lacks the capability.
- **Transcript turn timestamp stacks under the turn count (F222)**: the run/transcript
  view puts the message timestamp beneath the turn number (`.turnmeta` column) rather than
  beside it, reclaiming horizontal space for the say text (operator AUDIT note).

## [0.124.1] — 2026-07-29

### Removed
- **`bootstrap.migrate_secret_grants` deleted after convergence** (one-shot
  `secret_grants:` → `grants:` rename from 0.124.0): the pass ran on the production
  instance on 2026-07-29 (19 routine.yaml files rewritten, no legacy keys remain across
  the three data homes), so per policy the function, its daemon-boot call and its test
  are gone.

## [0.124.0] — 2026-07-29

### Added
- **Unified access grants — the four-state model** (`entities.py`, `engine/requests.py`,
  `web/grants_apply.py`; docs/traits-permissions.md): every grantable thing gets ONE
  namespaced entity id (`action:` gated kinds · `util:` reserved utils · `secret:` ·
  `connection:` · `machine:` · `fs-read:`/`fs-write:` · `runs:` · `workflows:` ·
  `recreate:` deleted-util unlocks), and every denial now routes to a typed ACCESS
  REQUEST: `ask_user` gains an optional `request: "<entity-id>"` field, the record
  renders on the Decisions page as four buttons — **allow now · allow forever · deny now
  · never** — and the decision applies mechanically. Allow-forever lands in the entity's
  NATIVE routine.yaml key (capabilities via the same raise-then-floor cascade as the
  permissions editor, bindings, fs roots; `secret:` rows in the new `grants:` mapping);
  **never** writes a `grants:` deny tombstone (the run stops asking — denials switch to
  "permanently declined", the catalog badges the util, the routine page's new *Declined
  access* panel un-declines); now-decisions live in-memory on the run (a resumed leg
  re-asks) and reach all three enforcers — `validate_action`, the util sandbox's
  filesystem roots (`sandbox.policy_for_ctx`), and the declared-only env injection
  (once-granted connections/machines/secrets flow like bound ones, for that run). A
  mid-run allow re-projects the transport schema at the turn boundary, so a granted kind
  is generatable on the very next turn; a decision made between runs is consumed at the
  next boot, before the prompt is composed. Requests are validated inside the
  schema-retry cycle (`requests.request_denial` — bad ids, already-enabled entities,
  unknown providers/machines/secrets, credential-store fs paths, sub-workflow requests
  are corrected at zero turn cost).

### Changed
- **Config-writer ownership tightened**: forever-decisions are persisted by the WEB
  layer at click time — the engine now writes NO routine.yaml at all (the D39
  `record_secret_grants` engine writer is gone). Secret exposure rides the generic
  request flow: `secret_grants:` is replaced by `grants:` rows (`secret:<NAME>`), with a
  one-shot boot migration (`bootstrap.migrate_secret_grants`,
  MIGRATION expires 2026-08-29); the routine detail/PATCH API and the routine page's
  secret-exposure panel speak the new shape.
- **Never-recreate-deleted-utils** is now the `recreate:<slug>` entity — an allow-now
  decision this run unblocks the recreate; deliberately NO allow-forever, so a fresh
  deletion always outranks an old grant. The fuzzy answered-ask name-scan (and
  `RunContext.user_answers`) is gone.
- `sandbox.policy_for_run(server, routine)` → `policy_for_ctx(ctx)`: the util jail now
  compiles from the run's EFFECTIVE roots (config + one-time fs grants) — one policy
  source, two enforcers.

### Fixed
- **Schema projection mutated the global ACTION_SCHEMA**: `kindsurface.schema_for_kinds`
  filtered the ORIGINAL property specs into its deepcopy shell, so the
  description-trimming loop permanently trimmed the shared schema on every projection —
  cross-run contamination in the daemon process (a restricted run's projection thinned
  the descriptions every LATER run was shown). Now filters the copy; regression-pinned.

## [0.123.0] — 2026-07-29

### Added
- **Main-thread FREEZE telemetry** (`static/trace.js` + `web/api_traces.py`, F218 — instrumentation
  for the "UI freezes during active runs" report, N12). The console now observes the browser's
  Long Tasks API and records a `freeze` ui-trace (view + worst blocked-ms) when the main thread
  stalls ≥200ms, throttled to one report per 10s. `freeze` was added to the ui-trace ingest
  allowlist so the events persist. This gives the next audit a DIRECT freeze signal — independent
  of SSE reconnect traces, which a prior audit wrongly eyed as freeze evidence but are actually a
  network artifact of the operator's tailscale access — so a genuine stall can finally be measured
  rather than guessed at.

## [0.122.0] — 2026-07-29

### Changed
- **Dashboard "this week" strip: bar width is now a 5-run moving average of runtime**
  (`static/components/weekgrid.js`, F210 — operator request). Each fire bar's width tracks a
  routine's runtime; it was the mean over the whole heartbeat window (up to 15 runs), so a stale
  long run kept dragging the bar for many runs. It now averages only the most recent 5 runs that
  recorded a real `elapsed_s` (`recent_runs` is newest-first, so the head), and the legend tooltip
  says "over N runs". More responsive to how a routine is behaving lately.

## [0.121.0] — 2026-07-29

### Changed
- **A 429's `Retry-After` hint is now honored before failover** (`endpoints/base.py`, F220).
  Retryable 429/5xx errors already got a 3-try exponential backoff, but it ignored the server's
  explicit `Retry-After` and used a fixed 1s/2s schedule — so a provider asking for a longer pause
  (the recurring nano-gpt 429 overloads) exhausted its retries and failed over / cooled down
  prematurely. `raise_for_status` now parses a numeric `Retry-After` onto `EndpointError.retry_after`,
  and `with_retries` waits exactly that (capped at `RETRY_AFTER_CAP_S` = 30s so a hostile or absurd
  hint can't hang a run) instead of the generic exponential. No hint → the exponential schedule is
  unchanged; the test clock (`base_delay==0`) short-circuits the hint so retry-logic tests never sleep.

## [0.120.0] — 2026-07-29

### Fixed
- **Run composer: a typed draft now survives a refresh and the input clears on send**
  (`static/views/run.js`, F215). The message input had no stable persist key, so `formpersist`
  fell back to its placeholder — which mutates with mode/recipe state — and a draft saved under
  one placeholder never restored. It now carries `data-persist="run-msg"`. The `converse` branch
  also never emptied the visible input on submit (only the `inject` branch did), leaving the sent
  text on screen; it now clears immediately.
- **Dashboard table: clicking the active column header reverses the sort** (`static/views/dashboard.js`,
  F208). Re-clicking the sorted column was a no-op — the only way to see the other direction was
  to sort by another column and come back. A per-column direction (`sortDir`, persisted) now
  toggles on re-click, and the header arrow shows the current direction (▾ desc / ▴ asc).

## [0.119.0] — 2026-07-29

### Added
- **Provisioned secret NAMES are surfaced to every run** (`engine/capabilities.py`, D46 — operator
  decision, option A). The CAPABILITIES prompt section now lists the NAMES of the secrets in the
  central store (never a VALUE, no consent prompt), so a run knows up front which credentials
  exist and which are missing instead of probing with the `secret-check` util or discovering the
  gap as a mid-task "key not set" error. A util still only RECEIVES a secret it declares on its
  `secrets:` header — naming a secret here is informational and cannot leak a value.

### Fixed
- **A secret provisioned via the daemon environment now reads as SET in Settings → Secrets**
  (`web/settings/secrets.py`, F209). Presence was computed from the store file (`secrets.env`)
  ONLY, so a declared secret provisioned through `os.environ` — which `utils_lib._child_env`
  DOES inject into a declaring util — showed as "not set" though it works (the Webauthsources
  symptom). Presence is now the union of the store and `os.environ`; the store remains the only
  writable surface (os.environ is read-only presence).

## [0.118.0] — 2026-07-29

### Changed
- **Blocking / permission questions are no longer mirrored to Discord** (`engine/decisions.py`,
  D48 — operator decision). A Discord-side answer to a permission ask was observed NOT reaching the
  run: the user answered on Discord, the run kept waiting, and they had to re-answer on the web
  console (personal-weight-loss-coach webauth ask; finding F193). Until Discord's answer-ingestion
  path (poll → `question_answered`) is proven reliable end-to-end, mirroring a question there is
  worse than not — the user believes they answered while the run stalls. `mirror_blocking()` now
  returns `None` while the new module flag `MIRROR_BLOCKING_QUESTIONS` is off (its default), so
  blocking asks are answered on the web console / Decisions page only. **Outbound FYI is
  unaffected** (oauth-refresh notices, detached-task results still send via `rsched.notify`). The
  whole `DiscordMirror` machinery is retained behind the flag and can be re-enabled once two-way
  delivery is verified.

### Docs
- **`deploy/DOCKER.md`** (D49): extra host-directory bind mounts are opt-in — add them in a local
  `docker-compose.override.yml` (gitignored, auto-merged), not the tracked `docker-compose.yml`,
  and grant the path as an fs-root per routine. Keeps the shipped compose minimal (cf. R35).
- **`docs/subtasks.md`** (D50): new "Aborting or pausing a child" section — `kill n=N` aborts a
  running subtask/subrun, `wait` gathers it, a parent's `finish` reaps all children; there is
  deliberately no pause/resume (kill + re-issue covers the observed cases).

## [0.117.0] — 2026-07-29

### Fixed
- **`engine/outputs.py` — `spill()` no longer crashes the turn on a malformed context** (F211).
  0.116.0's spill store computed `base`/`rel_dir` (which call `_run_key(ctx)` → `ctx.run_dir`)
  OUTSIDE the function's try block, so a context without `run_dir` raised `AttributeError` and
  broke the whole util turn — directly violating spill's own documented "Never raises: a failed
  spill must not fail the turn" contract. The regression shipped RED (`test_utils.py::
  test_failed_util_teaches_repair_and_keeps_trace_tail`) because `test_utils.py` is
  `skipif(uv is None)` and the commit environment lacked `uv`. The setup lines are now inside the
  try and `AttributeError` is caught alongside `OSError`/`ValueError`. Full suite back to green.

### Changed
- **New-routine scaffolding: a stage-generation outage reads as degraded, not "FAILED"** (F212a,
  operator AUDIT note). When the stage-generator model is unreachable at creation (a transient
  quota/rate-limit outage — the routine is still scaffolded from the verbatim workflow pattern and
  runs fine), the routine's LEDGER said **"⚠ stage generation FAILED at creation"**, which reads as
  a broken routine. It now says **"⚠ scaffolded without generated stages"** and states plainly that
  the routine is fully functional and how to get tailored stages later. The never-silent cause line
  and the `wizard_build_degraded` health event (F183/D41/F197) are unchanged.

## [0.116.0] — 2026-07-26

### Added
- **Util output too large for its observation is saved, not destroyed** (`engine/outputs.py`). A
  util's stdout is captured up to 1 MB (`utils_lib.OUTPUT_CAP`) and then head+tail truncated to 8k
  for the observation — and the transcript records the TRUNCATED payload, so everything between
  those two caps was produced and immediately lost. The only recovery was re-running the util,
  which does not return the same data for anything non-deterministic, paid, or time-bound (a page
  fetch, an LLM subcall, a mailbox read, a quote). The full text now goes to
  `.util_outputs/<run-ts>/t<turn>-<util>.out` (`.err` for a truncated stderr).
  - **Only what was truncated is kept.** An output the observation carried whole is already in the
    transcript verbatim; a second copy would duplicate a file the system has. The store is the
    recovery of a loss, not a mirror of util traffic.
  - **The pointer rides the observation that lost the middle**, naming the exact path — the moment
    of need, so the store needs no index, a run never guesses a filename, and an untruncated call
    carries nothing extra. Reads are ordinary `read_file`, which pages by line window: a large
    output is cheaper to consult on disk than it ever was in context, where it existed only as a
    head+tail guess. The state digest lists the newest spills so a run can read what an EARLIER
    run already fetched instead of fetching it again (the only route into the prompt for those —
    this run's own pointers ride its observations).
  - **Engine-owned and read-only for the run**, like `runs/`: a run does not rewrite the record of
    what a util returned. **Gitignored on first use** — the run-end autocommit is `git add -A` and
    util output can carry tokens, so without this every spill would enter the routine's repo
    permanently and ride `git-sync` to a remote (self-healing for existing routines and
    conversations, mirroring `machines._ensure_mnt_gitignored`; new dirs get it from
    `scaffold.GITIGNORE`). Never search-indexed (the index already excludes tool observations for
    the same reason). Pruned to the last `KEEP_RUNS` (5) run dirs — a backstop against unbounded
    growth, never a promise about how long an output lives. A child run's key carries its `sub`
    path, so a subrun's restarted turn numbering cannot overwrite its parent's spill.

## [0.115.0] — 2026-07-26

### Added
- **Usenet as an opt-in capability.** A routine holding the new `usenet` permission can list
  and search newsgroups, read articles, post, and retrieve binary posts from an NZB. Same
  shape as `darknet` in 0.113.0 and for the same reason: **no engine code changed.** Which
  utils are reserved is the union of every library permission doc's `requires.utils`
  (`grants.read_library_requires`), so `library-seed/permissions/usenet.md` declaring
  `requires: {utils: [usenet, usenet-nzb]}` IS the enforcement — there is no list in the
  source to extend. The permission is not a default and is deliberately absent from
  `ADOPT_PERMISSIONS`: it reaches a routine only when the user grants it.
  - **Two utils, not one** (library, not `util-seed` — same as `darknet`). `usenet` is the
    text half: `groups` (wildmat search, or `--new-since` for newly created ones), `headers`
    (a group's overview, filtered), `article` (by `<message-id>` or group + number), `post`,
    and `check`. `usenet-nzb` is the binary half — a different job, so a different util:
    `inspect` parses an NZB offline, `fetch` runs parallel connections, decodes yEnc,
    reassembles the parts and repairs from par2. Both are **server-agnostic**: `NNTP_SERVER`
    / `NNTP_PORT` / `NNTP_USER` / `NNTP_PASS` / `NNTP_FROM` come from the Secrets store under
    the declared-var rule, implicit TLS on 563 and STARTTLS elsewhere. Free text servers
    carry no binary retention at all; that is a provider choice, not a bug.
  - **Posting is dry-run by default and needs the user's word.** Without `--go`, `post`
    prints the exact article and stops — and that dry run needs no server, so showing a human
    what is about to go out is the cheap path rather than the expensive one. The permission
    prose makes the `ask_user` confirmation explicit: an article propagates to thousands of
    servers in minutes and there is no unsend.
  - **Searching is client-side, because NNTP has no search command.** `headers` pulls one
    overview range and sifts it with `--subject` / `--from` regexes and `--since`. Overview
    subjects arrive RFC 2047-encoded and are decoded *before* filtering — otherwise every
    regex would match the encoding rather than the words — and the range carries a cap so a
    mistyped `--range` fails fast instead of pulling a provider's whole retention.
  - **Binaries are verified, not hoped for.** Per-segment CRC32 *and* the part length implied
    by `=ypart begin/end` (which the spec makes authoritative over the size in `=yend`).
    Parts are written at their own offsets into a sparse file, so parallel arrival order does
    not matter. Filenames come from the yEnc `=ybegin` header rather than the routinely
    obfuscated NZB subject, and both are reduced to a basename inside the output directory
    before anything is written. An unrepairable download is reported incomplete, its partial
    files removed unless `--keep-broken`, and the exit status says so in both output modes.
    par2 arrives as a wheel-packaged binary so a sandboxed run needs no system package, and
    repair runs over what actually landed on disk rather than the names the NZB claimed.
  - `--connections` defaults to 8 and caps at 30: providers cap concurrent connections per
    account and answer `502` past it. One connection per thread is a constraint, not a tuning
    choice — the NNTP client holds unlocked buffered reader state on a single socket.

### Fixed
- **Two timing races in `tests/test_loop.py` that made the suite flaky under load**, both
  found by a run that hung for 13 minutes instead of the usual three.
  - `test_pause_gate` published the run dir to its clearer thread BEFORE writing
    `control.json {"pause": true}`. The clearer could see the dir, write `pause: false`, and
    have the pause write land on top of it — and `pause_gate` polls forever by design (a
    paused run stays paused), so nothing ever released it and the whole suite hung rather
    than failing. The pause is now durable before the dir is published.
  - `test_ask_user_blocking_deferred_by_user` wrote its defer marker on a fixed 0.3s timer,
    racing the run's boot. When boot lost, the marker was present at boot and correctly swept
    as stale, so the test failed on the 1m blocking-timeout path. It now waits for the
    pending-question record — written when the question is asked, before the wait — so the
    marker always lands mid-wait, which is what the test is about.

### Documentation
- `docs/usenet.md` (new, on the Help tab via `GUIDE_ORDER`), plus the sweep: `README.md`,
  `CLAUDE.md`, and `docs/traits-permissions.md` (the shipped-permission table and the
  reserved-util list).

## [0.114.0] — 2026-07-26

### Changed
- **Conversations get a spine and permission to be long.** Replies came out pathologically
  short, and the cause was three things stacked — none of them fixable by raising a number.
  - **The engine no longer steals the reply.** A budget violation used to return an
    engine-authored `partial` ("Run stopped by the engine: turn budget exhausted (10)") with
    the model never told. In a conversation that string *is* what the user reads as the reply.
    The first violation now spends a one-time **reserved finish turn**: the action schema
    narrows to `finish`, one more turn is granted carrying `OBSERVATION (budget spent): …
    This is your LAST turn`, and the ending is always in the run's own words. A run can
    overrun a budget by exactly one turn; only a second violation force-finishes. This is
    universal — a scheduled routine's forced stop cost the next run its handover the same way.
  - **The per-reply budget is a backstop, not a pace.** `CONVERSATION_BUDGETS` goes 10 → 40
    turns and 30 → 60 minutes, and `max_subruns` drops to the default 8 (decomposing a heavy
    step is a normal move, not a rationed one). The old cap was read by the model at turn 1,
    so replies were short by *planning*, not truncation — the harness budget prose now says
    outright that budgets are a ceiling and the work ends when it reaches a handover point.
    The budget warning wording follows (`wind down` → `converge`, naming the reserved turn).
  - **A conversation writes its own workflow.** `state/plan.md` — the goal, the ordered steps
    with status, open decisions, what's owed by the user — authored and revised by the run
    itself, inlined in full at the top of the STATE DIGEST (capped at 60 lines; a plan that
    outgrows that belongs in `stages/`). It is the emergent counterpart to a routine's
    compiled `stages/` + `phase.json`: a scheduled run re-orients against its recipe every
    run, while a conversation had only chat scrollback and finished at the shortest possible
    bar. The `converse` pattern (version 3) gains `working_plan()`, drops the hardcoded
    "roughly 10 turns per reply", and replaces turn-counting with the checkpoint rule — reply
    when the user has something real, not at the first natural pause.
- **The state digest names delivered artifacts.** `artifacts/` was created for every
  conversation, rendered by the UI, and invisible to the run — which rebuilt or duplicated
  what it had already handed over. Now listed with sizes, plus the update-in-place contract.

### Migration
- `conversations.migrate_conversations` (`MIGRATION(expires=2026-08-31)`, run at daemon boot):
  existing conversations reach the new pattern no other way — `main.md` is materialized
  verbatim at creation and `sync_seed_library_docs` never overwrites, so both the live
  library's `converse.py` and every conversation's own copy were frozen at their creation-time
  version. Re-renders `main.md` from the seed pattern and lifts per-reply budgets off the
  retired values; each conversation's OWN `traits/` are untouched. `routine.yaml`'s
  `workflow:` block now records the pattern `version`, which is what makes it idempotent.

## [0.113.0] — 2026-07-26

### Added
- **Darknet access as an opt-in capability: one util, one permission, one container — and no
  engine code.** A routine holding the new `darknet` permission can search Tor hidden services
  and read a `.onion` page. The gate needed no source change: which utils are *reserved* is the
  union of every library permission doc's `requires.utils` (`grants.read_library_requires`), so
  `library-seed/permissions/darknet.md` declaring `requires: {utils: [darknet]}` **is** the
  enforcement. It is not a default and is deliberately absent from `ADOPT_PERMISSIONS`.
  - New `tor` compose service (`deploy/Dockerfile.tor`, `deploy/torrc`) — SOCKS5 on `tor:9050`,
    built from Debian's own `tor` package rather than a third-party proxy image, since this is the
    component that decides whether traffic is actually anonymised. **No `ports:` mapping** (that
    would be an open proxy on the LAN), with `SocksPolicy` RFC1918-accept + `reject *` behind it.
    State is the named volume `tor-data` — the one deliberate exception to
    every-data-home-is-a-bind, because Tor's guard state is regenerable and worthless on another
    host, and a named volume still survives container recreation.
  - The library `darknet` util: `search` (via the index's onion mirror, so the util stays
    `.onion`-only), `fetch` (text or HTML), and `check` (health probe; exits non-zero on a dead
    circuit in *both* output modes). `search` is a two-step exchange: the index serves a rotating
    anti-bot token as a hidden form field and 302s to nothing without it, so the form page is
    fetched first and the token sent with the query. No browser is needed — the token is plain
    HTML and the result rows are server-rendered; verified on the instance that plain HTTP and
    headless Chromium return identical result sets, despite the index's "no non-JavaScript
    version" banner (which concerns its own UI, not its search). `socks5h://` always — a `socks5://` override is upgraded,
    since `.onion` has no DNS and local resolution both leaks and cannot work. Clearnet is refused
    before the proxy is contacted, redirects are followed **manually** so a `Location:` header
    cannot walk the request off `.onion`, and there is **no direct-connection fallback**: if the
    proxy is down the call fails naming it, because fetching over clearnet instead is a
    deanonymisation bug rather than a degradation.
  - Documented honestly in the new `docs/darknet.md`: the boundary is the util's own code, not the
    kernel — `net:` is a bool and Landlock ABI 4 restricts bind/connect by *port*, not
    destination, so `outbound` means the whole internet. `docs/sandboxing.md` now says so at the
    point where it describes the network declaration.

## [0.112.0] — 2026-07-26

### Added
- **The util sandbox now exposes operator-staged shared read-only asset dirs to every run.**
  `sandbox.policy_for_run` adds any existing dir in `_SHARED_RO_UNDER_ROUTINES` (currently the
  unpacked **NopeCHA browser extension** under the routines home's `.control/nopecha-extension/`)
  to the run's read roots, derived from `server.routines_home` and **existence-guarded** so a
  deploy that has not staged it is unaffected. This lets `launch-captcha-browser` load the
  extension with `--load-extension=<dir>` from inside the Landlock jail (a CDP Chrome subprocess
  otherwise can't read a path outside the routine's own roots). Read-only: a util may LOAD the
  extension, never write it. The NopeCHA free tier solves < ~100 CAPTCHAs/day with no key, so no
  secret is required for that volume. Wiring of `launch-captcha-browser` itself belongs to
  global-utils-review (R28). Operator request R21. Test:
  `tests/test_sandbox.py::test_policy_for_run_includes_staged_shared_read_roots`.

## [0.111.0] — 2026-07-26

### Changed
- **`read_file` now END-truncates an over-cap read on whole-line boundaries instead of the
  head+tail elision.** A large read kept the head AND tail with the middle elided — fine for
  opaque output (a util's stderr, where the traceback END must survive), but wrong for an ordered
  file read: the reader lost the middle and had no clean way to continue. `engine/fileops._read_one`
  now keeps whole HEAD lines up to the observation cap, drops the tail, sets `end_line` to the last
  line actually shown, and appends a marker naming the exact `start_line=N` to re-read — so a
  follow-up read continues IN SEQUENCE. The shared `truncate()` (util stderr, vision output) is
  unchanged. Operator AUDIT note. Test: `tests/test_view_image.py`
  `test_read_file_end_truncates_and_resumes_in_sequence`. (F204)

## [0.110.0] — 2026-07-26

### Fixed
- **Utils now run with the calling routine's own directory as their working directory (CWD).**
  `utils_lib.run_util` launched every util with `cwd=<global-utils library home>`, but a routine's
  agent process runs in its routine dir (and its `read_file`/`write_file` resolve relative paths
  there) — so any relative path a routine passed to a util resolved against the library dir and hit
  ENOENT (seen across the bina-grants-analysis util calls; operator AUDIT note + bug R19). `run_util`
  gains an optional `cwd` (default: the library home, unchanged for the CLI, selftests, notify and
  settings), and the two run-scoped call sites — the routine util action (`engine/executor.py`) and
  the `vision` fallback (`engine/fileops.py`) — pass `ctx.routine.dir`. The util sandbox already
  grants the routine dir (`sandbox.policy_for_run` write_roots), so relative paths now resolve where
  a routine expects them to, matching read_file/write_file. Test: `tests/test_utils.py`
  `test_run_util_cwd_routes_to_given_dir`. (F206)

## [0.109.0] — 2026-07-26

### Changed
- **`report_bug` and `hand_off` are merged into ONE action, `report`.** Two actions that filed
  a durable item someone else acts on were the same act under two names, with two id
  namespaces, two streams and two Items types — the overload `hand_off` was introduced to
  relieve, reintroduced one level down. Now there is one channel, and what varies is only
  whether the reporting run can name an owner:
  - **Unaddressed** — "something is wrong and I am not going to work out whose it is." The row
    waits in `.control/reports.jsonl` for self-audit's triage. A run that hits friction mid-task
    should never have to consult the ownership table to say so.
  - **Addressed** (`target`) — the same row, plus delivery into that routine's `inbox/`, read on
    its NEXT SCHEDULED RUN. It starts no run and wakes nobody.
  `answers: "<R id>"` closes a report this routine received. Ungated and in `ALWAYS_KINDS`, so
  every routine holds it: routing only works if the channel is present at the moment the run
  notices the problem. That also removes the cross-routine injection concern that motivated
  gating `hand_off` — every routine now knows what a report is, and a delivered one is labelled
  with its sender.
- Triage becomes FORWARDING, not absorbing (the open half of B4): self-audit's `gather-evidence`
  answers an unaddressed report that is not a scheduler defect by filing an ADDRESSED one
  carrying `answers`, so the hand-off is recorded instead of performed by hand.
- One `R<n>` namespace and one append-only ledger, `.control/reports.jsonl` (report rows +
  `delivered` event rows). The 20 live `bug-reports.jsonl` rows moved across unchanged — they are
  all unaddressed, which is exactly what a bug report was. `W<n>` never reached production.
- One Items type, `report`, replacing `bug` and `work_order`. Status is derived the same way:
  `open` → `in_progress` once an addressed target drained it → `settled` once answered.
- The `work-orders` permission and capability are gone (`hand_off` leaves `GATED_KINDS`), and the
  four maintenance routines' grants with them. `rsched/bug_reports.py` + `rsched/work_orders.py`
  collapse into `rsched/reports.py`; `tests/test_report_bug.py` + `tests/test_work_orders.py`
  into `tests/test_reports.py`.

### Removed
- **Dead recipe frontmatter.** `seed_sha256` / `compiled_sha256` / `modules` were residue: their
  reader was deleted in 0.28.0 with the seed/recompile machinery, and the one-shot migration that
  shipped with it only renamed the `steps/` directories. `stages`, `tags`, `includes` and
  `adapted` were still WRITTEN but never read back — second copies of facts whose sources of
  truth are the `stages/` and `traits/` directories and `routine.yaml`, free to drift from them.
  Writers removed from `scaffold.py`, `engine/runtime.py` and `adapt.py`; 14 live routine and 4
  seed `main.md` files stripped. Only `materialized_from` and `tools` are read back;
  `name`/`slug` stay as the human identity of a file the user edits in the recipe editor.
- `routine-seed/workflow-curator/` — a retired routine still installed on every fresh install,
  which would now contend with `routine-improver` for the shared library. Its stale references in
  `health_events.py` and `api_workflows.py` point at the real owner.

### Note
- Because `report` is an `ALWAYS_KIND` that owns `target`/`answers`/`title`/`detail`, those four
  fields now survive every schema projection (`kindsurface`). A restricted run pays ~4 field
  descriptions more than before — the cost of the channel being unconditionally present.

## [0.108.1] — 2026-07-26

### Fixed
- `conversations.attachment_note` named the `vision` util in the `[attached files — …]` block
  it prepends to every message carrying an attachment. That block is prose the model reads, and
  the fallback describer is the ENGINE's choice, not the run's — so it now names the capability
  ("described for you automatically"). This was the live generator behind the same string in 12
  conversations' `instruction.md`; the 0.107.0 sweep never reached it because it only looked at
  library files, which is exactly the argument for fixing generators over outputs.
- The two routine instruction SEEDS that named utils (`routine-improver`'s `git-sync`,
  `uncensored-model-radar`'s `discord` and `model-refusal-test`) are rewritten to name the
  capability. A seed is not read at run time, but it is what a recompile compiles from, so a
  named util there re-enters the recipe on the next materialization.
- Repaired the existing conversation recipes: 30 stale trait copies re-synced from the library
  and 12 `main.md` attachment lines updated to the current `converse` wording. Audit over every
  seed and recipe in `~/routines` and `~/conversations`: 49 flagged files → 7, all seven
  legitimate (the `jsonblob.com` service; a `util-stats` path plus the literal error string a
  run matches on).

## [0.108.0] — 2026-07-26

### Added
- **`hand_off` — the inter-routine referral channel.** A routine addresses a durable WORK
  ORDER to another routine: `target` (its slug), `title`, `detail`, and optional `answers`
  (the `W<n>` id of an order this one closes). It is filed in the append-only ledger
  `~/routines/.control/work-orders.jsonl` and delivered as a message into the target's
  `inbox/`, which that routine's NEXT SCHEDULED RUN drains. It starts no run and wakes
  nobody — that is the whole point, and `tests/test_work_orders.py` pins it by asserting the
  delivery writes exactly one file and creates no spool, run dir or status.
  `scheduling`/`schedule_run` was briefly granted to the maintenance routines for this and
  reverted: it arms a one-shot that FIRES a run, which seizes the target's schedule.
- Gated by a new `work-orders` permission (+ `hand_off` in `GATED_KINDS`), granted to
  `self-audit`, `routine-improver`, `config-optimizer` and `global-utils-review`. The gate is
  not bureaucracy: a delivered work order becomes prose in another routine's prompt, so an
  ungated version would be a cross-routine injection channel. Self-targeting is refused.
- Work orders reach the prompt in their OWN section, `# WORK ORDERS FROM OTHER ROUTINES`,
  split out of `# MESSAGES FROM THE USER` — a work order is not something the user said, and
  rendering it as one has the run answer the wrong party. Mid-run they inject as
  `WORK ORDER (injected mid-run)`. The section carries no standing prose; how to receive one
  is the permission doc's job, already in CAPABILITIES when held.
- `W<n>` joins `F`/`D`/`R` as an item type on the Items page, with the lifecycle the ledger
  exists to expose: `open` (filed) → `in_progress` (the target's run drained it; the engine
  stamps a `delivered` event row) → `settled` (the target answered with a `hand_off` back).
  The card shows the routing line, so a hand-off that carried is distinguishable from one
  that silently never arrived.
- `routine-improver` gets `stages/library-pass.md`, making its ownership of the shared
  library real rather than asserted. Once per sweep it reads
  `~/routines/.control/workflow-usage.jsonl` (written every run since 0.7.0 and, until now,
  read by no agent), tallies each per-target fix under the pattern slug that target was
  materialized from in `state/library-watch.json`, and carries a confirmed PATTERN defect
  upstream — lint-gated, `META["version"]`-bumped, committed with its blast radius. It keeps
  the two checks the archived `workflow-curator` earned: a defect must show across routines
  built from the same pattern, and a library file must actually back the slug.
- `maintenance-routing` is now a SEEDED library trait (a fresh install lacked it), with its
  ladder step 2 filled in: hand it to the owner with `hand_off`, and close what you receive.

### Changed
- **The no-named-utils rule moves from a linter to the generation prompts.** `lint.named_utils`,
  `lint.lint_recipe_text`, `lint.lint_routine` and their gate in `rsched validate` are removed
  (net −156 lines). Nearly every recipe, trait, pattern and playbook is LLM-written, so a check
  over the output leaves the generator producing the same defect forever; and because the util
  catalog is dynamic (`global-utils-review` creates and removes utils autonomously), any
  name-matching check turns unrelated files red the day a util is named after an ordinary word
  — `report`, `notes`, `digest` — and blocks library saves with a 422 until someone edits a
  frozenset. `workflows/adapt.py` and `workflows/generate.py` now state the rule strictly,
  enumerating the forbidden forms. The existing recipes were swept once by hand: 63 flagged
  files down to 5, all five legitimate (the `jsonblob.com` service, a path, a literal error
  string). The general rule — correct the cause, never add a check for the symptom — is now a
  CLAUDE.md gotcha.
- `self-audit` loses the shared library from its `fs_write_roots` (it keeps READ). One owner,
  enforced by config rather than by prose; it hands library work over with `hand_off`.
- `inbox.drain_messages` carries `work_order`/`from` through from the message file, so the
  boot drain and the mid-run injection can route and label a sibling routine's message.

## [0.107.0] — 2026-07-25

Reviewer-backlog sweep: markdown in every message body, a hard "recipes name capabilities,
never tools" rule with a linter behind it, dead-code detection in the quality gate, and the
maintenance-routing hierarchy made mechanical.

- **Human message bodies render markdown** (`static/components/transcript.js`). The `answer`
  and `user_injection` branches built plain template strings while say/observations/questions
  went through the md pipeline, so a pasted list or code fence in an answer showed as literal
  asterisks. Both now render through `md()` — a superset of the inline renderer, matching what
  the conversation view already did. The gap affected two mounts (the run view and the
  Dashboard's activity section); the UI test asserts both.
- **A recipe says WHAT, never which tool** — new hard rule, no exemption for meta routines.
  Workflow patterns, materialized recipes, traits and playbooks may not name a util or show its
  flags; they name the capability and the run picks the tool from the live CAPABILITIES catalog,
  persisting what worked in the ROUTINE'S own memory. `lint.named_utils` enforces it at
  `rsched lint` (library), the library save endpoints, and — new — `rsched validate`, via
  `lint.lint_routine`, the first linter that reaches a materialized recipe at all. Two carve-outs
  keep the signal honest: a util name that is also a service or ordinary word (`gmail`, `shell`,
  `ftp`) is flagged only in an invocation shape, and a path named after its tool
  (`<repo>/.codemap/`) is a task fact. `workflows/adapt.py` states the rule in the prompt that
  compiles a recipe, so new routines start clean. `state/` and `.memory/` stay unlinted — tool
  knowledge belongs there. Spec: docs/authoring.md.
- **Dead code joins the quality gate** — `test_vulture_clean` in `tests/test_quality.py`, scanning
  `src` and `tests` together (a src symbol exercised only by a test is not dead), configured in
  pyproject so framework entry points called by decorator are excluded. Adopting it found four
  genuinely dead symbols, now removed: a `ServerConfig.playbooks_home` property, a duplicated
  sandbox-mode tuple (`SANDBOX_MODES` is now derived from the pydantic Literal — one contract, one
  source), a write-only `RunContext.parent_run_id`, and a dead wizard helper. Also declared four
  dependencies the code imports directly but only received transitively (`pydantic`, `markdown2`,
  `cryptography`, `py-vapid`). Ruff needed no change — `select = ALL` already covers its side.
- **Items in the item shape** (docs/items.md): the self-audit recipe now emits a `status` on every
  finding from the documented vocabulary, an `items: [...]` join on every changelog row it
  appends, and cites bug reports by their `R<n>` id rather than a timestamp no reader can resolve.
- **Maintenance routing is now mechanical.** `routine-improver`, `config-optimizer`,
  `global-utils-review` and `self-audit` hold the `scheduling` permission and the `schedule_run`
  action, so a problem outside a routine's remit goes to the routine that OWNS it — delivered into
  that routine's inbox as a durable work order — instead of terminating at the operator. The
  ownership table and escalation ladder are a new library trait, `maintenance-routing`. The shared
  library (workflow patterns, traits, playbooks), unowned since `workflow-curator` was retired in
  July 2026, belongs to `routine-improver`; stale references in README and docs/architecture.md
  are corrected.

## [0.106.0] — 2026-07-25

The **Items** page: one index of every system-maintenance item — findings, decisions and
bug reports — with its status, purpose, origin and when it was addressed. It replaces BOTH
the Log page and the Audit page.

- **New read model `readmodels/items.py`** merges the four scattered sources into one shape
  (spec: `docs/items.md`): the self-audit `report.json` (findings + decisions and the
  CURRENT status — always the authority), `changelog.jsonl` (the archive of which commit
  addressed what), `decisions-answered.json` (durable answered markers) and
  `.control/bug-reports.jsonl` (the ungated `report_bug` stream). An item carries `id`,
  `type`, `status`, `title`, `detail`, `origin{routine,run_id,ts,commit}`, `addressed[]`,
  `evidence[]`, `refs[]`. Memoized behind the four files' stat fingerprint; writes nothing.
- **Status vocabulary**: `open | in_progress | addressed | settled | dropped | unknown`,
  with documented precedence — the report's own field first, then a durable answered marker,
  then archive-only-with-changelog-rows, else `unknown`. Findings carry no `status` on disk
  yet (the self-audit routine will emit one from the spec on a later run): an absent status
  reads `unknown` and is NEVER recovered by parsing title prose.
- **The changelog join is explicit**: a row's `items: ["F202"]` field is the only trusted
  link. Historical rows fall back to an `F`/`D` id scan of their prose and are flagged
  `best-effort` in the API and labelled in the UI. The file mixes pretty-printed and compact
  JSON, so it is parsed with a streaming `raw_decode` loop — the old line-oriented reader
  silently dropped every multi-line row.
- **`GET /api/items`** (`web/api_items.py`) with `type` / `status` / `routine` / `search`
  filters; `counts` always cover the UNFILTERED set. `GET /api/audit` is gone — `api_audit`
  is now just the reviewer-feedback write channel (`POST`/`PUT`/`DELETE
  /api/audit/feedback`), whose behaviour is preserved exactly.
- **`report_bug` stamps a monotonic `R<n>` id** on every report (`bug_reports.py`), assigned
  under the same advisory lock as the append so concurrent runs cannot collide, and returned
  in the observation so the filing run can name it. `R` and not `B`: the user's own
  reviewer-backlog items are written `B<n>` in prose and would mislink. The 20 existing
  production rows were stamped in one atomic pass; there is no id-less form in the code.
- **Frontend**: `static/views/items.js` + `components/itemcard.js`; `reflinks.js` now
  linkifies `F`/`D`/`R` and lands on `#/items?focus=<id>`. `static/views/audit.js` and
  `static/views/log.js` are DELETED. The Log page's unique capability — the live
  cross-routine run feed with inline transcript tailing — became
  `components/activityfeed.js`, mounted as the Dashboard's activity section (collapsed and
  inert until opened; it no longer syncs its filters to the URL, being a section rather than
  a page).
- Docs swept: `docs/items.md` (new, on the Help tab), CLAUDE.md, README, `docs/architecture.md`,
  `docs/prompt-anatomy.md` (the report_bug observation now names the id).

## [0.105.0] — 2026-07-25

Context engineering pass: a run is shown only the vocabulary it has, told each thing once,
and can reach the rest on demand.

- **The action surface is PROJECTED onto the kinds a run may emit** (new
  `engine/kindsurface.py`). Sections 1, 2 and 6 of the system prompt all described all 21
  kinds regardless of the workflow's `tools:` allowlist and the routine's capabilities —
  ~15k of a ~22k prompt spent on two parallel descriptions of channels the validator would
  reject. `effective_kinds()` is now the ONE owner of "what may this run do"
  (capabilities.py, the harness contract's prose bullets and the schema all read it), and
  `schema_for_kinds()` narrows `ACTION_SCHEMA` — dropping other kinds' properties and
  trimming each surviving description to its relevant clauses. Derived from
  `actions.KIND_FIELDS` (promoted from `_KIND_FIELDS`), the same map `validate_action`
  builds its allowed-field set from, so what the model is SHOWN cannot drift from what the
  engine ACCEPTS. Measured: ~19% off the schema+prose surface for a default routine, ~12%
  for a conversation, ~60-64% for a tools-restricted workflow.
- The narrowed schema also goes to the TRANSPORT (`completion.next_action`, incl. the
  uncensored referral), so a disallowed kind is ungeneratable under constrained decoding
  instead of generated and then rejected — a saved turn, not just saved tokens. Validation
  keeps the FULL schema: a denial must stay a precise, teaching `grants.deny` message, never
  a schema parse error. A run with every kind enabled gets `ACTION_SCHEMA` unchanged, byte
  for byte — the prompt-caching contract is untouched.
- **The `say` contract is stated once.** `ACTION_SCHEMA`'s `say` description hardcoded the
  `standard` deliberation wording while the harness contract inserted the level-scaled one —
  so at `terse` the prompt demanded "ONE terse clause" and "2-3 sentences" in the same
  breath, and at `deliberate`/`think-on-paper` it contradicted the level the user chose.
  `engine/deliberation.py` is now the sole authority for HOW MUCH to say; the schema
  describes only the field's mechanics. The `note` channel is split the same way: the
  harness states the engine mechanics, the schema owns what belongs in one.
- **`practice-library` is a routine default** (`DEFAULT_PERMISSIONS` + the `read_trait`
  capability; `ADOPT_PERMISSIONS` carries it to existing routines once at boot). The curated
  practice modules cost nothing in the composed prompt — the digest lists names only — and
  are now fetchable just-in-time when a run meets a situation its own `traits/` set doesn't
  cover. It writes nothing: an unheld module applies for that run only, so the routine's
  practice set stays the user's. Dropped from `CONVERSATION_PERMISSIONS` as now redundant.
- **CLAUDE.md right-sized, 742 → 164 lines.** The nine subsystem-narration sections moved
  verbatim to the new `docs/architecture.md` (auto-published as a Help guide); what stays is
  the working tier — purpose, commands, the core contracts `self-audit` defends, the module
  standards, versioning, deploy — plus a pointer index into `docs/` and a new **Gotchas**
  section collecting the rules that are enforced by a test or a past incident (no
  backwards compatibility, the `MIGRATION(expires=…)` marker, the full-repo quality gates,
  the doc-sweep rule, the caching contract, the never-writable `routine.yaml`).
- `docs/prompt-anatomy.md` documents the projection and the single-source say/note split;
  `tests/test_kindsurface.py` pins the load-bearing property — a projected schema may never
  omit a field an allowed kind needs (checked against `KIND_EXAMPLES`, so a new kind is
  covered as soon as it gets an example).

## [0.104.0] — 2026-07-25

### Changed
- **OAuth consent scopes come from `<PROVIDER>_OAUTH_SCOPES`, with no hardcoded fallback**: a
  `scoped` provider (Google, Slack) now builds its consent request from the
  `<PROVIDER>_OAUTH_SCOPES` secret (`providers.authorize_scopes`, space/newline/comma-separated)
  instead of a `default_scopes` list baked into `providers.py`. `authorize-start` **errors** (400,
  naming the secret) when it is unset — so a connection can never silently consent to a narrower set
  than intended. This closes a trap where a Google connection consented only to the hardcoded
  `openid`+`email` and ignored the operator's `GOOGLE_OAUTH_SCOPES` entirely; that one secret is now
  the single source of truth for both the connection consent and any Google util. `scoped=False`
  (Notion — scopes fixed on the integration) sends no scope param and needs no secret. Widen/narrow
  a Google connection by editing `GOOGLE_OAUTH_SCOPES` and clicking re-authorize.
- `GET /api/settings/oauth` now reports `scoped` / `scopes_key` / `scopes_set` per provider, so the
  Connections card can flag a scoped provider whose scopes secret is still missing.

## [0.103.0] — 2026-07-25

### Added
- **Run messages carry file attachments (F202, user-requested)**: the run page composer
  gains the same 📎 attach / chips / paste-to-attach affordance as conversations, and
  `POST /api/runs/{id}/inject` + `/converse` are now multipart — uploads are stored under
  `attachments/` beside the run's polled inbox (the routine dir, or the `.wizard-<ts>`
  workspace for clarify runs), recorded as `attachments` rels on the inbox message, and
  auto-attached by the engine exactly like a conversation message. Screenshots and PDFs
  can now ride a mid-run injection, a queued next-run note, and a continued-run reply.

## [0.102.0] — 2026-07-24

### Added
- **Abandoned wizard sessions auto-expire (D44, user-settled: 12h)**: listing sessions now
  archives any ENDED session (clarify result waiting unconsumed, or failed) untouched for
  12h to `.archive/<wid>-stale` — the same recoverable move as cancel. The setup banner
  clears itself and dead inboxes stop collecting answers. Live chats and in-flight builds
  are never expired.

## [0.101.0] — 2026-07-24

### Fixed
- **Wizard build failover (F197)**: the stage-generation pipeline re-resolves the model
  chain after every failed attempt, so a hard endpoint failure (the 2026-07-24 claude
  credit outage) fails over to the next model instead of retrying the dead one until the
  build degrades. The clarify RUN already failed over; the BUILD now does too.
- **Degraded builds name their cause (F197)**: the LEDGER's "⚠ stage generation FAILED"
  block carries `Cause: <error>` and the daemon appends a `wizard_build_degraded` health
  event — no more diagnosing outages through the (sandbox-unreachable) daemon journal.
- **Abandoned wizard sessions are dismissible (F198)**: the setup banner and the
  new-routine list both carry a `discard` button for EVERY in-flight session (before,
  a session with a run page offered only "resume" — an abandoned one haunted every view
  forever and its unconsumed questions kept collecting answers into a dead inbox).

## [0.100.0] — 2026-07-24

### Added
- **Wizard live build progress (F192)**: the decompose pipeline reports each step (outline →
  main.md → stage k/N by name → traits) through a `progress` callback; `_build_routine`
  writes it into `state/finalize.json`, the wizard snapshot forwards `step`/`done`/`total`,
  and the setup panel's building screen shows the live step line instead of a bare spinner.
  A raising callback can never break a build.
- **Host mounts reach the fs picker (F190, deployment layer)**: `docker-compose.yml` now
  binds `/mnt` with `rslave` propagation — disks mounted on the host after container start
  appear inside too. Documented in `deploy/DOCKER.md`; takes effect on the next
  `docker compose up -d` (the daemon restart alone does NOT recreate the container).

## [0.99.0] — 2026-07-24

### Added
- **write_util EDIT MODE (D42-B)**: `write_util` now accepts `anchor`/`replacement`
  (+ optional `all`) as the alternative to re-emitting the complete script — the engine
  patches the util's existing source in place and runs the result through the unchanged
  approval + selftest + rollback gate. A 3-line fix to a 50KB util no longer exceeds a
  reply's output cap; failures teach (`util show <name> --full` for a verbatim anchor,
  `all: true` for ambiguous anchors). Schema descriptions, observation wording and the
  show-source hint all point at the new route.

### Fixed (utils library, alongside)
- **gmail (F186)**: non-ASCII `--query` no longer crashes — IMAP SEARCH switches to
  `CHARSET UTF-8` with byte-encoded criteria (pwlc's verified 3-hunk patch, landed).
- **discord (F196)**: `read --all` now fetches the NEWEST 100 messages (omits `after`)
  instead of the oldest 100.

## [0.98.0] — 2026-07-24

### Added
- **Decompose pipeline (D41 redesign)**: routine generation no longer rides ONE huge JSON
  completion. `decompose()` now runs scoped calls — stage OUTLINE (mutually exclusive
  scopes) → main (must route every stage) → one call PER stage (anti-stub guard) → adapted
  traits — each retried and validated; a dead traits call degrades softly to verbatim
  copies instead of discarding a good main+stages.
- **`util show --full` / `--range FIRST LAST` (D42-A)**: the complete source of any util
  is now obtainable without shell — the capped default teaches both flags — so shell-less
  routines can faithfully repair large utils.
- **`run_canceled` health event (F188)**: a user abort is no longer logged as an
  `orphaned_run` crash.
- **Wizard model picker (F191)**: the create form carries an explicit model choice
  (default: system default); the clarify template's models are no longer inherited
  silently into created routines.

### Fixed
- **Deferred answers reach the live run (F195)**: an answer filed while the asking run is
  still active is injected at the next turn boundary instead of waiting for the next run.
- **Discord mirror stale/cross-routine replies (F194)**: the mirror sends with `--cursor
  --json`, remembers the question's snowflake, and accepts only strictly-newer replies
  read with `--mine`.
- **Resumed runs report cumulative elapsed (F182)**: `status.json` no longer resets
  `elapsed_s` on a post-terminal resume leg (`elapsed_base_s` carried like `usage_base`).
- **One-click decision options (F189)**: clicking an option button SUBMITS the answer
  everywhere the shared answer form renders; digit keys still prefill for editing.
- **fs picker honesty (F190)**: an unreadable directory entry is marked (🔒) instead of
  silently hidden or demoted; the empty state explains the daemon's mount view.
- **Secret-exposure panel freshness (F193)**: the routine page refetches grants on the
  `question_answered` bus event instead of rendering a page-load snapshot.


## [0.97.0] — 2026-07-24

### Fixed
- **Wizard decompose reliability (D41)**: the stage-generation LLM call now retries once,
  gets real output headroom (`max_tokens ≥ 32000`) and a 300 s timeout — a single truncated
  or timed-out completion used to silently ship a STAGELESS routine (both 2026-07-24 wizard
  creations, `ards-study-steward` and `nanogeofeld-steward`, were born this way). The
  degraded fallback is now visible: the scaffold writes a ⚠ warning into the new routine's
  LEDGER, and `decompose()` returns an explicit `degraded` flag.
- **Stage frontmatter truthful**: a scaffolded routine's `stages:` frontmatter now lists every
  stage module on disk (decomposed + wizard extras) instead of only the decomposed ones —
  a `stages: []` beside real files broke the routine page's state graph.
- **Subruns can reach parent state (F185)**: a spawned/subtask child's allowed fs roots now
  include the parent's dir (read + write, chained for grandchildren) — a child tasked on the
  parent's `state/` files failed every `read_file` with "outside the allowed roots".

### Changed
- **Decompose prompt (D41)**: each stage body must be a complete, self-sufficient module
  (typically 20-60 lines; stubs are a stated failure) and resolved parameter VALUES must be
  bound inline — parameter names do not exist at run time.
- **D40 pinned by test**: connection bindings and secret-exposure grants save during an
  active run (behavior since 0.89.0 D35-A; now regression-tested so it cannot regress).

## [0.96.1] — 2026-07-24
### Fixed
- **Anti-batching override in the run prompt** (F180): the claude-CLI harness advertises
  "call multiple tools in one reply", but with `--output-format json` the engine only ever
  receives the envelope's single `structured_output` — when a model batched several actions
  in one reply, at most one executed and the rest were SILENTLY dropped (each still ACKed
  "success"; observed first-hand: a batched `write_file state/phase.json` never landed, and
  routine-improver 20260723-230001 hit the "No such tool available" variant 3×). The
  composer's harness contract now states explicitly: exactly ONE tool call per reply, the
  platform batching hint does not apply, extras are dropped/rejected — batch reads via one
  action's `paths` list. docs/prompt-anatomy.md updated to match; the sentence is pinned by
  test_composer (contract) and test_prompt_anatomy (doc sync).

## [0.96.0] — 2026-07-24
### Added
- **`sym diff` — scoped, symbol-addressed change review**: old side from any git ref
  (default HEAD), new side from disk, diff cut per symbol with class changes recursing to
  the changed methods; signature changes carry a `! signature changed · N files reference
  it` impact note. Single-symbol drill-down or whole-file listing (added/removed symbols
  collapsed to their roots). Scope was decided by measurement, not folklore: over the last
  40 commits only 2% of changed symbols were formatting/docstring-only (agent-authored,
  ruff-gated commits), so the classification tags and a difftastic/GumTree dependency were
  rejected; scoped diffs measured at 41–49% of full-file diff volume. Self-audit recipe
  (live + seed): §A drills into suspicious delta symbols via `sym diff --since <anchor>`;
  act-apply-fixes self-reviews every edited file (`sym diff <file>`, HEAD → working tree)
  before the syntax pre-gate and the pytest gate.

## [0.95.0] — 2026-07-23
### Added
- **Self-audit efficiency/effectiveness batch** (evidence-driven — profiling showed 55–77%
  of run observation volume was shell-based code exploration): the new library `sym` util
  (syntax-aware surgery: `read` fetches one complete symbol with a content hash, `replace`
  is compare-and-swap at the ast span with a whole-file re-parse gate, `refs` counts
  references, `check` is a fast syntax pre-gate for .py/.js/.json — ESM-safe on Node 20);
  the new `run-digest` util (one-observation triage digests of every new routine run —
  outcome, action counts, errors, questions, flags — so raw transcripts are opened only on
  anomaly, raising coverage from ~5 runs per routine to all); `codemap --since COMMIT`
  (symbol-level change set for the since-anchor review) and pre-verified orphan flags
  (whole-repo dotted-key/path reference scan rides the flag line). Codemap's import graph
  now counts function-local lazy imports and package-`__init__` relative imports — the two
  patterns that had 43 modules crying wolf as orphans (real count: 0). The doc-staleness
  check derives its scope from the tree (a reference is checked only when its first
  segment is a real directory under a candidate root) instead of a hardcoded namespace
  list. Self-audit recipe (live + seed): symbol-delta review, digest-first transcript
  triage, the `report_bug` stream (`.control/bug-reports.jsonl`) as a named evidence
  source, `sym`/windowed reads as the fetch discipline, `sym check` before the pytest
  gate, and a one-line-per-item `audit/report-index.md` replacing the full report re-read
  at orient.

## [0.94.0] — 2026-07-23
### Added
- **Self-audit works lookup-first from a generated codemap**: the new library `codemap`
  util (stdlib-only, offline, seconds) derives a compact map of this repo into a
  gitignored `.codemap/` — per-module API surface with line numbers, signatures,
  reverse-import counts and covering tests; the full HTTP surface with auth level and the
  static/ files calling each route (nested-router chains and shared-router imports
  resolved); frontend module exports/imports; contract literals and config-model fields;
  and a mechanical audit-flag inventory (over-budget files, orphan/untested-module
  candidates, skipped tests, TODO + MIGRATION markers, stale doc path references, churn
  hotspots). The self-audit recipe (live + seed) regenerates the map at orient, treats it
  as the lookup surface for the whole run, starts the fresh-eyes sweep from its flags, and
  refreshes it after committing — replacing most token-expensive code exploration with
  reads of pre-derived files.

### Fixed
- Two stale doc references the first codemap run surfaced: `CLAUDE.md` still pointed the
  task-tree read-model at `web/tasktree.py` and `docs/run-analytics.md` the health view at
  `rsched/run_health.py` — both live under `rsched/readmodels/` since the 0.87 split.

## [0.93.0] — 2026-07-23
### Added
- **The permissions & practice-module panels explain themselves**: every capability row
  (gated actions, reserved utils, run-history depth, subtask-pattern sourcing) now carries a
  descriptive help line with a concrete example, and every conduct-permission and
  practice-module row gains a "▸ full description" expander that renders the complete
  library doc inline — the exact prose the run's prompt receives (new shared
  `docexpand.js`; applies to the routine page, the conversation composer, and the wizard).
- Stream diagnostics (F175): the server now records an `sse-close` ui-trace line whenever a
  run event stream closes — cause (`end` / `cancelled` / `closed` / `error`), lifetime, and
  events carried — so client-side `reconnect` traces can be matched against what the server
  saw; the client's reconnect trace now logs the resolved stream path instead of the
  URL-builder's source text.

## [0.92.0] — 2026-07-23
### Changed
- Run view: the revise-recipe affordance is now an **"editable recipe" checkbox** right next to the composer input on finished routine runs (off by default) — checked, the SAME conversation continues via `/converse` with a run-scoped recipe unlock (the user's text rides the inbox verbatim; no framed pivot message). The `✎ revise recipe` button, the `revise` composer mode, and the `/api/runs/{id}/revise` endpoint are removed.
### Added
- **Per-routine secret exposure (D39)**: a util call whose transitive `secrets:` declarations name secrets present in the store now runs only once the user has granted this routine those secrets — the first call files ONE blocking approval (Discord-mirrored; ambiguous replies are held, D38), and the answer is persisted to routine.yaml's `secret_grants`. The routine page gains a "Secret exposure" panel (ask on first use / expose / withhold per store secret), editable any time; `PATCH /api/routines/{slug}` accepts `secret_grants`.

## [0.91.0] — 2026-07-23
### Added
- Run page: a dedicated **✎ revise recipe** button beside the composer input on terminal
  routine runs — preselects the revise mode; the placeholder states that the revision run
  sees this whole conversation as context (D37).
- Blocking **util-approval questions are settled only by a clear approve or decline** (D38):
  any other reply — a presence ping, an unrelated instruction — is held as a normal delayed
  user message (delivered at the next turn boundary, after the decision), the question stays
  open, and the Discord mirror re-prompts for approve/decline. New `inbox.file_message`
  engine-side writer; held replies are visible in the transcript (`answer` event, `held`).
- Stream diagnostics: the first-drop `reconnect` UI trace now records how long the SSE
  stream lived and how many events it carried (F175 — run-view streams observed dying
  every ~2 minutes; age/traffic distinguishes an idle-timeout kill from a mid-burst one).

## [0.90.1] — 2026-07-23

### Fixed
- **Real approvals were recorded as DECLINED (F161)**: util-approval answers arrive as
  free text (Discord mirrors blocking questions to the phone), and `_is_approval` only
  accepted a narrow head-word list — the operator's *"Do it. The mail is …"* read as a
  decline, twice, in the weight-loss coach's bootstrap. The affirmative vocabulary now
  covers natural phrasings ("do", "sure", "yep", "yeah", "proceed", "ja"), and a declined
  observation carries the verbatim answer so a mismatch is visible instead of reading as
  a contradiction. (`engine/interact.py`)

## [0.90.0] — 2026-07-23

Claude-subscription usage widget (D33) + an honest run-page waiting line (self-audit).

### Added
- **Local Claude-subscription usage widget (D33, option A)**: the claude-cli endpoint
  card in Settings → endpoints now shows the LOCAL token tally for the rolling 5h
  (Anthropic's subscription quota window) and 7d windows, computed from run telemetry
  (both homes, running runs count live) — Anthropic exposes no balance/quota API for
  subscriptions. New `GET /api/stats/claude-usage` + `readmodels/claude_usage.py`.
- **Refusal diagnosability (F164)**: the claude-cli result envelope's `stop_details`
  (e.g. `{"category": …}` on a classifier refusal) now rides the `Completion` verbatim
  and is included in the empty-completion transcript error event, so an empty reply's
  WHY is visible in the transcript.

### Changed
- **The run page's waiting line is honest about what it waits on (F170, operator
  note)**: between an `assistant_action` and its `observation` it names the executing
  action — "running util pytest-run…", "waiting on sub-runs…", "running an LLM
  subcall…" — instead of the blanket "waiting for the model…". (`static/views/run.js`)

## [0.89.0] — 2026-07-23

Operator-settled decisions D34/D35/D36 + the run-page model widget (self-audit).

### Added
- **Global scheduling pause (D34, option A)**: a dashboard button drops a durable
  `.control/pause.request` sentinel (sibling of the restart sentinel). While set, the
  scheduler skips scheduled fires (the fire table still advances, so resuming never
  backlog-fires), defers trigger/one-shot intake (nothing is consumed unfired), and a
  loud dashboard banner owns the resume control. Manual "▶ run now" stays available as
  the explicit override. `/api/status` reports `paused`; `POST/DELETE /api/settings/pause`
  toggle it idempotently. (`daemon/pause.py`, `daemon/scheduler.py`,
  `web/settings/pause.py`, `static/views/dashboard.js`)

### Changed
- **Config saves are allowed during a live run (D35, option A)**: `PATCH /routines/{slug}`
  and `PUT /routines/{slug}/permissions` no longer 409 while a run is active — verified
  that the engine reads `routine.yaml` exactly once at run boot (`runtime.run_routine`),
  so a mid-run save cleanly applies to the NEXT run. Recipe/file edits, recipe revert and
  archive keep their busy-guard: stage/trait files ARE read mid-run.
- **Run page model widget tells the truth (F166)**: `GET /runs/{id}` now falls back to the
  routine's configured `models.main` when the pre-engine boot stub has no model yet, and
  the "switch model" select mirrors the run's LIVE model instead of resting on the
  catalog's first entry (which read as "the run uses Haiku" on manual starts).
- **Composer hint (D36, option B)**: the conversations composer placeholder now names
  Shift+Enter for a new line (chat/transcript/answer composers already did).

## [0.88.2] — 2026-07-23

### Fixed
- **The system prompt no longer lies to the routine-improver about its own recipe**
  (`engine/composer.py`): the ownership paragraph stated "Your own recipe (main.md, stages/,
  traits/) is READ-ONLY to you" **unconditionally**, while the engine (grants/fileops) actually
  UNLOCKS own-recipe writes when a user `fs_write_root` covers the routine's dir — exactly the
  routine-improver's configuration. Consequence: with its "include in improvement" toggle on,
  the improver queued itself as a target and then skipped every lens on the self target,
  citing the sealed sentence (run 20260723-112446, turns 11/13). The recipe line is now
  conditional on `grants.recipe_unlocked` — an unlocked run is told its recipe IS writable.
  (+1 prompt test; docs/prompt-anatomy.md notes the variant.)

## [0.88.1] — 2026-07-23

Self-audit fix batch for six operator-reported defects (2026-07-23 audit notes).

### Fixed
- **A boot-time mount-key sweep can no longer kill scheduling** (`machines.py`):
  `sweep_stale_mount_keys()` runs on `run_forever`'s boot path *before* the tick loop's
  per-tick guard exists; an unreadable `.mounts/` (permission blip, sandboxed test env)
  raised out of `base.iterdir()` and silently unwound the scheduler while the web UI kept
  serving. Now a loudly-logged skip. (+ regression test)
- **Thinking models produce usable actions** (`endpoints/openai_compat.py`): closed
  `<think>…</think>` preambles (qwen3 / GLM hybrid-thinking via NanoGPT) are stripped from
  `content`, and the empty-content fallback now also reads `reasoning_content`
  (DeepSeek/vLLM/SGLang-style) beside `reasoning`. An unclosed think block is left visible
  for the retry path. (+2 tests)
- **New routines inherit the clarification template's models** (`web/api_wizard.py`):
  finalize without an explicit wizard models pick scaffolded a routine with NO `models:`
  block, silently landing it on the system fallback model (template said Fable, the created
  routine ran Opus). `_models_for_build()` now inherits the template's mapping. (+2 tests)
- **A slow routine build is no longer declared stuck** (`static/components/setuppanel.js`):
  after 5 minutes of a still-`building` scaffold the UI failed the flow with "may be stuck —
  try creating it again", inviting a retry that could only 409 ("already exists") while the
  first build finished fine. Now a keep-waiting notice; only a real error stage returns to
  the create form.
- **Multi-line secrets survive the Settings form** (`static/views/settings-secrets.js`):
  the plain-secret value field was an `<input type=password>`, which strips newlines from a
  pasted SSH private key before the store (newline-safe since 0.85.2) ever sees it. Now a
  masked textarea with show/hide. (+ UI round-trip test)
- **Conversations view no longer throws before initialization**
  (`static/views/conversations.js`): the run-state tail could invoke the `showQuestion`
  alias during setup, before its `const` initialized (TDZ `ReferenceError` seen in the
  2026-07-23 UI trace). The shared `questionPanel` import is used directly.

### Tests
- `test_utils.py` `_ctx` fixture gains the `machines` attribute 0.84.0 added to
  `RunContext.routine` (the same fixture-drift class as 0.79.0's `connections`); the
  grandchild-kill assertion treats an unreaped zombie (state `Z`, pid1 = the daemon in a
  container, no reaping init) as dead; `test_root_has_no_parent` skips where the sandbox
  cannot list `/`.

## [0.88.0] — 2026-07-23

The routine page becomes an overview, not a wall. It used to open on a "Name" field and
scroll ~7,500px through twenty stacked config sections with no status in sight; now it leads
with an instrument band and folds the configuration into labeled, collapsible groups —
"informative first, the nitty-gritty one fold away".

### Added
- **Routine overview hero** (`static/views/routine-overview.js`): a compact instrument band
  at the top of every routine page — live/idle/disabled status with the next fire, the last
  run's outcome and cost, the recent-run heartbeat, this month's spend, and any open
  decisions — each linking to the run, decisions, or history it summarizes. Reads only fields
  the detail payload already carried, so it costs no new request.
- **Collapsible config groups**: the ~15 configuration sections plus recipe/health/state are
  regrouped (`groupSections`) into six labeled, foldable groups — Schedule & triggers,
  Permissions & practices, Budgets & limits, Models & resources, Recipe & memory, Identity &
  origin — so the page is scannable instead of one undifferentiated column. The section `<h2>`s
  are preserved, so the sticky "on this page" rail still lists and jumps to every one, and
  Decisions and Runs stay in the always-visible overview zone above the groups.

### Changed
- `static/views/routine.js` now renders the hero and overview zone, then routes the config and
  recipe sections through a detached host into the grouped layout; no section body changed, so
  every existing control and its behavior (and the UI tests that drive them) are untouched.
- The "signal deck" stylesheet grows a hero-tile cluster and `rgroup` group styling in the
  same mono/amber instrument idiom (`static/views.css`), no new tokens or assets.

## [0.87.5] — 2026-07-23

The creation-path floor decision (the findings ledger's last behavioral item).

### Changed
- **A capabilities mapping is floored from birth, on every path**: the conversation
  default-creation path and the `/conversations/defaults` preview now run the same
  raise-then-floor the save paths and routine scaffold already apply — no persisted
  (or previewed) mapping can express a capability its held conduct docs did not require.
  Enforcement is unchanged (capabilities-only, fail-closed); production data audited —
  no live routine/conversation carried an orphan capability, so nothing migrated.

### Fixed
- docs/traits-permissions.md still claimed a capability could be enabled bare, without a
  conduct doc — stale since the floor landed (D8). The two-invariant wording now matches
  the code; CLAUDE.md names creation alongside save.

## [0.87.4] — 2026-07-23

Last small closers from the findings ledger.

### Fixed
- A broken connection/machine **binding no longer fails silently**: the resolver warnings
  the executor used to discard ("key X unset", "machine Y not in catalog") now land in the
  engine log on every util call that resolves bindings.
- **Workflow lint validates `tools:`**: a META allowlist naming an unknown action kind
  (or not being a list) is a lint problem — a typo'd entry used to pass lint and silently
  allow nothing at run time. Vocabulary = `engine/actions.KINDS`.
- Playbook writes (`MAIN.md` + detail files) are atomic (`paths.atomic_write`) — they are
  read cross-process by the web layer and library sync.

## [0.87.3] — 2026-07-23

Test-suite consolidation + the findings ledger's coverage list.

### Changed
- **One test double / helper where five-to-seven copies were**: `FakeRunner` (scheduler,
  triggers, schedule-once, hooks; the detached suite subclasses it for its status-writing
  fire + guarded resume), `git_in` (the pinned-identity subprocess-git helper behind every
  per-file `_git`), `mk_run` (the run-dir/status.json factory), and `make_test_server`
  (the hermetic config.yaml builder behind `api_client` and every hand-rolled TestClient
  block) — all in `tests/conftest.py`.
- UI harness: the free-port probe is gone — the bound socket is handed straight to
  uvicorn (`run(sockets=[...])`), closing the close-then-rebind race under xdist; the
  StubRunner's unread resume recording dropped; stale "inert until installed" comment
  fixed.
- `test_loop` wait wall-clock margins widened to 20s (the failure mode they distinguish
  is the 30s timeout; 10s flaked under load); the search limit-cap test now asserts the
  clamp (it was a tautology); a `/api/audit` test subsumed by an earlier one removed.

### Added
- Coverage for the ledger's untested list: the `subruns` status action; the wait-timeout
  branch (SubrunManager-level); the compaction gate's cached-0.8 vs uncached-0.6
  thresholds; trigger exactly-once redelivery across a crash replay; the sshfs mount
  success path (key 0600, pinned known_hosts, keydir removed on unmount); `/api/fs`
  401 + truncation; `ensure_docs` skip-env short-circuit; and a new UI file covering
  the Help and Log views plus the transcript renderer's question / answer / error /
  compaction rows.

## [0.87.2] — 2026-07-23

### Changed
- **Mega-view splits** (the ≤~350-line rule, applied to the frontend): `settings.js`
  874 → 99 lines (eight `settings-*.js` section modules — github, connections, machines,
  secrets, library+sync, source, server, notifications — plus `settings-common.js` for the
  shared remote tester; the pre-existing `settings-endpoints.js` convention, applied to
  every section); `conversations.js` 754 → 442 (`conversations-new.js` composer,
  `conversations-head.js` header, `components/filepicker.js`); `routine.js` 623 → 147
  (`routine-config.js` — every config panel, Name…Origin — `routine-health.js`,
  `routine-recipe.js`). Pure refactor: same DOM order, same behavior, all 56 UI tests
  green unchanged.

## [0.87.1] — 2026-07-23

Frontend polish sweep (the findings ledger's deferred UI batch).

### Added
- **Shared components**: `components/referchip.js` (the refer-to chip the run view and chat
  composer both mount — one convention, one implementation) and `questionPanel` in
  `components/answerform.js` (the blocking-question panel; the conversation now also shows
  the util-approval tag and the timeout/Decisions line — same record shape, same chrome).
- Run detail API (`GET /api/runs/{id}`) carries `home` (routine | conversation |
  background) — a payload extension.

### Changed
- **SPA remount replaces `location.reload()`** everywhere (run resume/converse/revise
  reattach, library delete): `router.remount()` re-renders the current view in place —
  no full page reload, no flash. Library **save** now also refreshes in place (list +
  tags), reopening the editor via its deep link.
- **Run view is home-aware**: a conversation-home run's breadcrumb links
  `#/conversations/<slug>` (it used to 404 onto the routine page), its rail uses the
  conversation stategraph/artifacts routes; a background task labels itself and skips
  the routes it doesn't have.
- Stats: routine/conversation names in "By routine" and "Monthly spend" link to their
  pages; the two stat-tile CSS systems (`.stat` / `.stat-card`) collapsed into one.
- Week grid colors are slug-stable (hash, not row index) — a routine keeps its color
  as the list reorders.

### Fixed
- **Accessibility**: dialogs trap Tab focus and restore it to the opener on close
  (`role=dialog aria-modal`); the search box is a real combobox/listbox
  (`aria-expanded`, `aria-activedescendant`, option roles); the conversation state dot
  carries `title` + `aria-label` (it was color-only).
- **Error-vs-empty honesty**: a failed fetch no longer renders as "empty" — the state
  graph, help tab, artifacts, file-activity, and subtask-tree rails each say the load
  failed (first load only; later transient errors keep the last good render).

## [0.87.0] — 2026-07-23

### Changed — the oversized modules split along their seams (overhaul batch 8)
Every file the audit flagged over the ~350-line standard now has one responsibility
(public surfaces unchanged — same imports, same routes):
- `engine/executor.py` (595) → executor (dispatch, util runner, llm subcall) +
  **`engine/fileops.py`** (read/view/write/edit, memory actions, read_trait, the shared
  path gates).
- `config.py` (666) → the **`config/` package**: `base` (vocabulary + lenient
  validation), `modelconf` (endpoints/models/machines), `server`, `routine` —
  `from rsched.config import X` is unchanged for every consumer.
- `web/api_routines.py` (630) → read surfaces + **`web/api_routine_edit.py`** (traits,
  permissions, PATCH, run-now, archive) + **`web/routines_common.py`** (the guards and
  lookups four sibling routers used to reach into api_routines for).
- `web/api_conversations.py` (506) → main + **`web/conversations_common.py`** (lookups,
  streamed attachment saving) + **`web/api_conversation_playbooks.py`** (save/update
  playbook).
- `endpoints/claude_cli.py` (478) → the adapter (sessions, retries, media latch) +
  **`endpoints/claude_cli_wire.py`** (command construction, env scrubbing, token
  resolution, envelope parsing).
- `engine/actions.py` stays whole on purpose — it is the documented single home of the
  action contract.

### Fixed/Changed — remaining deferred hygiene (batch 8, same commit series)
- ONE home each for: the lenient frontmatter parse (`library_docs.parse_lenient`), the
  neutral git identity (`libgit.GIT_USER/IDENTITY_*`), the Standing-practices tail
  (`scaffold.render_practices_tail` — the two copies' lead wording had drifted), the
  cross-home probe (`registry.all_homes`), and the abort-with-pid-fallback sequence
  (`api_runs.abort_with_fallback`, reused by background cancel — which now 409s
  honestly instead of `ok:true cancelled:false`).
- Bootstrap's five add+commit pairs go through `libgit.commit` (per-repo lock, scoped
  stage); `registry.next_fire` takes a real `Schedulable` protocol (the library-sync
  duck-typing `type:ignore`s are gone); `RunInfo` carries `model` so the Stats
  aggregate stops re-reading every status.json per request; `_run_ref` drops its two
  retired-shape tolerances; scaffold filters unknown budget keys and applies the same
  raise-then-floor capability discipline as the save path; GitHub device flows are
  pruned; the `library_home`/`utils_home` twin properties collapse into
  `libraries_home`; expired GitHub device flows are pruned on each start.

## [0.86.2] — 2026-07-23

### Changed — hygiene and dedupe sweep (overhaul batch 7)
- **Dead code removed** (each verified caller-less): `grants._PERMISSIVENESS` +
  `grants.unsatisfied_requires`, `lint_materialized_text` + `PLACEHOLDER_RE`,
  `bug_reports.read_bug_reports`, `scaffold._git_init`, `library_docs._git`,
  `schema_guard.parse_reply`'s never-passed `semantic` hook, `read_workflow`'s
  redundant 3-tuple (body==raw), the openai adapter's unreachable
  `structured_outputs` hint, and the OAuth provider registry's consumer-less
  `device_url` scaffold field.
- **One home per idiom**: `libgit.git_log` replaces the two byte-identical copies
  (library_docs / workflows.library); every mid-run control.json signal writes through
  ONE `merge_control` helper (api_runs, reused by the trait live-add) instead of six
  hand-rolled read-modify-writes; the runner reads `registry.ACTIVE_STATES` instead of
  two drifted inline tuples; the CLI's turn renderer falls back to `BRIEF_FIELD` for
  any kind without a rich label (six kinds rendered blank).
- **The server's zone, not a hardcoded city**: conversations, wizard sessions, detached
  tasks, and `rsched scaffold` now default tz to `server_tz()` instead of
  Europe/Berlin.
- `runtime`'s dead "fatal problem" substring classification is gone (no load problem
  ever matched it); `suggest_workflows` degrades gracefully like its sibling
  suggesters instead of 500ing the wizard on an endpoint failure; the api_audit
  legacy-shape recovery and the registry's pre-`elapsed_s` fallback carry MIGRATION
  markers; `atomic_write` documents its deliberate no-fsync durability posture and
  `FileSink.__del__` its real contract.

## [0.86.1] — 2026-07-23

### Fixed — frontend sweep (overhaul batch 6)
- **Badge refreshes are coalesced.** Every bus event — including sub-second `llm_task`
  storms during a busy run — fired its own `GET /api/questions`; llm_task events now
  skip the badge entirely and everything else refreshes at most once per 3s window with
  a trailing refresh.
- The routine page is live: its own run start/finish events refresh the header chip,
  recipe health, and the runs table (it was a static snapshot — a stale hub).
- Question filters live in the URL (`#/questions?filter=…&routine=…`); a routine page's
  "answer" button deep-links straight to that routine's open decisions.
- Search hits into a NESTED subrun (`?sub=2/1`) land on the child's top-level subtree
  instead of NaN-ing back to the main transcript; the never-produced `?offset=` deep
  link is gone.
- Library rows carry REAL section deep-links (`#/library/<kind>/<slug>`) — middle-click
  and open-in-new-tab work; a failed conversations-list load shows an error + retry
  instead of a silent blank rail; the Summary tab's filter/read states are actually
  styled and the list refreshes when a run finishes; the one-shot cancel dialog prints
  the fire time instead of `[object HTMLSpanElement]`; the models panel uses a CSS token
  that exists.
- a11y: toasts announce via `role="status"` + `aria-live`; the refer (↩) buttons are
  visible on keyboard focus.
- Dedupe/dead-code: ONE authed-fetch loop behind `api()`/`apiUpload()`; ONE
  `BUDGET_FIELDS` vocabulary (`components/budgetfields.js` — the two copies had drifted
  labels, and the wizard panel now shows the help lines too); dead `.tag.meta` CSS
  dropped. Server-side dead routes removed: `GET /api/workflows` + `POST
  /api/workflows/lint` (uncalled), the three shadowed `/library/utils/{name}`
  registrations (the `{kind}/{slug}` routes dispatch), `POST /settings/machines` (PUT
  upserts), and the pre-D11 wizard `events`/`transcript`/`answer` shims.

## [0.86.0] — 2026-07-23

### Changed — the read-model architecture gets an honest home (overhaul batch 5)
The audit's architecture verdict: the registry was "a universal read-model at a daemon/
address" (21 importers, 10 of them web), its siblings were scattered across four homes,
and every rail poll re-derived its view from raw transcripts.

- **`rsched/registry.py`** — the catalog/run-index read-model moves OUT of `daemon/`
  (every importer rewritten; nothing re-exported). The daemon OWNS processes; it does
  not own the shared view of the disk.
- **`rsched/readmodels/`** — the derived-view home: `stats`, `run_health`, `util_stats`,
  `statemap`, `fileactivity`, and `tasktree` (formerly `web/tasktree.py`) move in, with
  two shared primitives:
  - `memo` — stat-fingerprint caching (inode+mtime+size, the registry's idiom): a
    derived view recomputes only when an input file actually changed, and cached values
    return as deep copies. `statemap.phase_stats`, `fileactivity.file_activity`, and
    `tasktree.build_tree` — all polled every few seconds by the run rail — now hit this
    instead of re-parsing whole transcripts per tick.
  - `usage_stream` — the ONE parser of `workflow-usage.jsonl` (stats' monthly spend,
    run_health's buckets, and util_stats' table each had their own), memoized on the
    stream's fingerprint.
- **Search**: queries run on their own WAL read connection — a long refresh pass no
  longer makes the search box hang behind the writer's lock; the refresh budget now
  covers the stat-walk too (it was spent before the first budget check at scale).
- `USAGE_ERROR_EXIT` moves to `utils_lib` (the util contract) — the Stats read-model no
  longer reaches into `engine.executor` for a constant.

## [0.85.4] — 2026-07-23

### Fixed — transports, daemon, and web API sweep (overhaul batch 4)
- **Remote machines**: `known_hosts` parsing anchors on the key-TYPE token — a `.pub`
  paste used to pin `base64 comment` and every connection then refused (engine + the
  seed `remote` util); a failed mount attempt no longer leaks its PEM key dir; stale
  `.mounts/` key dirs are swept at daemon boot; `mnt/` is gitignored BEFORE any mount
  attempt so a crashed run's stale mount can't be autocommitted.
- **OAuth**: the refresh manager's slow token exchange now lands via compare-and-swap
  (`store.update_connection`) so it can never clobber a re-authorization that happened
  mid-exchange; a refresh response without `expires_in` gets an assumed 1h lifetime
  instead of hammering the provider every 5s tick; a non-JSON 200 no longer aborts the
  whole pass (per-connection isolation); the needs-reauth Discord ping is gated on a
  binding routine actually holding the `communication` permission; the provider `error`
  echoed on the public callback page is HTML-escaped.
- **Model calls outside the engine get real limits.** Decompose, suggest, generate,
  distill, and conversation autolabel now pass the resolved model's `max_tokens` (and
  effort) — they silently ran at anthropic's 8192 fallback; the adapter fallback itself
  now IS `config.DEFAULT_MODEL_MAX_TOKENS` (16 384), removing the two-constants
  conflict. The Settings credential label flags a configured-but-missing env file
  (`env_file_miss`) instead of reporting benign keylessness.
- **Daemon**: a queued run is abortable (the supervisor honors a cancel flag after its
  slot acquire); a PAUSED run releases its concurrency slot like waiting_user; a pid
  the daemon may not signal (EPERM) counts as ALIVE; retention gzips nested subrun
  transcripts (`rglob`); trigger cooldowns gate PER TRIGGER as documented (a cooling
  trigger's events wait; a sibling's fire); `ensure_config` verifies the token actually
  landed whatever the example says and writes atomically; `rsched abort` resolves
  conversations and background tasks and counts queued runs.
- **Web API**: inbox message filenames carry a uuid (same-second messages clobbered
  each other); the protected clarification template can no longer be resumed or given
  revise-recipe grants directly; the Decisions surface scans `background_home` (a
  detached task's deferred asks were invisible); attachment uploads STREAM to disk with
  the size cap enforced mid-flight (a big body used to buffer whole and OOM the 3.3GB
  box), capped per message, same-name collisions suffixed, and a 413 no longer strands
  a half-created conversation; web routine-dir commits take the shared repo lock;
  `config.yaml`/library-doc/workflow writes are atomic; the wizard answer endpoint
  validates `qid` (was a path segment); same-second wizard sessions no longer 500.
- **Config**: unknown top-level keys in `config.yaml`/`routine.yaml` are reported as
  problems — a misspelled `permisions:` used to silently reset the real field to
  defaults with zero trace.
- **Misc**: `stats.monthly_spend` skips malformed usage records instead of 500ing the
  dashboard; on-demand workflow generation counts up `-2, -3…` on slug collisions and
  rewrites the draft's META slug to match (no silent overwrite, no perma-lint flag);
  the decompose fallback logs WHY it degraded; the playbook-distill digest keeps the
  NEWEST exchanges when truncating (Update-playbook needs exactly those).

## [0.85.3] — 2026-07-23

### Fixed — engine resume/control correctness (overhaul batch 3)
- **A resumed parent gets its children's results back.** Child-exit announcements are
  live message appends with no 1:1 transcript event — a resume replayed everything BUT
  them, so a parent interrupted after a subtask finished lost the child's summary.
  `replay_messages` now reconstitutes announcements from `subrun_end` events (placed
  where the live message sat; children delivered via a `wait` observation are not
  re-announced), sharing one wording builder with the live announcer.
- **Blocking answers are no longer duplicated on resume.** The answer text lives inside
  the ask_user observation; replaying the `answer` event too injected it twice.
- **Mid-run switches fire once, not once per leg.** control.json is web-owned, so a
  consumed switch_model/set_deliberation/add_traits signal could never be cleared —
  every resume leg re-applied it (re-pinning models the user had changed back and
  re-injecting the same engine notes). The engine now keeps a per-run applied ledger
  (`control-applied.json`) that seeds the edge-triggers on every leg.
- **Fresh-boot inbox prose is transcripted.** Messages drained at kickoff rode only the
  composed prompt — invisible to the transcript renderer and lost on resume. They are
  `user_injection` events now (with a `boot` marker) and replay correctly.
- **`schedule_run` self-target works for conversations.** The schema always promised it;
  the handler only resolved routines_home. A conversation's one-shot lands in a
  namespaced spool (`conv--<slug>`, so a same-named routine can never be mis-fired) and
  the daemon wakes the conversation by RESUMING its run — the "remind me in 3 days"
  flow. Corrupt one-shot request files are dropped instead of rescanned every 5s; the
  dead `active` flag is gone from spool records.
- **Aborting a paused run credits the paused time**; killing a still-running child at
  parent exit snapshots its usage race-free; parallel `spawn` catalogs are listed for
  subtask/detach-only workflows too; a missing util's observation now names the
  available utils inline (no discovery turn); `orphaned_children` carries the workflow
  slug so boot's synthesized `subrun_end` matches the collector's shape.
- Hygiene: the inbox's raw-text fallback (no writer produces it) is gone — corrupt
  message files are consumed with a warning, transiently-unreadable ones still wait;
  `decisions._reply_texts` pins the discord util's ONE output shape; the dead
  `Budget.hard` knob is removed; tolerant `getattr` debris dropped across
  loop/subruns/interact/executor/capabilities/composer (test stubs now carry real
  fields); `executor` reuses `grants.is_recipe_path`.

## [0.85.2] — 2026-07-22

### Fixed — daemon + web security/robustness sweep (overhaul batch 2)
- **SSE tickets are SSE-only credentials.** A minted ticket used to satisfy `require_auth`
  on EVERY `/api` route — a 60-second URL-carriable full-API bearer, writes included. It
  now authenticates only the two EventSource surfaces (`/api/events`,
  `/api/runs/{id}/events`), GET only.
- **The fs picker refuses credential stores.** `/api/fs/list` browsed anything the daemon
  user can reach — including `~/.credentials`, the config dir (secrets.env, vapid keys,
  `.mounts/`), `~/.ssh`, `~/.claude`. Those roots (and descendants) now 403.
- **One bad scheduler tick no longer kills scheduling.** The cron tick body is guarded:
  an exception (a tz typo surfacing in next_fire, a disk blip) is logged + flagged as a
  health event and the loop keeps ticking — it used to unwind `run_forever` silently
  while the web UI kept serving. Lifespan background tasks (scheduler, push, search) get
  a died-silently observer; routine/library-sync `tz` values are validated at load/save.
- **Restart protection for clarify runs works again.** `restart.clarify_states` still
  scanned the pre-D13 `.wizard-*/runs` layout, where no run has lived since the wizard
  unification — the drain gate for in-flight setup conversations was silently inert. It
  now reads `clarification/runs/*` (the real layout); the tests pin the real layout too.
- **The scheduled library sync respects the commit serialization.** `git_sync` committed
  with an unscoped `git add -A` and no repo lock — sweeping any concurrent writer's
  uncommitted util into an "instance sync" commit and racing engine commits on git's
  index (bypassing the 0.83.1/0.83.2 discipline daily). It now stages only its own paths
  (`routines/ config/`) under the shared per-repo lock, pulls `--autostash`, and no
  longer pushes over a failed pull. Config-export redaction also scrubs URL-embedded
  credentials (`https://user:token@host` in remote URLs).
- **Multi-line secrets survive the store.** A pasted SSH private key (the
  remote-machines `key_var` flow) was silently corrupted by the line-based secrets store
  (docs tell the operator to paste one). Values containing newlines are now JSON-quoted
  onto one line; single-line values keep the historical format byte-identically.

### Fixed — engine + transport correctness sweep (overhaul batch 1)
Security/correctness-critical fixes from the external audit's findings ledger.

- **Empty completions engage failover and referral (the refusal-gap fix).** Adapters now
  surface the provider's stop reason on every `Completion` (`stop_reason`: anthropic
  `stop_reason`, openai `finish_reason` / ollama `done_reason`, the CLI envelope's
  `stop_reason`/`subtype`). An EMPTY completion with `stop_reason: refusal` — a classifier
  refusal, previously indistinguishable from a hiccup — is referred to the routine's
  `uncensored` model like a free-text refusal; the SECOND consecutive empty from one model
  engages the fallback chain exactly like a hard `EndpointError` (logged as the same
  `failover` payload) instead of blind same-model retries until the run died.
- **Cooldowns are a provider-health signal.** `InstrumentedEndpoint` now starts the 5-min
  failover cooldown only for retryable-class failures (outage, rate limit, network) — a
  bad key or a Settings probe with a wrong credential no longer poisons resolution for
  5 minutes. The engine still cools any model it abandons mid-turn (the judgment moved to
  `completion._switch_to_fallback`, which now marks the failed model itself).
- **claude-cli transport hardening**: calls now run under the shared `with_retries`
  backoff (one transient CLI failure no longer costs a cooldown + failover); garbled CLI
  stdout is retryable like an unparseable HTTP body; the stream-json image capability
  latch flips only after retries exhaust (not on one blip) and a fresh-session reseed
  carrying OLD in-context images degrades them to placeholders instead of hard-failing;
  per-run session cwds under `~/.cache/rsched/claude-cli/` are pruned after a week;
  `SSH_AUTH_SOCK`/`SSH_AGENT_PID` are scrubbed from the CLI child like every util child.
- **`write_util` traversal guard + selftest rollback.** A `write_util`/`remove_util` name
  must be a kebab-case slug (validated in the schema cycle, backstopped in
  `utils_lib.write_util_file`/`remove_util_file`) — a path-shaped name could write outside
  the library. A FAILED selftest now rolls the write back (a new util's dir removed, a
  revision restored to the previous working text) instead of leaving the broken script
  live for concurrent `gu` callers.
- **Util subprocess timeouts kill the whole process group.** `run_util` starts utils in
  their own session and `killpg`s on timeout — the `uv run` grandchild used to survive
  (holding the pipes open and blocking the engine turn past its timeout forever). Output
  capture is file-backed and capped at 1 MB per stream. The seeded `gu` dispatcher's
  `list` skips `__pycache__`/removal residue; `header_problems` now also rejects
  undeclared `["gu", "<sibling>"]` exec sites (same regex the boot migration uses —
  moved to `utils_lib.GU_CALL_RE`).
- **Child tasks keep their workflow's `tools:` allowlist.** `childrun.build_child` loaded
  the materialized pattern's allowlist and then dropped it — every spawn/subtask child ran
  unrestricted. The child number allocation is also lock-protected now (parallel spawns
  could collide), and child/recipe/result writes are atomic (`materialize_to_disk`,
  `_ensure_decomposed`, `result.md`).
- **Blocking-ask re-files keep `config_patch`.** The abort/defer/timeout re-file paths
  dropped a pending config proposal; only the dialog path kept it.
- **Spend from failed turns is booked.** Usage burned by schema-retry cycles that never
  produced an action (force-fail, abort preempt) now lands in the run's usage.
- Empty-completion retry backoff honors `RSCHED_RETRY_BASE_DELAY` (suite runs the logic,
  not the clock); `run_context` sheds its `__import__("os")` debris.

## [0.85.0] — 2026-07-22

### Added — Runtime duration bars on the dashboard "this week" strip
Each fire on the week strip is now a **duration bar** instead of a point: it starts at the fire
time and its width is the routine's **average runtime drawn true to scale** against a day's width
(a full day column = 24h), so a mark's length is an honest read of how long a run takes
(`components/weekgrid.js`).

- The average is computed in the browser from the `recent_runs` window each card already carries
  (`avgRuntime` over `elapsed_s`); no API change. A **2px minimum width** keeps even a short run
  visible, and the **exact runtime is in the hover tooltip** (bars for minutes-long runs sit near
  the floor — that's the honest scale on a week-wide axis).
- Routine **identity moved to a legend below the strip** (colour swatch + name + schedule), which
  frees the timeline to use the full width; the routine's average also shows on legend hover.

### Added — Sections side-TOC on the routine page
The routine detail page now grows the same sticky **"On this page"** rail Settings has, listing its
`<h2>` sections on wide viewports (`components/toc.js`). The page's recipe file tree is a
within-section nav and no longer suppresses the page-level TOC.

### Changed — Filesystem roots are picked, not typed
The routine page's read/write **filesystem roots** are now chosen with a real **server-side
directory browser** instead of a free-text "one path per line" textarea — browse the daemon's
filesystem and select an actual directory (`components/dirpicker.js` + `components/fsroots.js`,
backed by the new bearer-authed `GET /api/fs/list` — `web/api_fs.py`, names + is-dir only, never
file contents). Each root is a removable row; the save payload is unchanged.

## [0.84.0] — 2026-07-22

### Added — Remote machines: routines act on SSH hosts (GPU boxes, build servers)
A routine can now run commands and move files on remote machines over SSH — for work that needs
specific hardware (a GPU, a big build box) the daemon host doesn't have. Modeled on OAuth
connections: a **resource binding**, never a capability a run can grant itself.

- **Machine catalog** (`config.yaml` `machines:`, `ServerConfig.machines` → `MachineConfig`): an
  operator-only, instance-wide list of SSH hosts (host / user / port / `key_var` / pinned
  `host_key` / workdir / description / tags). Key MATERIAL never lives in config — `key_var` names
  a **Secrets-store** key holding the private key; the pinned `host_key` is the server's public
  key, verified STRICTLY at connect (no TOFU in a headless run). Settings → Machines does CRUD,
  a host-key **scan**, and a live **test** — the last two run the real `remote` util server-side,
  so what Settings proves is exactly what a run gets.
- **Binding** (`routine.yaml` `machines: [names]`): a routine names the catalog machines it may
  reach; the binding IS the grant. No run creates or changes one (`routine.yaml` stays sealed).
  Bound on the routine page, alongside models/connections.
- **The reserved `remote` util** (needs the new `remote-machines` permission): `list`, `exec`
  (short, blocking), `submit`/`status`/`logs`/`cancel` (DETACHED jobs for long GPU work — poll,
  or pass `--notify-webhook <the routine's own trigger URL>` and let the job ping the routine on
  completion, no polling), `push`/`pull` (SFTP), plus `scan-host`/`test`. Host keys pinned; a
  mismatch refuses to connect.
- **Injection**: the engine resolves a routine's bindings to `RSCHED_MACHINES` (non-secret
  connection metadata) + `RSCHED_MACHINE_KEYS` (private keys from the Secrets store), passed to
  the `remote` util under the same declared-var gate OAuth tokens use — a token/key reaches a util
  iff the routine binds the machine AND the util declares the var. Bound machines are named in the
  prompt's CAPABILITIES section, so the model knows its hardware without a discovery turn.
- **Filesystem shares** — compute crosses via `remote exec`, the FILESYSTEM via a mount. A machine
  catalog entry can set a `share` (a remote dir); when a routine binds that machine the engine
  mounts it over sshfs at `<routine>/mnt/<name>/` for the run, so ordinary filesystem utils (and
  `read_file`/`write_file`) act on remote files with **no transfer step**. The engine mounts it
  (not a sandboxed util), so the key never enters a util; the routine dir is already a sandbox write
  root and a Landlock rule on it covers the sshfs sub-mount, so utils operate under the same jail.
  `mnt/` is gitignored; mounting is best-effort (unreachable host / no `sshfs` → warn and proceed).
  Docker gains `sshfs` + `/dev/fuse` + `CAP_SYS_ADMIN` (inert unless a bound machine sets a share).
- **Hardening**: `SSH_AUTH_SOCK` / `SSH_AGENT_PID` are now scrubbed from every util subprocess
  (`STRIP_VARS`), so a forwarded agent can never route around the per-routine machine binding.

`~/.ssh` stays invisible to the sandbox exactly as before — remote-machine keys come from the
Secrets store, not from disk. See `docs/remote-machines.md`.

## [0.83.2] — 2026-07-22

### Fixed — Routine-dir commits queue instead of racing (finishes the 0.83.1 race work)
0.83.1 made the shared LIBRARY repo's commits lock-serialized. This does the same for a
ROUTINE's own git repo, closing the last meta-routine race: the **routine-improver commits a
target routine's dir itself** (via the `git-sync` util at its `record` stage), so when that
target is mid-run, two processes were committing one repo — the improver's `git-sync` and the
target's own autocommit / pre-run recipe snapshot — colliding on `index.lock`. Now every writer
of a routine dir takes the **same per-repo lock** (`<repo>/.git/rsched-commit.lock`) and they
queue instead of racing:
- **Engine** ([autocommit.py](src/rsched/engine/autocommit.py), [recipes.py](src/rsched/recipes.py)):
  the run-end autocommit, the pre-run recipe snapshot (`current_recipe_commit`), and the web
  recipe revert (`revert_recipe`) all commit under `paths.file_lock(repo_lock_path(dir))`.
- **`git-sync` util** (library repo): holds the same flock around its local `add`/`commit`/`rebase`
  (push stays outside — it's network-only and doesn't touch the index). Shipped to the library
  (daemon reads utils live — no restart).
- Tests: routine-dir lock coverage in [test_libgit.py](tests/test_libgit.py); the util's `--selftest`
  asserts the lock file is taken.

Still a *logical* (not corruption) gap, unchanged: a multi-file recipe edit isn't one transaction
and the target runs on its old in-memory recipe until its next run — acceptable, since the recipe
only takes effect at the next run anyway.

## [0.83.1] — 2026-07-22

### Fixed — Race conditions when the meta-routines run alongside other routines
Concurrent runs are separate processes with isolated per-routine dirs, so ordinary routines never
collide. But the meta-routines cross that boundary by design — **routine-improver** writes another
routine's recipe, **workflow-curator / util review** rewrite or delete utils another run may be
executing, and **self-audit** reads everything. Every engine write on those paths used a
non-atomic `path.write_text()` (truncate-then-write), and the shared library repo was committed with
an unlocked `git add -A`, so a concurrent reader/committer could see a torn file or sweep a
sibling's change into the wrong commit. Now:
- **Shared library repo commits are serialized and scoped** ([libgit.py](src/rsched/libgit.py)): the
  three duplicate `git_commit` helpers ([utils_lib](src/rsched/utils_lib.py),
  [library_docs](src/rsched/library_docs.py), [workflows/library](src/rsched/workflows/library.py))
  delegate to one primitive that holds a per-repo file lock (`paths.file_lock` /
  `paths.repo_lock_path`) and stages only the path it changed (`git add -A -- <path>`). Every
  writer — engine `write_util`/`remove_util`, the Library-tab web edits, on-demand workflow
  generation — passes its own pathspec, so no `git add` can sweep another writer's not-yet-committed
  file and two writers never collide on `index.lock`.
- **Engine writes are atomic** ([executor.py](src/rsched/engine/executor.py)): `write_file` (the
  overwrite branch), `edit_file`, and `memory_write` go through `paths.atomic_write` (tmp+rename,
  now mode-preserving so an existing file's bits — notably +x — survive an overwrite). The improver
  rewriting a live routine's recipe, that routine's own git autocommit / pre-run recipe snapshot,
  and self-audit reading any routine now see the old or new file whole, never a partial write.
- **Util create/delete are atomic** ([utils_lib.py](src/rsched/utils_lib.py)): `write_util_file`
  uses tmp+rename; `remove_util_file` renames the dir aside before deleting, so a routine executing
  `gu <name>` concurrently sees the util whole or gone, never a half-emptied tree.

Not addressed (out of scope, flagged for a follow-up decision): the improver editing a running
routine's recipe is still a *logical* race — atomicity stops torn files, but a multi-file recipe
edit is not a single transaction, and the target runs on its old in-memory recipe until its next
run. A run-active guard for recipe writes is the open question.

## [0.83.0] — 2026-07-22

### Added — Revise recipe (change a routine's recipe in natural language, from the run view)
A finished routine run's message box gains a **"revise this routine's recipe"** mode: type the
change ("make the report shorter", "stop checking X") and the run resumes with a **run-scoped
recipe self-write grant** and edits its OWN `main.md` / `stages/` / `traits/` / `tuning.yaml` using
its normal file tools — the warmest possible context (it just executed). No extra routine, no
persisted grant.
- **`engine/revise.py`** + the loop ([loop.py](src/rsched/engine/loop.py:102)): a marker the
  `/revise` endpoint drops in the run dir is read ONCE at loop init — it grants `recipe_unlocked`
  and widens `allowed_tools` with `read_file`/`write_file`/`edit_file` for that leg only, then
  clears itself. Ordinary runs stay recipe-sealed; `routine.yaml` (config) stays sealed even under
  revise.
- **`POST /runs/{id}/revise`** ([api_runs.py](src/rsched/web/api_runs.py)): routine-only,
  finished-runs-only; injects a framed directive (edit your recipe; route config asks to
  `ask_user`) and resumes. UI: the "revise" mode in [run.js](static/views/run.js) (hidden for the
  protected clarification template).
- **Config bridge (one-click apply):** a run can't edit `routine.yaml`, so a config-shaped request
  becomes an `ask_user` carrying an optional **`config_patch`** (the `PATCH /routines` body). The
  Decisions page renders the proposed change with an **"approve & apply"** button that PATCHes the
  routine and resolves the ask — reusing the config controls shipped in 0.82.0. `config_patch`
  threads through `actions.py` → `interact.py` → the decision record (`inbox.file_question`) →
  `questions.js`.

## [0.82.0] — 2026-07-22

### Fixed
- **Editing a claude-cli endpoint no longer wipes its `credentials_env` / `key_env_file`.** A
  full-replace `PUT /settings/endpoints/{name}` preserved only `temperature` / `extra_body` /
  `max_tokens`, so any edit (even re-saving the token) reset a custom subscription-token path back
  to the default `~/.credentials/claude-code-oauth.env` — silently breaking auth. Both fields are
  in the preserve list now (`web/settings/endpoints.py`).

### Added — config surfacing (every setting is now reachable in the UI)
An audit found several config fields that had no editable control and could only be changed by
hand-editing `routine.yaml` / `config.yaml`. All are now in the UI:
- **Routine page:** a **Name** rename; a **Retention** control (`keep_runs`); a **Filesystem roots**
  editor (`fs_read_roots` / `fs_write_roots` — a write root covering the routine's own dir is the
  lever that unlocks recipe self-editing, the routine-improver's mechanism); the schedule **catchup**
  policy (skip vs run-once) on the schedule editor; and the **`max_total_turns`** budget (was
  conversation-only). `PATCH /routines/{slug}` accepts them (`keep_runs`, `fs_read_roots`,
  `fs_write_roots`, `schedule.catchup`); the detail read surfaces them.
- **Settings → Server:** a new panel for the runtime knobs — util **sandbox** mode
  (strict/permissive/off), **max concurrent runs**, **registry rescan** interval, and the **GitHub
  OAuth client id** (`GET`/`PUT /settings/server`, `web/settings/server.py`). Sandbox and rescan
  apply live; concurrency needs a restart (the copy says so).
- **Settings → endpoint cards:** inputs for **`temperature`**, **`key_env_file`**, the claude-cli
  **`credentials_env`**, and the openai **`extra_body`** (JSON — OpenRouter provider routing);
  `EndpointBody` + `_endpoint_view` carry the latter two.
- **Triggers card:** a **cooldown** input on webhook-trigger creation (the backend already accepted
  `cooldown_s`; the form never sent it).

### Changed
- `patch_routine` grew a `_apply_resource_fields` helper for the nested/validated fields, and now
  handles the `deliberation` (tuning) write before any `routine.yaml` mutation — so a combined
  patch can no longer early-return and drop an accompanying config change.

## [0.81.0] — 2026-07-21

### Added
- **Practice modules are changeable after creation** — a *Practice modules* panel on the routine
  page and in the conversation header adds or drops traits on an existing routine/conversation
  (`POST /routines/{slug}/traits`, `POST /conversations/{slug}/traits` — one shared
  implementation). The `traits/` directory IS the state and main.md's Standing-practices tail is
  DERIVED from it, rebuilt on every change (`rsched/traits.py`), so add and remove need no
  special-casing and a hand-edited tail converges back. A later add copies the library text
  **verbatim** — only creation adapts, and an LLM round-trip between flipping a switch and the
  module taking effect isn't worth it for a set written to be generally applicable.
- **An addition reaches a run already in flight.** Deliberately not 409-guarded like other
  routine file edits: a run may never write its own `traits/`, so the web layer is the sole
  writer there and no race exists. Since the composed prompt is immutable under the
  prompt-caching contract, `control.json` `add_traits` makes the engine append the module's prose
  as an engine note at the next turn boundary (`control.apply_trait_additions`, the same
  edge-triggered discipline as the model and deliberation switches). Removal lands at the next
  run — prose already in a live context cannot be unsaid.
- **`read_trait` — a read-only practice consult for a run.** A run still never changes its own
  set, but may pull one library module into the CURRENT run when the work turns out to need a
  discipline its recipe lacks (`name: "list"` for the catalog, entries flagged when already held).
  Nothing is written, so the recipe invariant holds intact. Gated by the new `practice-library`
  permission — default-on for conversations, opt-in for routines.

### Changed
- `DEFAULT_TRAITS`' "not toggleable afterwards" note is retired: the user may now retune the set
  at any time. What stays fixed is the direction — practice is granted, never self-granted.

## [0.80.0] — 2026-07-21

### Added
- **A curated practice-trait set in the library** — eleven new opt-in traits distilled from
  Anthropic's prompt-engineering guidance, the Claude Code plugins (skills and prompt-snippet
  references as well as the output-style hooks), OpenAI's agent prompting guide, and the
  self-correction/verification literature:
  `evidence-discipline` (every reported claim traced to an observation; verified-or-not as a
  binary, never a confidence score), `decision-commitment` (choose and stop re-deciding),
  `error-recovery` (read a failed observation before reacting; two failures at one step means the
  approach is wrong), `change-restraint` (the smallest change that does the job),
  `independent-verification` (check from outside the context that produced the work — a mechanical
  check, else a `subtask` verifier briefed without your reasoning), `review-recall` (find first,
  filter second), `teaching-insights` (explain the reasoning where a human is reading),
  `interface-design` (build UI that looks chosen rather than generated — pin the subject, avoid
  the current default looks, plan and critique a token system before coding) and `interface-copy`
  (words as design material: name things by what the reader controls, errors that explain and
  direct, one job per element), `test-design` (a test earns its place by failing — name the
  regression first, assert behaviour not internals, watch it fail once) and `failure-visibility`
  (error handling written INTO code: never catch without a reaction, enumerate what a broad catch
  would swallow, stubs never ship).
  None is a default: each is picked per routine/conversation, **the trait itself is the on/off
  switch**, and a trait that is off contributes nothing to the prompt. They reach existing
  instances at the next daemon boot via `bootstrap.sync_seed_library_docs` and ride the normal
  `library-sync` to the library repo — no new mechanism, no always-on block.
- **`docs/curated-traits.md`** (new Help-tab guide) — per-trait provenance and *evidence strength*,
  the reasoning behind shipping these as selectable traits rather than one always-on prompt
  extension (the prompt's scarce resource is attention, not cached tokens), and the candidates
  evaluated and **rejected on evidence**: self-critique-before-finishing (measurably net-negative
  unaided), "don't be sycophantic" (the least effective mitigation tested), numeric confidence
  (systematically overconfident), plus the ones this harness already covers structurally.

### Changed
- `suggest_traits_permissions` now tells the wizard when each curated trait is worth taking — and
  explicitly not to take the set by default, since every trait costs prompt on every run it is on.

## [0.79.1] — 2026-07-21

### Fixed
- **A finish→reopen no longer loses the pre-finish leg's util histogram and counters in
  `status.json` (F140 — completes the F131/F132 fix).** The boot-time `prior_counters` reseed
  (0.76.1) rehydrates a resumed leg's cumulative telemetry from the prior leg's `status.json`,
  but `Runner.resume()` overwrote that file with a bare `{state:queued, turn:0, …}` dict — no
  `utils`, no `asks_deferred`/`schema_retries`/… — *before* the engine booted, so the reseed
  read an already-clobbered file and carried nothing forward. Observed: a reopened run with 9
  real util calls reported only 2 in `status.json` (and `utils:{}` on the double-finish path).
  The queued-status write is now a shared `_queued_status()` helper that, on resume, merges the
  prior leg's telemetry forward (transient run-state fields still reset); a fresh run is
  unchanged. The global util-stats snapshot was always correct (transcript-derived); this only
  repairs the per-run `status.json` + finish event. Regression-guarded by a round-trip test
  asserting the resume write is lossless w.r.t. `prior_counters`.

## [0.79.0] — 2026-07-20

### Added
- **Settings → Secrets: manage multi-entry (JSON-map) secrets one entry at a time.** A secret whose
  value is a JSON object (e.g. `FTP_SOURCES` holding several FTP servers) can be extended without
  re-typing the whole write-only blob: the card lists the entry NAMES (never the values) with a
  per-entry delete, and an "add / replace entry" form merges a single entry SERVER-side (the other
  entries' values are never returned). New endpoints `PUT /settings/secrets/{key}/entry` and
  `DELETE /settings/secrets/{key}/entry/{name}`; the listing gained a `maps` field. Plus a
  show/hide toggle on the secret-value field, since a JSON map is unreadable when masked.

## [0.78.4] — 2026-07-20

### Added
- **Settings → Secrets now shows each needed secret's FORMAT.** A "format / help" expander per
  entry reveals the declaring util's `usage:` + docstring, so a structured secret's shape (e.g.
  `FTP_SOURCES` is a JSON map `{name: {host, user, pass, port?, tls?, dir?}}`) is discoverable
  right where you set it — not only in the util source. `utils_lib.parse_header` now returns the
  docstring; the needed-secrets API carries the declaring util's usage + doc.

## [0.78.3] — 2026-07-20

### Fixed
- **Settings → Secrets no longer lists OAuth connection tokens (e.g. `NOTION_ACCESS_TOKEN`) as
  "needed" secrets.** A util declares them only so the sandbox lets the ENGINE-injected token
  through — the user never *sets* them (they come from binding a connection on a routine), so
  prompting for them was misleading. The needed-secrets list now excludes every provider's
  `<PROVIDER>_ACCESS_TOKEN` (`oauth.providers.connection_token_vars`, now also the single source
  for that injected-var name, used by `store.tokens_for_routine` too).

## [0.78.2] — 2026-07-20

### Fixed
- **The "declare the credential env vars you read" util gate had a blind spot** (`utils_lib.
  _secrets_read`): it caught direct literals (`os.environ["X_TOKEN"]`) and single-constant
  indirection (the `gu claude` `TOKEN_VAR = "…"` pattern) but **not a tuple/list of names looped
  over `os.environ`** — `KEYS = ("A_PASS", …); for k in KEYS: os.environ.get(k)`. So the `ftp`
  util shipped without declaring `FTP_PASS`, which the sandbox then silently scrubbed from a
  routine's util subprocess (its FTP creds never arrived). The gate now resolves grouped
  tuple/list constants; a full library sweep found `ftp` was the only offender (its `secrets:`
  line is fixed in the utils library). Credentials set in Settings → Secrets now reach the util.

## [0.78.1] — 2026-07-20

### Added
- **Settings → Connections: each provider row now links straight to where you create its OAuth
  app** ("create app ↗" → the provider's dev console: Notion my-integrations, Google Cloud
  credentials, Slack apps), via a new `console_url` on the provider registry. No more hunting for
  the right page.

## [0.78.0] — 2026-07-20

### Added
- **A sticky side table-of-contents on long pages** (`static/components/toc.js`): on wide viewports
  a fixed rail parks in the right margin (mirroring the run/conversation rails, same 1560px
  breakpoint), listing the view's `<h2>` sections with click-to-scroll and the in-view section
  highlighted. Mounted generically by the router for any view with ≥2 headings; skipped on views
  that already carry their own rail/nav. Hidden below 1560px.

### Changed
- **Settings → Connections: the Public URL field now pre-fills from the browser's origin** (when
  it's https and nothing is saved), so you rarely type it — the URL you reached the console at is
  the redirect base you want.

## [0.77.1] — 2026-07-20

### Fixed
- **Settings → Connections: the OAuth base-URL field was mislabeled "Redirect URL"**, which invited
  pasting the full `…/oauth/callback` (doubling the path). Renamed to **"Public URL"** with a
  "base, not a path" note, and the card now derives + shows the exact callback
  (`<public_url>/oauth/callback`) to register in the provider, with a copy button.

## [0.77.0] — 2026-07-20

### Added
- **OAuth connections** (docs/oauth-connections.md): connect an external service account (Notion
  first) via OAuth in the web UI, and a routine acts on its behalf headlessly. A connection is a
  RESOURCE binding (routine.yaml `connections:` provider→account, like `models:`), never a
  capability. Consent + refresh run in the daemon/web process; a run only READS a short-lived
  access token from disk (the engine↔daemon boundary is filesystem-only).
  - `oauth/providers.py` — provider registry (Notion implemented: auth-code + PKCE, long-lived
    token, no device flow; Google/Slack scaffolds; app creds in the Secrets store as
    `<PROVIDER>_OAUTH_CLIENT_ID`/`_OAUTH_CLIENT_SECRET`). `oauth/store.py` — the daemon-owned
    `connections.json` (0600, single writer + lock, metadata-only listing).
  - `web/settings/oauth.py` + a PUBLIC `GET /oauth/callback` (bearer-exempt like the webhook route;
    the per-flow `state` + PKCE-S256 are the guards) + a Settings → Connections card. New
    `ServerConfig.public_url` builds the redirect URI (e.g. a Tailscale Serve https URL).
  - `daemon/oauth_refresh.py` — `OAuthRefreshManager` refreshes expiring tokens on the scheduler
    tick, persists refresh-token rotation, flags `needs_reauth` + notifies on rejection (a no-op
    for non-expiring providers like Notion).
  - Engine injection: a routine's bound connections reach a util as `<PROVIDER>_ACCESS_TOKEN` via
    `run_util(extra_secrets=…)` / `_child_env`, but ONLY if the util declares the var — the
    declared-only sandbox invariant, extended to engine-provided tokens. The `notion` global util
    was revised to read `NOTION_ACCESS_TOKEN`.

## [0.76.3] — 2026-07-20

### Changed
- **`ruff check` and `mypy` now run inside pytest (`tests/test_quality.py`), so the one gate the
  engine actually enforces covers them.** CLAUDE.md requires both green on the FULL repo every
  commit and relies on pre-commit — but the daemon commits programmatically (git hooks bypassed),
  pre-commit is not installed on the deployment, and self-audit's only hard gate is `pytest-run`.
  The F97 external audit found the tree had been RED (11 ruff + 8 mypy errors from the Jul-19
  toolchain bump, ruff 0.15.21 / mypy 2.3.0) across 0.72–0.76 with every commit sailing over it,
  because a run only lints the files it changed. Running the two gates as tests means a red
  full-repo can never be committed silently again — a red suite reverts, and the checks also cover
  the live tree's pending edits. Skips cleanly in a minimal env without the dev tools; the commit
  gate always has them. (Companion to the same audit's 0.76.2 fixes.)

## [0.76.2] — 2026-07-20

### Fixed
- **F97, actually fixed — the util-stats snapshot dir was never writable in the container,
  and the four-release chase (0.68.0–0.68.3) diagnosed the wrong `~/.local`.** External audit
  on the host (reviewer-reserved for 2026-07-20) settled it: the snapshot file
  (`~/.local/state/routine-scheduler/util-stats.json`) has **never existed** — not on the host,
  not in the container. The daemon runs as uid 1000 in Docker, and the container's
  `/home/mark/.local` is `root:root`: the entrypoint (`deploy/docker-entrypoint.sh`, run as
  root) does `mkdir -p ~/.local/share/routine-scheduler-libraries` for that bind mount —
  creating `~/.local` + `~/.local/share` as root — then chowns only the *leaf* to `mark`, so
  `~/.local` and `~/.local/state` stay root-owned and the writer's `mkdir(~/.local/state/
  routine-scheduler)` raises `PermissionError` (reproduced: `docker exec -u 1000 rsched mkdir -p
  …/state/…` → *Permission denied*). The 0.68.3 fix chowned the **host's** `~/.local`, which is
  irrelevant — `~/.local/state` is not a bind mount, so the daemon writes into the container's
  own root-owned tree; and the routine's "stale mount-namespace" note was a misdiagnosis (the
  `util-stats` util's 404 was correct — the file genuinely was absent). Fix: add
  `~/.local/state` to the entrypoint's chown loop, so the uid-1000 daemon can create any XDG
  state subdir it needs (now and for future consumers). Takes effect on image rebuild +
  container recreate.
- **A cleanly-finishing engine subprocess's WARNING/ERROR logs no longer vanish — the reason
  F97 hid for four releases.** The daemon spawns each `engine-run` with `stdout=DEVNULL,
  stderr=PIPE` and only surfaced that stderr on a *crash* (`_reap`), so the 0.68.1/0.68.3
  snapshot-write breadcrumb — emitted by the engine on a successful finish — was silently
  dropped ("never silent again" was still silent). `_reap` now re-emits a tail of any
  WARNING/ERROR/CRITICAL/traceback lines (new pure `_notable_stderr` helper, tail-capped so a
  chatty run can't flood the log) into the daemon log (→ `docker logs`), so a persistent
  non-fatal failure is diagnosable from the outside. Unit + integration tested
  (`tests/test_scheduler.py`).

## [0.76.1] — 2026-07-20

### Fixed
- **A resumed run reset its per-run telemetry counters to the resumed leg's own tally
  (self-audit F131/F132; bug report from routine-improver 2026-07-20).** A finish→reopen (an
  operator message injected after an authored finish) starts a fresh `RunContext`, and `boot`
  rehydrated the token-spend base, grounding set, and turn base from the transcript — but NOT
  the cumulative counters mirrored to `status.json` and the finish event. So a reopened run's
  `status.json` showed `utils: {}` and `asks_deferred: 0` (plus `schema_retries` /
  `schema_forcefails` / `referrals`) despite the pre-finish leg's real activity — e.g.
  global-utils-review 2026-07-19 recorded four real util calls yet reported an empty util
  histogram, nearly tripping a false finish-claim-of-unperformed-work flag. Fix: on resume,
  `boot` reseeds these counters from the prior leg's `status.json` (the run dir is reused
  across legs) before the first `write_status` overwrites the file — the same
  cumulative-across-legs guarantee `usage_base` already gives token spend. The GLOBAL
  util-stats snapshot was always correct (it is transcript-derived); this repairs only the
  per-run `status.json` and finish event. New `history.prior_counters` helper (unit-tested) +
  a resume integration test.

## [0.76.0] — 2026-07-19

### Fixed
- **The `remove_util` action permission reverted to unchecked on every "Save permissions"
  (self-audit F130; operator bug report filed from global-utils-review 2026-07-19).** Enabling
  the `remove_util` toggle never persisted, so an operator-approved util removal
  (`pagedrop-publish`, unused and now failing its own selftest with 403) stayed un-executable
  for 5 consecutive runs. Root cause: `floor_capabilities` keeps a gated action only when a HELD
  permission doc's `requires.actions` names it, but `permissions/util-authoring.md` was seeded
  before `remove_util` existed and lists only `write_util` — so the floor stripped `remove_util`
  on every save. Fix: `floor_capabilities` now also keeps a gated kind whose canonical source
  permission (`_DEFAULT_KIND_SOURCE`, e.g. `remove_util → util-authoring`) is held, closing the
  "library predates the kind" gap generically. The RAISE (`capabilities_for`) is unchanged, so
  merely holding util-authoring does NOT auto-enable `remove_util` — it stays an explicit opt-in
  that now persists.

### Added
- **`write_file` observation reports the file's total `size` after the write (self-audit F129;
  bug report: an `append:true` appeared to overwrite a file's existing content).** The
  observation carried only `bytes` written, so an append that silently overwrote was
  indistinguishable from a genuine append. It now includes `size` (total on-disk bytes) and the
  append observation reads *"wrote N bytes … (appended; file now M bytes)"* — a true append shows
  `size == prior + bytes`, an overwrite shows `size == bytes`, making the class provable from the
  observation alone. (`do_write_file`'s append path itself is correct — `open("a")` — so no
  overwrite was reproducible in code; this is the diagnostic for a future occurrence.)

## [0.75.0] — 2026-07-19

### Added
- **Reject an ok-finish that CLAIMS a high-signal action the run never took (reviewer AUDIT
  decision D31=B; self-audit finding F127).** A routine wrote *"Filed report_bug to
  self-audit"* in its finish summary while taking no `report_bug` action — narrated unperformed
  work that the old fabrication guard (which only rejects an ok-finish taken as the very
  *first* action) let through. New `src/rsched/engine/finish_guard.py` `unbacked_action_claims()`
  scans a top-level ok-finish summary for the literal engine token of `report_bug` / `ask_user`
  / `schedule_run` bound to an affirmative completion verb; when that action was never taken
  this run the finish is rejected with an instruction to either take the action or drop the
  claim. Deliberately narrow (precision over recall on the shared run path): only literal action
  tokens (never natural-language paraphrases), negations are excluded, and **meta routines**
  (tag `meta` — self-audit, routine-improver, config-optimizer, token-lab, clarification) are
  EXEMPT because their job is to quote and analyse *other* runs' actions (a universal check would
  false-reject the auditor's own summaries). Covered by `tests/test_finish_guard.py` (incl. the
  real radar summary as the positive) and a `tests/test_loop.py` integration test.

## [0.74.1] — 2026-07-19

### Changed
- **Auto-rerun the flaky Playwright UI suite (reviewer AUDIT decision D30=A).** The browser
  UI tests are non-deterministic under `pytest-xdist` (browser/timing/shared-resource
  contention between parallel workers occasionally reds a genuinely-passing test on a
  full-suite run — F120), which corrodes the hard test-gate. `pytest-rerunfailures>=14.0` is
  now a dev dependency and a `pytest_collection_modifyitems` hook in `tests/ui/conftest.py`
  applies `flaky(reruns=2)` to every `tests/ui` test (scoped there so the rest of the suite
  keeps failing fast). Reruns fire ONLY on failure — an intermittent blip passes on retry, a
  real regression still fails all attempts. The `flaky` marker is registered in `pyproject.toml`
  so it is warning-clean under `filterwarnings=error` even while the plugin is absent.
  - **Note on activation:** declaring the dep in `pyproject.toml` does not install it into the
    project venv `/opt/rsched-venv`, which is read-only to routines; the reruns stay **inert**
    until the venv owner runs `uv sync` (a one-time out-of-band step). Until then the wiring is
    committed and the gate is unaffected. The earlier hard blocker — that merely adding the dep
    made `uv run` try to sync the read-only venv and crash the gate — is resolved out-of-band by
    the `pytest-run`/`rsched-lint` utils' `uv run --no-sync` fallback.

## [0.74.0] — 2026-07-19

### Added
- **`report_bug` — an ungated, default-on "report potential bugs" action for EVERY routine
  (reviewer AUDIT decision D29=A).** Any run — at any depth — may file a bug report about the
  scheduler itself (engine, a util's CLI, the web UI, a workflow) with a one-line `title` and
  optional `detail`. It appends a structured entry
  (`{ts, routine, run_id, title, detail}`) to `<routines_home>/.control/bug-reports.jsonl`
  (new `rsched.bug_reports` module, best-effort append modeled on the health-events log) and
  does not interrupt anyone or reach the user. `report_bug` joins `finish` in the new
  `ALWAYS_KINDS` set: it bypasses both the workflow `tools:` allowlist and the capability
  gate (it is not a `GATED_KIND`), so it is available to every routine with no capability to
  enable. The self-audit routine's gather-evidence reads this stream each run and turns
  unresolved entries into findings (recipe wiring tracked separately for the routine-improver).
  New action schema fields `title`/`detail`; handler `interact.handle_report_bug`; observation
  rendering; composer + `docs/prompt-anatomy.md` action-list entries. Tests in
  `tests/test_report_bug.py` (+ the `report_bug` case in the `test_actions.py` valid-actions
  matrix).

## [0.73.0] — 2026-07-19

### Fixed
- **Decision answers now sync to the Audit page too (reviewer AUDIT note: "responses to
  decisions are still not synced everywhere the decision surfaces").** The Audit tab
  reconstructed a decision's answered-state from the still-queued `pending_feedback` inbox
  messages ALONE, so the moment a self-audit run consumed a decision's feedback message the
  Audit page re-presented that answered decision as `open` — while the Decisions page kept it
  hidden via the durable `audit/decisions-answered.json` marker. `/api/audit` now also emits
  `answered_decisions` (the marker ids answered at-or-after the report's `generated`, the same
  rule `_audit_decisions` uses); `static/views/audit.js` reads it and shows those decisions as
  **answered** (not open), hiding their options. The two surfaces now agree.
  `web/api_audit.py`, `static/views/audit.js`.

### Changed
- **`schedule_run` unknown-target now teaches the caller which slugs are valid.** Arming a
  one-shot on an unknown routine returned a bare `unknown_target` — a scheduling routine
  guessing a sibling's slug (observed: `train-seat-finder-scheduler` burned turns guessing
  `bahnbonus-seat-finder`/`-position-finder` before the real `bahnbonus-seat-position`, even
  building a new util + asking the user). The `unknown_target` observation now carries
  `valid_targets` (every sibling routine slug) and `suggestions` (fuzzy close matches), and the
  formatted observation prints "Did you mean …? Valid target slugs: …".
  `engine/interact.py`, `engine/observations.py`.

## [0.72.1] — 2026-07-19

### Fixed
- **Conversation title/tags now use the conversation's OWN model, not the system model.**
  `conversations.autolabel` resolved the title+tags via `EndpointRegistry.for_system()`, so a
  conversation pinned to an uncensored model still had its title generated by the default
  system model — which could refuse (e.g. a title reading "denied request"). It now resolves
  `for_model("main", <the conversation's models>)` — the same model its replies use — with the
  system model kept as the fallback when the conversation pins none. (Reviewer AUDIT note.)

## [0.72.0] — 2026-07-19

### Added
- **Schedule-once UI card (D28) — the frontend for the 0.71.0 one-shot backend.** The routine
  page now has a **Schedule once** card beside Triggers: a local-time datetime picker + reason
  field arms a one-shot (`POST /api/routines/<slug>/schedule-once`, the naive local time is
  converted to an absolute UTC instant client-side), the armed one-shots list with a Cancel
  button (`DELETE …/<id>`), and the daemon fire ledger (`fired N× · last …`).
  `static/components/schedule-once.js`, wired into `static/views/routine.js`.
- **Armed one-shots on the dashboard week strip.** `GET /api/schedule/week` now returns a
  `one_shots` list per routine (armed schedule-once fires inside the window) alongside the
  recurring cron `fires`, and includes a routine that has *only* a one-shot armed (no cron).
  The week grid renders one-shots as distinct **hollow** dots. `web/api_schedule.py`,
  `static/components/weekgrid.js`, `static/views/dashboard.js`.

### Tests
- `tests/ui/test_schedule_once.py` (Playwright): a seeded one-shot renders, Cancel clears the
  spool request, and arming from the UI writes a new request. `test_schedule_once.py` gains a
  week-strip API test (armed one-shot surfaces in `one_shots`; a far-out one does not).

## [0.71.0] — 2026-07-19

### Added
- **`schedule_run` action + `scheduling` permission — one-shot future runs (D27).** A routine
  holding the new `scheduling` capability can arm a routine to run ONCE at a future instant,
  then never again — the missing case between cron (repeats forever) and a manual run (now).
  `schedule_run` takes `target` (routine slug; **self-target always allowed**, another routine
  is the cross-routine case the permission authorizes), `fire_at` (an absolute ISO-8601 UTC
  instant or a relative offset like `+3d` / `+2h` / `+30m`), and `reason` (injected into the
  target's inbox just before it fires); `cancel: true` (+ optional `id`) calls it off.
- **Daemon-owned request spool + `OneShotManager`.** Armed one-shots live in
  `<routines_home>/.control/schedule-once/<slug>/req-*.json` (NOT `routine.yaml` — config
  stays the user's; the engine writes the spool un-sandboxed like `write_util`). A new
  `OneShotManager`, ticked beside `TriggerManager` after the cron loop, fires each due request
  ONCE (same draining/one-run-per-routine gates as cron/trigger fires) then **deletes** it —
  consumption is the non-repeating guarantee (no self-disabling cron, no config rewrite). A
  missed one-shot make-up-fires on the next daemon start; `expires_at` bounds staleness.
- **API:** `POST` / `GET` / `DELETE /api/routines/<slug>/schedule-once` — arm, list armed +
  fire ledger, and cancel from the routine page (the user path beside the routine's own arming).
- Full design + rationale: `docs/schedule-once.md`.

## [0.70.1] — 2026-07-19

### Fixed
- **New-routine draft field no longer refills with the last routine's task (F110).** The
  `#/new-routine` task textarea is form-persisted (a half-typed task survives a refresh —
  desired), but `static/views/new-routine.js` never forgot the draft once a clarification
  started, so the next visit restored the previously-created routine's text. It now calls
  `forgetField(ta)` on a successful start (the documented submit-then-forget pattern), so the
  field is empty on the next visit while still surviving plain navigation. Covered by
  `tests/ui/test_flows.py::test_new_routine_draft_is_forgotten_after_start`.

## [0.70.0] — 2026-07-19

### Added
- **`remove_util` action — routine-executable util curation (D25).** The engine gains a
  `remove_util` action mirroring `write_util`: a routine holding the **util-authoring**
  capability can now DELETE a global util, not just create/revise one. Like `write_util`,
  the removal runs un-sandboxed engine-side (`utils_lib.remove_util_file`, committed so it is
  recoverable from git history) — the counterpart the library previously lacked, which left
  removal only to the web UI or a host shell (F108: the util sandbox jails the library dir for
  every routine, even `shell`). The action **refuses** while any other util still declares the
  target on its `calls:` line (`utils_lib.referenced_by`, mirroring the `gu remove` no-callers
  guard), asks for approval unless the routine's write_util policy is `never`, and is declined
  for sub-workflows. Gated as a new `GATED_KIND` sourced from `util-authoring` (the permission
  doc's `requires.actions` now lists `write_util, remove_util`); stripped from detached tasks
  like `write_util`. Covered by `tests/test_remove_util.py` (helper, validation, capability
  gate, and the remove / refuse-callers / missing / subrun-decline handler paths).

## [0.69.1] — 2026-07-18

### Fixed
- **Audit page now renders the report's own markdown (F105).** `static/views/audit.js`
  never imported `md.js`, so a finding/decision `detail`, the top summary, and changelog
  entries showed their block markdown (lists, `code`, tables) as literal pre-wrapped text —
  the same gap F104 fixed on the Decisions page. Those four prose surfaces now render via
  `md()` (the sanctioned HTML-escaped innerHTML path); `F/D` ref-links still linkify through
  the rendered output. Covered by `tests/ui/test_flows.py::test_audit_detail_renders_markdown`.

## [0.69.0] — 2026-07-18

### Added
- **New "Summary" tab — each routine's latest finish message in one glance surface.** A
  sibling to the Decisions inbox (which collects what the routines need *from* you); Summary
  collects what they last *said* — the most recent run's finish summary per routine, newest
  first, with the finish markdown rendered (`md()`), a jump-to-run link, and a per-item
  mark-read control. Dismissing an item persists under `routines_home/.control/
  summary-read.json`; a newer run of that routine automatically resurfaces it. New route
  `#/summary` + `static/views/summary.js` + nav/breadcrumb, backed by a new read-only
  `GET /api/summary` and `POST /api/summary/{slug}/read` (registry read-model). The
  Decisions/`#/questions` inbox is unchanged. (Reviewer decision D21, option A.)

## [0.68.3] — 2026-07-18

### Fixed
- **util-stats snapshot failure was silent — the ACTUAL F97 root cause is a filesystem
  permission, not a `util_stats()` raise.** Proven this run by running the daemon's own venv
  (`/opt/rsched-venv/bin/python`, v0.68.2): `/home/mark/.local` is owned `root:root` (mode
  755), so the daemon (uid 1000 `mark`) cannot `mkdir ~/.local/state`; the snapshot write
  raises `PermissionError` — which **is an `OSError`** and was swallowed by
  `write_util_stats_snapshot`'s `except OSError: pass` with no log. Every util_stats-internal
  fix across 0.68.0–0.68.2 was treating the wrong layer. The real fix is operational (`chown
  mark:mark ~/.local`); code-side, the writer now leaves a `log.warning` breadcrumb naming
  the unwritable path so this class of misconfiguration is never silent again.
- **Markdown in Decisions-page items now renders.** `static/views/questions.js` rendered an
  OPEN question's text as raw `textContent` (and an answered one inline-only), so a meta
  (self-audit) decision's rich `detail` — lists, GFM tables, `code` — showed literal markup.
  Meta decisions now use the block renderer (`md()`); ordinary short prompts keep the
  inline-only subset (`mdInline()`). Reviewer-reported 2026-07-18.

## [0.68.2] — 2026-07-18

### Fixed
- **util-stats snapshot STILL never materialized — the real F97 root cause.** 0.68.1 only
  guarded a corrupt *transcript*, but the snapshot dir (`~/.local/state/routine-scheduler/`)
  never existed at all on the deployment even after two qualifying root-run finishes under
  0.68.1. Cause: `write_util_stats_snapshot` evaluates `util_stats(server)` *before* its I/O
  guard, and `util_stats()` still raised on a home it could not enumerate — `_backfill`
  iterates BOTH `routines_home` and `conversations_home`, and its per-home directory walk
  (`iterdir`/`stat`) was unguarded (a routines_home-only repro never exercised it). Two
  fixes: (1) `_backfill` now wraps each home's enumeration in `try/except` (skip+log a home
  it cannot read, keep the other home's counts); (2) `write_util_stats_snapshot` wraps the
  `util_stats()` call so any compute failure still writes a degraded, `error`-marked
  snapshot — the file (and its parent dir) is ALWAYS created, making the residual observable
  next run instead of a silent absent file. Tests:
  `test_backfill_tolerates_unreadable_home`, `test_write_snapshot_degrades_when_util_stats_raises`.

## [0.68.1] — 2026-07-17

### Fixed
- **util-stats snapshot no longer silently disappears when one transcript is corrupt
  (F97).** The run-finish hook (`engine/runtime.py`) that refreshes
  `util-stats.json` swallows every exception so telemetry can never break a run — but
  `util_stats()` computed the whole snapshot *outside* the write's own guard, so a single
  unreadable/corrupt transcript raised straight through the hook and produced **no snapshot
  at all** (the file stayed missing after several qualifying root-run finishes). `_backfill`
  now wraps each `_scan_transcript` in try/except: a bad transcript is skipped and logged,
  every other source still counts. The swallowed-exception `pass` in the runtime hook is now
  a `log.warning(..., exc_info=True)` so a future failure leaves a breadcrumb instead of
  vanishing silently.

### Changed
- **Default `ask_timeout_min` raised 5 → 480 (8h), the deployment norm (F102).** The old
  5-minute default seeded a blocking-ask timeout trap into every newly-created routine — a
  blocking question would auto-continue on its stated default after only 5 minutes. It
  recurred twice (`scheduler-improvement-research`, `global-utils-review`), each hand-fixed
  by the user, who approved raising it deployment-wide (config-optimizer
  `q-20260717-191914-24`). All mature routines already run 480; this fixes the root cause for
  future routines. Existing `routine.yaml` files are engine-sealed to runs and unchanged.

## [0.68.0] — 2026-07-17

### Added
- **Persisted util-stats snapshot — one source of truth for the Stats tab and routines
  (F97).** The per-util execution stats the Stats tab shows (`util_stats()`: library git
  dates + the durable workflow-usage stream + transcript backfill) are now written to
  `$XDG_STATE_HOME/routine-scheduler/util-stats.json` (default
  `~/.local/state/routine-scheduler/util-stats.json`) on every root-run finish, via the new
  `util_stats.write_util_stats_snapshot(server)` (atomic, best-effort — a stats write never
  breaks a run). The XDG state location is deliberate: a Landlock-jailed util subprocess can
  read `~/.local/state` but not the daemon's `routines_home/.control` area, so this is the
  one place a routine's util can reach the same numbers the web page computes. Unblocks the
  `global-utils-review` (util-improver) routine, whose first run stalled with "stats source
  UNRESOLVED" because the figures were reachable only through the token-gated `/api/stats`.
- **`util-stats` global util** (library) reads that snapshot and emits it (`--json` for a
  routine to consume, a table for humans, `--name` to filter one util) — the review
  routine's stats source.

## [0.67.4] — 2026-07-17

### Fixed
- **Run-page question form now updates when a run re-asks within the same phase (F93).**
  The run SSE tail (`web/sse.py`) emitted a `state` event only on a `(state, phase)` change,
  so a NEW pending question with unchanged state+phase never reached an open run page — the
  question form (which re-renders only on a `state` event) could keep showing a stale/absent
  form, forcing answers onto the Decisions page. The dedup key now also includes the pending
  question's `qid`, so a changed (or cleared) question always rides its own event.

### Added
- **`.ui-traces` diagnostics for the new-routine clarify run page (F93).** The setup panel
  records which stage it renders (`setup-stage`, with run state + `has_result`) and the run
  view records real transitions of the shown question id (`run-question`) — so a clarify run
  reported stuck on the chat frame (no create form) or missing its question form leaves a
  diagnosable trail for the self-audit's improve-ui lens.

## [0.67.3] — 2026-07-17

### Fixed
- **Settings → LLM endpoints: the system-model description now states its role-fallback
  behaviour.** The blurb described the system model only as the fallback for "setup-time
  work that isn't a routine yet" (the clarify wizard + workflow generation), omitting that
  it is ALSO the fallback for any routine role (`main`/`subroutine`/`tool_call`) left unset
  — which `config.py`, `EndpointRegistry.for_model`, and `docs/endpoints.md` all document.
  It now says so, and points at the separate per-model `fallbacks` failover chain, so the
  two fallback mechanisms aren't confused. UI-text accuracy only; no behaviour change.

## [0.67.2] — 2026-07-17

### Fixed
- **A conversation now sees its own task in the system prompt.** `build_system_prompt`
  appended the `# INSTRUCTION` section only at `depth > 0`, and the depth-0 ownership prose
  declared the WORKFLOW the "single source of truth for what to do". But a **conversation**
  runs at depth 0 while its task IS its first message (`instruction.md`) and the `converse`
  workflow only defines HOW to work a reply — so the agent was handed the converse pattern
  with its actual task dropped from the prompt, and on the first turn had to go hunting for
  `instruction.md` to understand what it was even asked to do. The composer now detects a
  conversation by HOME (its dir under `conversations_home`, matching `daemon.runner`, since
  the yaml `kind: conversation` is dropped by pydantic), carries the `# INSTRUCTION` section
  for it, and gives it conversation-specific ownership prose that names `instruction.md` as
  the task, frames later user messages as refinements of it, and preserves multi-turn /
  sub-work replies. Scheduled routines are unchanged (their task stays compiled into the
  recipe). `docs/prompt-anatomy.md` updated to match. Reported via the audit feedback channel.

## [0.67.1] — 2026-07-17

### Fixed
- **Dashboard routine card no longer counts snoozed questions as open.** The card's
  "N open questions" count (`web/api_routines.py`) ignored `snoozed_until`, so a question
  snoozed into the future showed as an open question on the card while the Decisions tab
  badge and the Decisions page — which hide snoozed items by design — showed nothing; the
  two surfaces disagreed. The card now derives both `open_questions` and `decision_backlog`
  through the same snooze-aware filter (`_awaiting_questions`, reusing `_snooze_active`), so
  a snoozed decision stays quiet everywhere and the card count can never contradict the
  badge. Reported via the audit feedback channel.

## [0.67.0] — 2026-07-17

### Changed
- **The `meta` tag is now a plain tag — no special-casing.** Previously `meta`-tagged
  workflows were hidden from the spawn/subtask capability catalog and from wizard
  suggestions, meta routines were hidden on the dashboard by default, and the `meta` tag was
  sorted first and styled specially. Now: meta workflows appear in the spawn catalog
  (`engine/capabilities.py`), in the wizard clarifier's candidate patterns
  (`web/wizard_store.py`) and in `suggest()` (`workflows/suggest.py`, the `INTERNAL_TAG`
  filter is gone); the dashboard no longer hides meta routines by default and sorts/styles
  the tag like any other (`static/views/dashboard.js`, `library.js`, `util.js`). Bundled meta
  routines still install **disabled** on a fresh instance (a seed-install safety default, not a
  tag behaviour — enable each on its routine page). Self-audit decision D15.

## [0.66.1] — 2026-07-17

### Fixed
- **`rsched lint` works under the util sandbox.** The 0.63.0 Landlock sandbox deliberately
  hides `~/.config/routine-scheduler/` (secrets live there), so `rsched lint` — which called
  `load_server_config()` only to find `libraries_home` — crashed with `PermissionError` when
  invoked from a sandboxed util (e.g. the `gu rsched-lint` helper self-audit uses). `lint`
  now accepts `--libraries-home DIR` to lint a library directly, skipping the server-config
  read; the library dir itself is already visible to utils (it is `utils_home`). Self-audit
  decision D16.
- **Restored the green test gate**: 0.66.0's new per-util telemetry (`ctx.count_util`) had
  broken a `tests/test_utils.py` fixture whose fake context lacked the method.

## [0.66.0] — 2026-07-17

### Added
- **Outcome-gated self-improvement: recipe-version health + one-click roll-back.** Every
  run is stamped with the recipe VERSION that produced it — the last commit touching
  main.md / stages/ / traits/ / tuning.yaml (`rsched/recipes.py`), never the state-noise
  HEAD; uncommitted recipe edits (the routine-improver's) are snapshotted into a
  recipe-only `recipe: pre-run snapshot` commit at run start, so every version is a real,
  revertable commit. The stamp lands in status.json (`recipe_commit`) and the durable
  workflow-usage record, so health history outlives run retention. The routine page's new
  **Recipe health** section (`GET /api/routines/{slug}/health`, `rsched/run_health.py`)
  buckets runs by version — outcomes, fail rate, median turns/tokens, deferred-question
  churn (`asks_deferred`, engine-counted) — with pre-stamp history date-attributed and
  marked `date-mapped`. A deterministic regression heuristic (no stats libraries; every
  constant justified in the module: 5-run windows, ≥3 runs to judge, fail-rate +0.4,
  1.5× median growth with +5-turn / +20k-token floors) flags the newest recipe change
  when its runs are clearly worse. **Flag-first**: the roll-back is the user's click
  (`POST /api/routines/{slug}/recipe/revert`) — it restores ONLY the recipe files as a
  new commit (never routine.yaml or state), 409-guarded while a run is active; the
  routine-improver never auto-reverts.
- **Per-util execution stats on the Stats tab.** Every util call is counted by outcome in
  the engine (`RunContext.util_stats`): ok / error / usage_error (exit 2 — argparse's
  bad-arguments convention) / missing / denied / rejected. Denials are counted at the
  validation seam (`engine/actions.util_rejection_outcome`) — a denied call is corrected
  inside the schema-retry cycle and never becomes a turn, so the executor alone would
  never see it; user slash commands count identically; `list`/`show` discovery never
  counts. The per-run breakdown rides status.json and the workflow-usage record (`utils`
  payload extension — always present on new records, marking the run as counted).
  `rsched/util_stats.py` joins that stream with the library's git history (created / last
  revised per util, one memoized `git log` walk) and a stat-fingerprint-memoized
  transcript backfill for pre-stream runs. The new **Global utils** table answers, per
  util: exists since when, last revision, how often executed / successful / mis-called /
  permission-blocked, first & last execution — honest about unknowns (never-executed
  utils, pre-stream rejection history).

### Docs
- New Help guide `docs/run-analytics.md`; CLAUDE.md (routines-on-disk + workflow-usage
  paragraphs) and README updated.

## [0.65.0] — 2026-07-17

### Added
- **Per-model output `max_tokens` in the catalog.** `ModelConfig.max_tokens` (and an
  `EndpointConfig.max_tokens` default it inherits) resolves into `ModelRef.max_tokens`,
  with a generous engine fallback (`DEFAULT_MODEL_MAX_TOKENS` = 16,384). Every engine call
  site — turns, the `llm` action, compaction archival, refusal referral — now sends the
  resolved per-model value instead of a hard-coded 16,384; `claude-cli` maps it to
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. Settings surfaces an audit flag (`max_tokens_warning`) on
  any model whose limit is unset (riding the generic default), implausibly low (< 4,096), or
  larger than the model's context window — so "every model has its max tokens set correctly"
  is auditable at a glance, mirroring how unset secrets are flagged.
- **Ordered model failover chains with provider cooldowns.** A catalog model may declare
  `fallbacks:` — an ordered list of catalog model names (non-transitive) the engine fails
  over to when the model fails hard (its transport retries are exhausted, or the error was
  never retryable). `routine.yaml` still maps each role to ONE catalog name — editing a
  catalog model's chain updates every routine that references it, so no config-shape
  migration. Two cooperating levels (`endpoints/failover.py`): a hard `EndpointError` marks
  the `(endpoint, provider model id)` *cooling* for 5 minutes (centrally, in
  `InstrumentedEndpoint` — the one seam every LLM call crosses), and every role resolution
  (`for_model` / `for_uncensored` / `for_system`) picks the first not-cooling chain member;
  the turn-completion seam (`engine/completion.py`) additionally advances down the chain
  MID-TURN on a hard failure. The switch is logged visibly as a transcript `error` event
  carrying a `failover` payload (`from` / `to` / `cooldown_s`) — a payload extension, not a
  new event type — and each turn's `usage.model` records the model that actually served it,
  so spend attribution and `status.json`'s live model stay truthful. Chain exhausted → the
  run fails exactly as before; models without `fallbacks` behave exactly as before.
- **Settings credential-source indicator.** Each endpoint card now shows which rung of the
  credential ladder is live — inline key / secret `<VAR>` / env file / none — and warns
  loudly when an inline key **shadows** a set secret (the inline key wins, so editing the
  secret changes nothing until it's removed). Computed by label-only mirrors
  (`api_key_source` / `token_source`) sitting beside the resolvers they track; key values
  are never returned through the API. The documented precedence (inline → secret → env file)
  is unchanged.

## [0.64.0] — 2026-07-17

### Added
- **Instance-wide full-text search.** One box in the app header (`/` or Ctrl-K) over
  everything the instance ever wrote — run transcripts (say/note narration, finish
  summaries, questions + answers, user messages; gzipped archives and subrun trees
  included), result.md reports, compaction `history/` archives, LEDGER.md, `.memory/`
  notes, durable decision records, and recipe files — across routines AND conversations.
  Hits rank by BM25 (porter stemming, so `playbook` finds `playbooks`), group by
  routine → run with snippet-highlighted matches, and deep-link into the run /
  conversation / decisions / routine views. Backend: an SQLite FTS5 index (stdlib
  `sqlite3`) at `<routines_home>/.control/search.sqlite3` — a pure cache of the flat
  files (delete it, it rebuilds), kept fresh behind per-file stat fingerprints (newest
  runs first, budget-bounded passes with a per-pass progress guarantee) by a daemon
  maintainer task plus a ~2s query-time top-up; rows for retention-pruned runs are
  pruned. Raw FTS5 syntax passes through when it parses; anything else falls back to
  escaped phrase terms — a malformed query is a 400, never a 500. New: `search/`
  package, `web/api_search.py` (`GET /api/search?q=`), the header
  `components/searchbox.js` (compact icon at rest, expands over the nav on focus),
  docs/search.md.

## [0.63.0] — 2026-07-17

### Added
- **Util-subprocess sandbox (Landlock).** Every util now runs inside a Landlock jail
  (`rsched/landlock.py` — a stdlib-ctypes binding + strict child wrapper; `rsched/sandbox.py`
  — the policy layer) whose visible filesystem is derived from the run's permissions: the
  routine dir + its `fs_read_roots`/`fs_write_roots` read/write, plus the toolchain a util
  needs to execute (interpreter, uv + its caches, the util library, system trees). The
  daemon-user HOME — `~/.config/routine-scheduler` (the secrets store), `~/.credentials`,
  `~/.ssh` — is invisible, closing the `gu page-fetch file:///…/secrets.env` read-and-exfil
  bypass. Verified working inside the production Docker container (Landlock ABI 4, filesystem
  + TCP, default seccomp). New server config `sandbox: strict | permissive | off` (default
  **permissive**: jail when the kernel supports it, warn + run bare when it doesn't; strict
  refuses to run utils unsandboxed). See docs/sandboxing.md.
- **Network as a declared util capability.** The util docstring header gains a required
  `net: outbound | none` line (undeclared = none — no TCP); the sandbox denies all TCP
  (Landlock ABI ≥ 4) to a util that declares none. Sibling calls declared on `calls:` resolve
  network + secret needs transitively.
- **Scoped secrets injection.** A util subprocess now receives ONLY the store secrets it (or
  a `calls:` sibling) declares on `secrets:`; every other store key is scrubbed even out of
  the inherited daemon environment (applies in every sandbox mode, no kernel needed). Secret
  detection now also resolves the `VAR = "NAME"` + `os.environ[VAR]` indirection.
- **Never recreate a user-deleted util.** `write_util` for a slug with a deletion in the util
  library's git history is rejected inside the schema-retry cycle (never a turn); the model
  must `ask_user` first and an explicit yes that run unblocks it (`interact.recreate_denial`).
  The boot seed-sync likewise never resurrects a user-deleted seed util.

### Changed
- `utils_lib.run_util` / `selftest` take a `SandboxPolicy`; `header_problems` requires the
  `net:` line; the util-authoring permission doc + prompt CAPABILITIES note the new rules.
- One-shot boot migration (`MIGRATION(expires=2026-08-17)`) stamps pre-sandbox library util
  headers with `net: outbound` (behavior-preserving) + any missing `calls:`/`secrets:`.

## [0.62.0] — 2026-07-17

### Added
- **Event-driven routine triggers (webhook path).** A routine can now fire on an external
  event alongside cron, via a new canonical `triggers:` list in routine.yaml (one shape from
  day one: `{id, type, cooldown_s, …}` — `webhook` implemented, `imap`/`watch_path` reserved
  so the mail/file-drop watchers slot in later without reshaping config). The webhook path:
  `POST /api/hooks/<slug>/<token>` (`web/api_hooks.py`) is the one deliberately
  unauthenticated API route — the per-trigger, server-generated URL token IS the auth
  (constant-time compare, generic 404 with no existence oracle, 64 KiB streaming size cap,
  per-slug rate limit + durable spool cap, payload never echoed, rejections logged). The
  handler only RECORDS events durably in the `.control/triggers/<slug>/` spool; the
  scheduler-ticked `TriggerManager` (`daemon/triggers.py`) turns them into fires, so
  one-run-per-routine, `max_concurrent_runs`, and the restart drain stay the daemon's job.
  **Coalescing**: N events while a run is active/queued/cooling → ONE fire, each event still
  landing as its own inbox message (deterministic filenames → exactly-once across crashes);
  `cooldown_s` (default 60) bounds trigger-fire frequency so a leaked URL can't burn budget.
  A **Triggers card** on the routine page (`static/components/triggers.js`) creates/deletes
  webhooks, copies the hook URL, and shows the per-trigger fire ledger. The library-sync
  export now redacts webhook `token` values in routine.yaml. See `docs/triggers.md`.

## [0.61.0] — 2026-07-17

### Added
- **Run-history heartbeat strip on the dashboard.** Every routine card AND list-view row
  now carries a compact SVG strip of the last 15 runs (`static/components/heartbeat.js` —
  the symmetric PAST view to the week grid's future fires): green ok / amber partial /
  red failed / grey aborted / teal still-running bars, oldest left, newest at the right
  edge, bar height tracking the run's token spend (sqrt-scaled per strip). Hover shows
  ts · outcome · turns · tokens · cost · duration; click opens that run. A routine that
  failed 4 of its last 10 runs no longer looks identical to one green for a month.
  Data path: cards gain an additive `recent_runs` field (`web/api_routines.py`
  `HEARTBEAT_RUNS_N` — a slice of what the registry already parses, no new scanning), and
  status.json gains the additive **`outcome`** field (ok|partial|failed|aborted, stamped
  at run end by the engine) because `state` folds a partial finish into "finished" — the
  strip is where partial becomes visible again.
- **GFM pipe tables + blockquotes in model-authored prose.** `static/md.js` (the one
  sanctioned innerHTML pathway) now renders pipe tables — header row + `|---|` separator
  → `table.list` in a `.tablewrap`, `:---:`/`---:` alignment honored, `\|` escapes, a
  malformed table stays literal text — and `>` blockquotes (grouped, nested via re-parse,
  recursion depth-capped) on BLOCK surfaces: finish summaries, llm replies, artifacts.
  The escape-first security structure is unchanged (everything HTML-escaped before any
  transform; no live HTML); `mdInline` (say narration, questions) stays inline-only.
  The models are TOLD: the composer's finish gloss and the ACTION_SCHEMA `summary`
  description now state that pipe tables and blockquotes render — so tabular results
  arrive as real tables, not ASCII art (`docs/prompt-anatomy.md` and its pin test move
  in the same commit).

### Fixed
- `tests/ui` `test_routine_page_saves`: the tag-removal disk assert waited a fixed 200ms
  — now an explicit poll on the yaml state (`_wait_until`), per the standing
  fix-flakes-with-render-waits rule.

## [0.60.0] — 2026-07-17

### Added
- **⚙ capabilities & budgets on the new-conversation composer.** The same panel the
  conversation header offers now exists BEFORE create — necessary because the first reply
  fires on create, so a permission (e.g. shell), per-reply budget (minutes/tokens), or
  deliberation level toggled post-hoc would miss reply #1. Fed by the new
  `GET /api/conversations/defaults`; the collected `{active, capabilities}` payload rides
  the create request through the same resolve + cascade + floor as the header save, and
  `deliberation` lands in tuning.yaml. The old "⚙ options: project dir, shell" block (and
  the `shell` create form field) is retired — shell is now just one toggle in the panel.
  `permissionsPanel` returns `{node, value}` so it can collect without saving.
- **Audit references are hyperlinks.** Every `F63`/`D14` mention in the audit report's
  prose (summary, findings, decisions) and in the Decisions page's meta items links to the
  card it names: `#/audit?focus=<id>` lands on, scrolls to, and flashes that card
  (`static/components/reflinks.js`; decisions now render read-only cards on the Audit page
  so D-references have a landing target).

## [0.59.0] — 2026-07-16

### Changed
- **The run page is the whole new-routine setup surface (D11 UI half, completing the
  wizard unification).** The bespoke wizard views (`static/views/wizard.js`,
  `static/views/wizard-create.js`, the `#/wizard` route) are retired. A clarify session —
  a real run of the protected `clarification` routine since 0.58.0 — now renders at
  `#/run/clarification:<ts>` like any other run, with a new setup panel
  (`static/components/setuppanel.js`) mounted on top: a slim chat frame (cancel setup)
  while the clarify run is live, then the suggest → create → build stages as run-page
  panels once it finishes. `#/new-routine` (`static/views/new-routine.js`) keeps only the
  draft form plus the in-flight-session resume list; the setup banner, the Decisions
  page's wizard items, and the resume links all point at the run page. `/api/wizard/start`
  and session snapshots return the session's `clarify_run_id` for that navigation.

### Fixed
- **Decision answers for a live clarify run now reach the session** (the missing sibling
  of 0.58.1's inject/converse fix). Answering a clarify ask through
  `POST /api/questions/{qid}/answer` (run page, Decisions page) — and deferring it — wrote
  to `clarification/inbox`, which the live session never polls; both now route to the
  `.wizard-<ts>` workspace inbox via `api_questions._record_dir`, and the answered-state
  derivation reads the same dir.
- **A clarify ask no longer lists twice on the Decisions page.** Since 0.58.0 the same
  blocking question surfaced once via the clarification routine's active run and once via
  the workspace's durable pending record; the wizard scan now dedupes against the real
  run (and stamps items with the clarify `run_id`, badged `wizard`, linking the run page).

## [0.58.1] — 2026-07-16

### Fixed
- **Run-page messages to a live clarify session now reach it** (self-audit D13=B follow-up).
  A clarify session (0.58.0) is a real run whose artifacts live at
  `clarification/runs/<ts>`, but the engine executes it in the hidden throwaway workspace
  `.wizard-<ts>` and polls THAT dir's inbox. `POST /api/runs/clarification:<ts>/inject`
  and `/converse` derived the inbox as `run_dir.parent.parent/inbox` =
  `clarification/inbox`, which the live session never polls, so a run-page message was
  silently dropped. New resolver `wizard_store.session_inbox_dir` redirects a clarify
  run's message to the `.wizard-<ts>` workspace inbox when that workspace exists; ordinary
  routines and legacy session-local clarify runs fall through to `routine_dir/inbox`
  unchanged. (`answer` already routed correctly — the wizard question carries the
  workspace dir name.)

## [0.58.0] — 2026-07-16

### Changed
- **Clarify sessions are now REAL runs of the `clarification` routine** (self-audit D13=B,
  first slice). `wizard_store.create_session` lands the run at
  `routines_home/clarification/runs/<ts>` — a valid `clarification:<ts>` run id with no
  dotfile bridge — and stamps the session's `routine.yaml` with the clarification slug so
  the engine composes that id in status/transcript/usage. `engine-run` gained a `--run-dir`
  override (artifact dir decoupled from the throwaway session workspace, which stays
  hidden as before); `_clarify_run_dir`, cancel/abort, the LLM-sidecar tailer and
  finalize's provenance copy all resolve through the new `wizard_store.clarify_run_dir`.
  Standard run surfaces now apply to clarify chats: the run page (`#/run/clarification:<ts>`),
  SSE tail, transcript paging, registry/dashboard listing, and orphan recovery. Legacy
  sessions and deploys without the template keep the old session-local layout (fallback).
  Remaining slices: run-page panels replacing wizard.js/wizard-create.js, and routing
  run-page *inject* to the session workspace inbox.

## [0.57.2] — 2026-07-16

### Fixed
- **Decision-card option buttons no longer overflow right on narrow screens** (self-audit
  F80). A full-sentence option (e.g. the wizard-unification decision's option B) rendered
  as a single `.btn` with `white-space: nowrap`, so a long label ran off the viewport even
  though the `.row` container already wraps between buttons. New rule
  `.answer-opts .btn { white-space: normal; max-width: 100%; text-align: left }` lets the
  label wrap inside the button and cap at the container width. The shared `answerForm`
  options row is tagged `.answer-opts`. Guarded by a 400px-viewport UI test asserting the
  option button's right edge stays within the question card.

## [0.57.1] — 2026-07-16

### Changed
- **Test suite: 3× faster, +12 behavior tests, coverage 84.8% → 88%** (user order). Speed
  came from diagnosis, not skipping: (1) the app lifespan's pdoc docs build is a to_thread
  task shutdown can only AWAIT — every TestClient/uvicorn test paid ~3s teardown and one
  test a 19s rebuild; `RSCHED_SKIP_DOCS_BUILD` (set suite-wide in conftest, cleared by
  test_docs_build) removes it. (2) `with_retries`' 1s/2s backoff clock is now
  `RSCHED_RETRY_BASE_DELAY`-tunable at call time — dead-endpoint tests exercise the retry
  logic without sleeping (test_with_retries_backoff pins the production delays). (3)
  pytest-xdist `-n auto` is the default (`-n0` for serial); the suite is hermetic per test.
  Wall clock: 224s → ~70s (110s with coverage). New meaningful tests: the CLI command
  surface (validate/abort/lint/suggest/scaffold/run-once exit codes, printed diagnostics,
  disk effects — cli.py 37%→~90%), the executor's real `uv run` util seam incl. the
  grants-aware failure/repair-hint contract, and the playbook edit/detail/delete routes
  (lint-gated PUT, honest 404s). Coverage ratchet raised: fail_under 84 → 87.

## [0.57.0] — 2026-07-16

### Added
- **The note channel** (user order): any action may carry an optional `note` — 1-3
  SELF-CONTAINED lines worth keeping beyond the context window (a confirmed finding, a
  dead end, a fallback plan, an unresolved doubt). The engine (`engine/notes.py`) appends
  it to `state/notes.md` at **no turn cost**, stamped `[run · turn · phase · action]` —
  the stamp is an address into the transcript/history archive where the note's full
  context permanently lives; the contract demands self-containment (the same boundary
  discipline as subrun briefs and finish summaries). Rationale: the one-action-per-turn
  contract priced every dedicated write at a full turn, so insights died with the window
  (bookkeeping deferred under budget pressure, end-of-run writes as reconstructions);
  this is the capture tier under the existing curation tier — `memory_write` keeps its
  turn price as the memory INDEX's quality gate. The state digest carries the file's
  tail into the next run (the full file stays on-demand); notes.md remains ordinary
  prunable state (the improver's hygiene lens treats an un-understandable note as
  broken). `think-on-paper`'s standing paragraph now rides this channel, so the top
  deliberation stop no longer costs an extra turn per decision. The transcript renderer
  shows captured notes as 📌 lines in the turn box.

## [0.56.1] — 2026-07-16

Self-audit (first slice of the D11 wizard→run-page unification: backend structure).

### Changed
- **`api_wizard.py` split into a three-module wizard package (F63 budget).** The 355-line
  route file (over the ~350-line one-responsibility budget) is now three files sharing one
  `APIRouter`: `wizard_common.py` (the router + the helpers both halves use —
  `_wizard_pid`/`_center`/`_wizard_recorder`/`_stop_tailer`/`_wizard_dir`/`_clarify_run_dir`),
  `wizard_sessions.py` (session lifecycle + the clarify-chat stream: list/detail/cancel/start/
  events/transcript/answer), and a slimmed `api_wizard.py` (the build half: suggest/
  generate-workflow/finalize + `_build_routine`). `app.py`'s `api_wizard.router` include is
  unchanged (the router is re-exported); `scaffold`/`suggest_tags`/`FinalizeBody`/
  `_build_routine` stay importable off `api_wizard` for the tests. Pure structure — no route,
  payload, or behaviour change; full suite green 840/3. This is slice 0 of the wizard→run-page
  unification (audit D11): the session/clarify half is now cleanly separated from the build
  half, the seam the frontend unification lands along.

## [0.56.0] — 2026-07-16

### Changed
- **`tuning.yaml` — the deliberation carve-out redesigned away** (user order, same-day
  design review of 0.55.0): `deliberation` was behavior mis-filed in the authority file.
  It now lives in `tuning.yaml`, a new per-routine document for machine-tunable BEHAVIOR
  parameters, classed with the RECIPE — writable under the existing `recipe_unlocked` rule
  (the improver's fs_write_root), so the FILE boundary is the permission boundary again.
  Deleted: `GrantPolicy.config_tunable` and the executor's yaml semantic-diff gate; the
  "routine.yaml is NEVER writable by any run" invariant is absolute once more (denials now
  point knob changes at tuning.yaml). `config.load_tuning`/`write_tuning` are the one
  reader/writer pair; scaffold and conversation creation always write the file; the
  clarify-template copy reads it; the registry memo fingerprints both files so a
  tuning-only edit is never served stale. Production data migrated in the same session
  (routine.yaml `deliberation` keys moved into tuning.yaml; a leftover config key is
  reported as a problem and ignored — never read).

## [0.55.0] — 2026-07-16

### Added
- **The deliberation slider** (user order): a per-routine/per-conversation knob over how
  much of the model's thinking lands ON PAPER — the persistent prose channel that, unlike
  ephemeral thinking tokens, survives between turns. Four named stops
  (`terse | standard | deliberate | think-on-paper`), each a qualitatively distinct say
  contract (`engine/deliberation.py` owns the wording; the top two license knowledge
  BEYOND the run — domain conventions, base rates, prior art — and the top stop adds a
  notes-file discipline before direction-shaping actions). Conversations default to
  `deliberate`, routines to `standard`; children inherit the parent's live level.
  Surfaces: routine page (Models panel), new-routine wizard (suggested per task by
  `suggest_traits_permissions`, editable), conversation header panel (saves config +
  re-levels a live reply), and the run view (mid-run, control.json `set_deliberation` —
  applied at the turn boundary as an engine note carrying the new contract, exactly like
  a model switch). Status/SSE/API carry the live level.
- **The improver can optimize it.** `deliberation` is now the ONE routine.yaml key a run
  may edit — only under a user-granted fs_write_root (the routine-improver's grant), and
  the executor parses the proposed yaml and rejects any change beyond that single key
  (`grants.py config_tunable` + `executor._deliberation_only_change`). The improver's
  seed teaches the rubric: raise a stop when judgment-heavy transcripts show restatement
  says, lower when mechanical work carries contextualizing ceremony; one stop at a time,
  evidence logged. Every other config key stays sealed exactly as before.

## [0.54.1] — 2026-07-16

### Fixed
- **Flaky `test_dialog_reply_*` decisions tests (recurring F71).** The driver thread's
  wall-clock deadline (30s) could expire before the run's total ask budget elapsed
  (`ask_timeout_min: 1` × two blocking asks = up to 120s) under full-suite CPU load, so the
  re-ask answer was never posted and `answers[1]` raised `IndexError`. Raised both driver
  deadlines to 180s so the driver always outlives the run's whole ask budget. Test-only
  change; no runtime behaviour affected.

## [0.54.0] — 2026-07-16

### Added
- **"Refer to" on every message (the messenger reply analog).** Every transcript message
  (turns, injections, questions, answers, finish banners) and every chat message (yours,
  the agent's replies, single work steps inside a fold) carries a hover ↩ that primes the
  composer with a reference chip; sending prepends ONE leading quoted line
  (`> re <label>: <snippet>`) to the message text — plain markdown the model reads
  naturally, no new event field. The sent message renders the line as a compact quote chip,
  ✕ drops a primed reference, and a slash command never takes one (its `/<kind>` head must
  lead). Run view (all three modes) and conversations alike.
- **Transcript story rendering.** The run transcript groups the say stream by acting stage:
  a phase change draws a labeled divider (from the `phase` stamp assistant_action events
  already carry), so a run reads as chapters of its own stages. Applies wherever the shared
  renderer runs — run view, subrun unfolds, and chat work folds.

### Fixed
- **Conversation messages no longer carry `\r`.** Multipart form encoding turns every
  newline into CRLF; the conversations API now canonicalizes to `\n` on receipt (create +
  message), so multi-line chat messages stop leaking carriage returns into instruction.md,
  the inbox, and the model's context. Surfaced by the refer-to tests' exact-match asserts.

### Changed
- **Finding-first `say` contract.** The harness contract and the action schema now demand
  the say LEAD with what the last observation taught, then why this action — a few words
  for routine steps, 2-3 sentences on decisions, direction changes, and surprises (was:
  "one short sentence, what/why"). Mid-run narration becomes an actual story instead of a
  restatement of the action beside it; prompt-anatomy doc + pin test track the wording.

## [0.53.0] — 2026-07-16

### Added
- **Clarification template routine (audit decision D10).** The "+ New routine" wizard's
  clarify sessions now copy their budgets, models, and practice modules (`traits/`) from a
  visible, protected `clarification` routine instead of hardcoded values. Seeded via
  `routine-seed/clarification` and adopted once at boot on existing deployments; the API
  refuses run/archive for it (403), every card/detail payload carries `protected`, and the
  routine page swaps the run/archive buttons for a "protected template" chip. Editing that
  routine's budgets/models/traits tunes every future clarification session.

## [0.52.0] — 2026-07-16

Self-audit (wizard hardening after the 2026-07-16 routine-creation incidents).

### Fixed
- **A self-restart no longer kills an in-flight routine clarification.** Clarify runs live in
  dot-hidden `.wizard-*` dirs the registry skips, so the restart drain never saw them: a drain
  fired mid-clarification and orphaned the user's setup conversation at turn 0. New
  `restart.clarify_states()` folds live clarify runs into the drain gate — `waiting_user`
  defers the restart, `running`/fresh `starting` drain it; dead pids and stale orphans never
  block. `/api/wizard/start` also returns 503 while draining (mirrors finalize's gate).
- **The clarify run can no longer be silently decomposed into the drafted routine itself.**
  Observed: applied to a draft that described a research routine, the decompose step built THAT
  routine — it ran the task, posted its output to Decisions, never wrote
  `state/wizard_result.json`, and creation dead-ended with "The clarification run ended without
  a result." Patterns may now PIN deliverable paths (`META["pin"]`, clarify-instruction v8 pins
  `state/wizard_result.json`); the decompose prompt demands them and a result that drops one
  falls back to the verbatim pattern.
- **Clarify questions no longer show twice on the Decisions page** — a live blocking question
  also has a durable pending record; `_wizard_questions` now dedups by qid like `_all_questions`
  always did.

### Added
- The clarify error screen offers **"retry with the same draft"** (the error-stage wizard
  snapshot carries `draft_full`) instead of only a draft-losing "start over".
- The setup banner names the session it refers to (draft preview), so a leftover abandoned
  session no longer reads as if the routine just created were still "in progress".

## [0.51.0] — 2026-07-16

### Added
- **Nano-GPT endpoint cards show the account balance** like OpenRouter ones (user order):
  the credits route now sniffs the provider from `base_url` — OpenRouter keeps
  `GET {base}/credits`, Nano-GPT uses `POST /api/check-balance` on the origin with
  `x-api-key` auth (string `usd_balance`, verified live) — and returns a per-provider
  `manage_url` the card links instead of a hardcoded OpenRouter URL.

### Fixed
- **The conversations rails persist at every desktop width** (user order: the conversation
  list stays LEFT, state/artifacts stay RIGHT): at 1200–1559px the view now escapes the
  1180px column and becomes a three-column grid with sticky rails beside the chat —
  previously both rails collapsed into stacked blocks above the chat below 1560px. DOM
  order is now list · chat · artifacts, so on narrow/stacked screens the artifacts drop
  below the chat instead of pushing it down. `tests/test_static_layout.py` pins the
  regime; new `tests/test_endpoint_credits.py` pins the credits provider sniff.

## [0.50.2] — 2026-07-16

### Fixed
- **`server_tz()` consults `/etc/timezone` before the `/etc/localtime` symlink**: Docker
  bind-mounts through the image's symlink (stale NAME over correct zone DATA), so in a
  container the symlink route answered `Etc/UTC` even with the host's zone mounted.

## [0.50.1] — 2026-07-16

### Fixed
- **Conversations and detached background runs now survive container recreation**: the
  compose file was missing bind mounts for `~/conversations` and `~/background`, so both
  homes lived in the container's writable layer — any `docker compose up -d` after a
  compose/image change would have silently destroyed them (plain restarts reuse the
  container, which is why nothing was lost). Both are now bound like `~/routines`.
- **`server_tz()` works inside a container**: it now honors a `TZ` env var and falls back
  to `/etc/timezone` (bind-mounted from the host along with `/etc/localtime`, read-only) —
  previously only the `/etc/localtime` symlink trick worked, which a bind mount defeats,
  so a containerized daemon always reported `Etc/UTC` and stamped UTC into every schedule
  the UI wrote.

## [0.50.0] — 2026-07-16

### Added
- **write_file overwrites must be grounded** (the Claude-Code-style read-before-write rule,
  scoped to where it matters): overwriting an existing file OUTSIDE the routine's own dir —
  a project file under an `fs_write_root` — is rejected unless the run has read, viewed, or
  written that file this run (`ctx.seen_paths`, rebuilt from the transcript on resume so a
  leg-one read grounds a leg-two rewrite). The routine's own dir is exempt (state/report
  rewrites are its normal mode), `append` adds without destroying, new files need no
  grounding, and `edit_file` stays ungated — its verbatim anchor is self-grounding. The
  rejection is a teaching observation naming the fix; the composed prompt's file-actions
  line states the rule up front.

## [0.49.1] — 2026-07-16

### Changed
- **`steps/` → `stages/` everywhere — one module-dir convention.** All seven production
  routines were migrated in place (`git mv steps stages` + a reference rewrite across
  main.md / stage modules / traits / state files, committed per routine repo; `runs/`
  and LEDGER history untouched), and the engine's transitional `steps/` acceptance from
  0.49.0 was removed (`statemap.STAGES_DIR`). Per the migration policy, the data
  migration ran once on the production instance and no migration code is kept.

## [0.49.0] — 2026-07-16

### Changed
- **The stage modules ARE the state graph — nothing inferred from prose.** `statemap.py` no
  longer parses main.md's `## Run flow` for bold state names; the diagram's nodes are the
  routine's own `stages/*.md` modules (older recipes' `steps/` accepted too), ordered by
  where main.md first mentions each one, with the module's leading heading as the tooltip.
  "no parseable run flow" can no longer happen — every routine has stage modules with
  task-specific names (this fixes the config-optimizer's empty rail).
- **The live phase is derived from stage-module reads, not phase.json.** Reading
  `stages/<name>.md` IS the state transition: the executor stamps it into `ctx.phase` →
  status.json → the SSE `state` event; a resumed run rehydrates the phase from its replayed
  transcript. `state/phase.json` stays recipe-private state (the digest still shows it) but
  no longer drives the diagram, and decompose no longer asks recipes to bookkeep it per
  stage. The routine `/stategraph` endpoint's `current` now comes from the latest run's
  status.json.

## [0.48.1] — 2026-07-16

### Fixed
- **Full-repo `ruff check` is green again**: the seed trees are now excluded from lint
  (`extend-exclude = ["library-seed", "util-seed"]` with the reasons documented in
  `pyproject.toml`). Workflow pattern files are never-executed control-flow depictions
  parsed with `ast` (pseudo-imports are the format; `workflows/lint.py` is their gate), and
  seed utils are PEP 723 single-file scripts with script conventions (print CLI,
  assert-based `--selftest`; header checks + the selftest run are their gate). Previously
  ~226 findings in those trees never surfaced because the pre-commit hook only lints
  changed files — the "ruff green in every commit" invariant now holds for the whole repo,
  and pre-commit's `--force-exclude` keeps the exclusion effective for explicitly-passed
  paths too.

## [0.48.0] — 2026-07-16

### Added
- **File-activity rail card** (user order): the run view and the conversation view now show
  which files a run read / wrote / edited — per-path counts derived server-side from the
  transcript's observation events (`GET /api/runs/{id}/files`, `rsched/fileactivity.py`),
  so subruns and user slash commands count too. Rows are first-touched order, long paths
  truncate on the left, failed touches are flagged; the card live-refreshes off the SSE
  tail (bursts coalesced into one refetch).

### Changed
- **State graph marks skipped phases**: a state the run's `phase.json` jumped over (no turn
  ever recorded under it) now renders `» skipped` instead of a ✓ — previously the checkmark
  was purely positional, claiming work that never happened. Detection requires the run to
  stamp phases at all, so a conversation's synthetic reply-cycle diagram is unaffected.

## [0.47.0] — 2026-07-16

### Changed
- **Conversations view adopts the run page's layout** (user order): the chat owns the full
  1180px main column; the conversation list parks in a LEFT margin rail and
  state/tasks/artifacts in the RIGHT margin rail on wide screens (`.run-rail` /
  `.run-rail.left`), ordinary collapsible blocks above the chat otherwise. The old
  three-pane grid (drag handles, fold rails, persisted pane widths) is removed —
  `views/conversations.js` −78 lines, plus the matching CSS. New
  `tests/test_static_layout.py` pins the rail adoption and checks every mounted
  `conv-*`/`pane-*` class is styled.

## [0.46.1] — 2026-07-16

### Fixed
- **Conversations view: `mdInline` was used but never imported.** `static/views/conversations.js`
  called `mdInline(q.question)` when rendering a deferred question (`showQuestion`) without
  importing it from `/static/md.js`, so the deferred-question box crashed the render with
  `ReferenceError: mdInline is not defined` (observed twice in `.ui-traces` on 2026-07-15).
  Added the missing import. A new static-analysis test (`tests/test_static_imports.py`) now
  asserts every `static/**/*.js` that calls `md()`/`mdInline()` imports it from `/static/md.js`,
  so the console's no-build ES modules can't ship this ReferenceError class again.

## [0.46.0] — 2026-07-16

### Changed
- **A slash command keeps the speaking turn with the user — it never hands the turn to the
  model.** When the model has given the turn back (an authored finish) and the resuming
  message only runs commands, the engine executes them and returns to idle with **no model
  turn and no reply** (the loop's command-only gate: `loop.leg_after_authored` + all
  commands, no prose → `_exit_commands_only`, no finish event, `result.md` untouched). You
  can run any number of commands in a row and the assistant stays quiet; it replies only
  when you send a plain message — and then it sees every command's result (replayed from the
  transcript). The rule is uniform across conversations and routines: it fires wherever the
  turn is yours (a conversation reply, or a resumed finished run), and does NOT fire for a
  routine's own scheduled execution (its workflow always runs; an injected command there is
  context). A command still grounds the run, so a following model finish is not treated as
  fabricated. The command composer's send toast now reads "command running — you keep the
  turn".

## [0.45.1] — 2026-07-16

### Fixed
- **Command autocomplete was unreadable**: the dropdown referenced a CSS token that
  doesn't exist (`--panel`), rendering transparent over the chat. It now uses the theme's
  raised surface (help panel likewise), the harness pins an opaque computed background so
  an undefined token can't slip through again, and a sweep confirmed every `var(--…)` in
  both stylesheets resolves.

## [0.45.0] — 2026-07-16

### Added
- **Chat slash commands — the user can run the same actions and utils as the assistant.**
  Type `/` in the conversation composer for autocomplete (kinds first, util names after
  `/util `); the **/ commands** button beside the input opens the full reference — the
  effect actions the conversation's capabilities allow plus every global util with its
  usage line (`GET /api/conversations/{slug}/commands`). A sent command executes through
  the engine's normal action path (`engine/commands.py` parse → the model action's exact
  schema + `validate_action` gates → `executor.dispatch`) at the next turn boundary —
  costing **no model turn**. The result renders in the chat as a command block, and the
  assistant sees exactly what the user ran and what came back; malformed or disallowed
  commands answer with their usage line. Grammar:
  `/util <name> [arg …]`, `/read_file <path> [path …]`, `/write_file <path> <content…>`,
  `/edit_file <path> anchor="…" replacement="…"`, `/view_image <path> [prompt…]`,
  `/llm <prompt…>`, `/memory_read <name>`, `/memory_write <name> about="…" <content…>`.
  Loop-control actions (`spawn`, `subtask`, `wait`, `ask_user`, `finish`, …) are
  deliberately not commands — they steer the assistant's run.

## [0.44.0] — 2026-07-16

### Added
- **Library items are deletable, not just editable**: traits and global utils gain a
  delete button in their editors (themed confirm, committed to the library repo) beside
  the existing workflow and playbook deletes. Two protections, enforced server-side and
  reflected in the UI: **permission docs cannot be deleted** (they are the capability
  layer's conduct surface — edit them instead) and the **`clarify-instruction` workflow
  cannot be deleted** (the new-routine wizard runs it to create every routine; its editor
  simply has no delete button). A deleted seed workflow/trait returns at the next daemon
  boot; a deleted util stays deleted but is git-recoverable. After a delete the page
  reloads onto the bare Library list instead of the dead item's deep link.

## [0.43.0] — 2026-07-15

### Added
- **The state-graph rail is an instrument panel**: every `assistant_action` transcript
  event now carries the phase that was active while it was produced, and
  `statemap.phase_stats` (served at `GET /api/runs/{id}/phases`) derives per-phase
  turns · tokens · wall-clock · cost from the transcript — dispatch time attributed to
  the acting phase, completion time to the phase that produced the next action, the
  tail after the last action to the last phase. The run-view and conversation rails
  render the numbers on each visited node, refreshed on every phase transition; turns
  from before any `phase.json` write show as a "before any phase" foot line.

## [0.42.0] — 2026-07-15

### Security
- **The bearer token no longer rides SSE query strings** (where it leaked into access
  logs). EventSource connections mint a short-lived, unguessable ticket first
  (`POST /api/sse-ticket`, 60 s TTL, multi-use within it so browser reconnects keep
  working; expired tickets purged on mint) and send that instead; `?token=` is no longer
  accepted anywhere. Reconnects mint fresh tickets automatically via the `sse()` wrapper.

## [0.41.0] — 2026-07-15

### Changed
- **Decisions page is a grouped inbox**: the priority view renders sections — *Blocking
  (a run is waiting on you)* → *Deferred* → *Meta* → *Settled (answered, queued)* — with
  section headers + counts; a blocking ask within 30 minutes of its timeout carries a
  loud red "expiring" chip and sorts to the very top of its group. Keyboard navigation
  (↵ / ↑↓ / 1-9), every filter chip, the routine filter and the non-priority sorts (which
  render flat, as before) all survive unchanged.

## [0.40.0] — 2026-07-15

### Changed
- **Run view: one message input with an explicit mode selector** replacing the shifting
  two-button arrangement. Where a message goes is stated, not implied: a live run fixes
  the mode to "→ live run" (inject, picked up at the next turn boundary); a terminal run
  offers "→ continue this run" (rehydrate and converse, the default) or "→ queue for next
  run". Enter always sends in the visible mode.

## [0.39.0] — 2026-07-15

### Changed
- **Routine page saves in place — no full-page reload anywhere.** Schedule saves refresh
  the header chip + next-fire line from a fresh read; permissions saves re-render the
  panel from the server's post-cascade state; models saves just toast (the selects already
  hold the truth). Scroll position and unsaved edits elsewhere on the page survive a save.
- **One shared tag editor** (`components/tags.js`) for routines AND conversations: chips
  with ✕ remove plus an inline add field, every change saved immediately — the routine
  page's separate "save tags" button and the conversation's prompt-dialog "+" are gone.

## [0.38.0] — 2026-07-15

### Changed
- **One shared answer form** (`components/answerform.js`) replaces the six hand-rolled
  copies (Decisions page, run view, conversation panel, wizard, transcript inline, chat
  inline). The component owns the core — input/textarea, option buttons (numbered + digit
  keys where wanted), default line, ask-back, Enter-to-submit, draft persistence, error
  toast — while each host keeps its chrome (meta chips, expires/mirrored notes,
  snooze/defer lifecycle, settled states) via `{ node, input, submit, setSettled }`.
  Accidental drift fixed in passing: the chat inline form no longer swallows errors
  silently, option buttons focus the input everywhere, and the conversation question
  panel renders markdown like every other surface.

## [0.37.0] — 2026-07-15

### Changed
- **Every native `confirm()`/`prompt()` replaced with themed dialogs**
  (`components/dialog.js` — the token gate's overlay language, keyboard-first: Enter
  confirms, Esc/overlay-click cancels, promise-based call sites). Covers routine archive,
  run abort, conversation delete + add-tag, workflow/playbook delete, endpoint/model/secret
  delete. Destructive confirms carry an action-named red button ("delete", "abort",
  "archive") instead of a generic OK.

## [0.36.0] — 2026-07-15

### Added
- **Uncensored-referral audit**: every referral — a turn the main model refused that the
  `uncensored` model answered (turn loop), or an `llm` call the tool model refused
  (executor) — increments `ctx.referrals`; children fold theirs into the parent. The
  count rides each run's `status.json`, the durable workflow-usage stream (so it survives
  retention and aggregates per month), and surfaces on the routine page's Models section
  ("↪ uncensored referrals: N total · M this month").

## [0.35.0] — 2026-07-15

### Added
- **Monthly spend aggregation** — answers "what does this routine cost me and is it
  growing": the workflow-usage stream now records each finished (sub)run's `cost` and
  serves as the DURABLE spend series (run dirs fall to retention; the stream survives).
  `stats.monthly_spend` rolls it up per routine × calendar month (depth-0 entries only —
  a parent's usage already folds its children in; detached-task slugs attributed to their
  owner conversation). Surfaced as a **"Monthly spend by routine" table on the Stats tab**
  (last 6 months, tokens · cost per cell, growing/steady/shrinking trend chips) and a
  **compact month line on every dashboard card** ("Jul: 2.00M tok · $2.00 (Jun: …)", with
  an ↑ growing chip past +20%). Historical entries predate the cost field, so cost sums
  start now; token trends are complete.

## [0.34.0] — 2026-07-15

### Added
- **Decision lifecycle on the Decisions page** — fields on the ONE record shape, not a
  new record type:
  - **Defer to next run** (blocking questions): a `{defer: true}` inbox marker releases
    the engine's blocking wait immediately — the run continues on the action's stated
    default, exactly the timeout path but chosen by the user; the record stays open as
    deferred, Discord (when mirrored) is told, and a marker that outlives its run is
    swept silently at the next boot.
  - **Snooze** (deferred questions): `snoozed_until` on the record hides it from the
    inbox, the nav badge, and every non-Snoozed filter until the timestamp (1h/4h/1d/1w
    or unsnooze); runs still see the open question in their state digest — snooze is UI
    noise control, never an answer.
  - **Decision-backlog flag**: a routine with more than 5 unanswered deferred asks gets a
    loud `decision backlog` chip on its dashboard card — the "silently starving on my
    input" signal.

## [0.33.0] — 2026-07-15

### Added
- **Policy gates as tests** (`tests/test_policy.py`, wired into pre-commit): (1) the
  delete-after-convergence rule is machine-checked — one-shot migration code must carry a
  `MIGRATION(expires=YYYY-MM-DD)` marker and the suite fails once the date passes (or on
  migration-shaped code without a marker); (2) a `__version__` bump without a matching
  `## [x.y.z]` CHANGELOG header at the top fails the suite (0.27 shipped without notes once).
- **Seed contracts pinned** (`tests/test_seeds.py`): every `routine-seed/` loads clean via
  `load_routine` (permissions exist, capabilities normalize, Standing-practices tail +
  bundled traits present, all `stages/*.md` references resolve), every seed markdown's
  `state/phase.json` assignment uses the canonical `{"phase": ...}` shape and names only
  live action kinds, `library-seed/` workflows parse via pyworkflow with slug/tools checks
  and the whole tree lints clean, and `util-seed/` docstring headers pass the engine's own
  `write_util` gate. Seed drift is now a test failure in the commit that causes it.

## [0.32.0] — 2026-07-15

### Changed
- **`engine/loop.py` and `engine/composer.py` split under the ≤~350-line standard**,
  behavior-preserving (every prompt string byte-identical; `test_prompt_anatomy` pins them).
  New modules, each one responsibility: `engine/completion.py` (get ONE valid action —
  schema retries, repeat-streak shedding, refusal referral, media fallback, the compaction
  gate), `engine/boot.py` (kickoff / resume rehydration of the message list),
  `engine/observations.py` (observation → next user message + truncation),
  `engine/capabilities.py` (the CAPABILITIES prompt section). `loop.py` keeps only the
  turn cycle; `composer.py` the system-prompt assembly and state digest.

## [0.31.0] — 2026-07-15

### Added
- **Browser UI test harness** (`tests/ui/`): Playwright drives the REAL console — the
  FastAPI app + static frontend served by uvicorn on an ephemeral port over fixture homes
  and a stub runner (no scheduler, no engine subprocess, no LLM). Covers the four
  load-bearing flows: Decisions answering (options, default, Enter-to-submit, blocking
  from a live run), the conversation composer (create + follow-up message), routine-page
  saves (description, budgets), and Settings endpoints/models CRUD (create, edit, delete
  behind confirm dialogs). Every test also fails on any uncaught JS error, and asserts
  what landed **on disk**, not just what the toast claimed. One-time setup:
  `uv run playwright install chromium`.

## [0.30.0] — 2026-07-15

### Added
- **Child-task process-model decision record** (docs/subtasks.md § Process model): evaluated
  migrating `spawn`/`subtask` threads onto the detached-subprocess pattern (to delete the
  resume-orphan handling) and rejected it with reasons — start latency, live budget folding,
  the responsive wait being a feature not a workaround, and the replacement lifecycle
  dwarfing the ~60 lines it would remove. Threads stay; `detach` remains the cross-process
  escape hatch.

### Changed
- **Registry scans are memoized behind stat() fingerprints** (`daemon/registry.py`): each
  parsed `status.json`/`result.md`/`routine.yaml`/question set is reused only while its
  (inode, mtime, size) fingerprint matches — freshness is re-decided from the filesystem on
  every lookup, callers get copies, entries for deleted dirs are pruned. Warm scan on the
  production instance: 77 ms → 9 ms, with no database and no invalidation protocol.

## [0.29.0] — 2026-07-15

The whole-codebase overhaul: every subsystem audited (engine, endpoints, daemon, web,
UI, workflows/seeds, tests, docs), bugs fixed, dead code and every legacy shim removed,
duplication unified, strict quality tooling introduced. No backwards compatibility is
kept — converged one-shot migrations and tolerant readers for retired formats are gone.

### Added
- **One outbound notification seam (`rsched/notify.py`).** Every engine/daemon-implicit
  "reach the user" send — the blocking-decision Discord mirror and the background-task
  delivery ping — goes through one module; channels are user-selected (web always,
  Discord via the `communication` permission), and the durable record is always the
  Decisions page / the conversation. New guide: `docs/notifications.md`.
- **Strict tooling, enforced.** `ruff` with `select = ALL` (every ignore carries its
  house-style justification inline in pyproject.toml), `mypy` over `src/rsched`,
  branch-coverage config, and a `.pre-commit-config.yaml` wiring both gates into git.
- **`docs/authoring.md`** — the missing guide to writing utils (PEP 723 + docstring
  standard + selftest), workflow patterns (`META`/`PHASES`/`main()`), traits,
  permissions, and playbooks, each with a real example.

### Fixed
- **Token budgets now mean the same thing on every provider**: the OpenAI-compatible
  adapter counted cached prefix tokens inside `in`, so `total_tokens` budgets burned
  cached traffic at full weight on OpenRouter/Ollama but not on Anthropic; cached tokens
  are now kept OUT of `in` across all three adapters (the documented invariant).
- **A dialog ("ask back") reply no longer destroys the decision record.** Intermediate
  replies used to resolve the pending question and tell Discord "resolved" before the
  dialog was over — a finish without a re-ask silently dropped the decision. The record
  now stays open (deferred) through the dialog; the model's re-ask supersedes it, a real
  answer resolves it, and a finish leaves it live for the next run.
- **`routine.yaml` is written atomically everywhere** (conversation autolabel, patch,
  wizard finalize) — three raw `write_text` sites violated the cross-process
  atomic-write invariant and could tear a concurrent engine boot read.
- Conversation "reply ready" desktop notifications now honor the Settings opt-in;
  Stats empty-states render their glyph correctly; same-placeholder form fields no
  longer share one draft-persistence key.
- Meta-routine seeds: three seeds shipped the removed `ask_timeout_h` key; the improver
  read a nonexistent `instruction.md`; self-audit's main.md contradicted its own
  write-report stage on deferred asks; phase-file keys standardized on `{"phase": …}`;
  false workflow provenance (`self-audit-code`, `meta-workflows`) removed.

### Changed
- **Settings leads with Endpoints → Models → System model** (the first-run critical
  path) and loads its sections in parallel; dashboard bus reloads are debounced.
- Shared UI primitives extracted (`states.js`, `follow.js`, unified formatters in
  `util.js`); duplicated backend logic unified (artifacts listing/serving, permission
  detail blocks, active-run guards, terminal-state constants, terminal-resume, the
  engine's usage folding, injection message shape, phase parsing, api-key resolution and
  HTTP plumbing across the three endpoint adapters).
- The stale committed `audit/` artifact (a self-audit run pointed at the source tree)
  is removed and gitignored; CHANGELOG gains the missing 0.27 entry and a proper 0.18
  header; README/CLAUDE.md/DOCKER.md drift fixed (`improve: false`, `workflow-curator`,
  `main()` patterns, model-catalog era Docker notes).

### Removed
- **All converged one-shot migrations and legacy shims** (the delete-after-convergence
  policy, applied): `rsched migrate-model-catalog`, `rsched migrate-stages`, the
  `ask_timeout_h` config shim, the legacy `confirm` vocabulary (`true` /
  `revisions-only` / `false`), the `fragment:` library-doc reader and `fragments` config
  scrub, `parse_run_ts`'s dead tz parameter, the `timeout_h` observation fallback, the
  `status: stable` frontmatter in fallback child recipes, and the empty boot-time
  permission-adoption walk.
- Dead code throughout: the unused `/routines/{slug}/files` endpoint, unread response
  fields (`endpoints` lists, `finish_status`), `GrantPolicy.workflows_sources`,
  `BudgetLedger.get`, `read_trait`, the vestigial `strip_inactive_improve` pass, unused
  UI components/CSS/exports, and tautological or dead test code.

## [0.28.0] — 2026-07-15

### Changed
- **Step modules are now "stage modules" (`stages/`).** A routine's decomposed workflow modules were
  called *step modules* and lived in `steps/`; they are now **stage modules** in `stages/`, listed by
  the `stages:` key in `main.md`'s frontmatter (was `modules:`), and the wizard/decompose schema emits
  `stages` (was `steps`). How a run reads them is unchanged — `main.md` is still the entry state machine
  that routes to on-demand modules.
- **The live workflow diagram is labelled with the routine's own stage names.** `decompose` now emits
  task-specific bold `## Run flow` state names that match the stage filenames, so the state-graph card
  in the run and conversation rails shows the routine's actual stages instead of the generic library
  pattern's states.
- **The routine-improver edits a target's RECIPE directly and proposes config changes via a deferred
  ask.** It rewrites `main.md` / `stages/` / `traits/` in place (the recipe is the source of truth); for
  any `routine.yaml` CONFIG change — budgets, models, permissions, capabilities, fs-roots — it files a
  **deferred `ask_user`** to the Decisions page rather than writing the file. A run NEVER writes
  `routine.yaml`.

### Removed
- **The seed→recompile machinery is gone — stage modules are the sole source of truth.** There is no
  longer a persisted per-routine *Seed*, no recompile-from-instruction step, no seed↔stages drift
  detection, no provenance hashing (`seed_sha256` / `compiled_sha256`), no routine-page Seed editor, and
  no `RecompileDriftError`. The clarified instruction is only a **transient compile seed** consumed at
  creation; a real routine dir no longer contains `instruction.md` (only the wizard's throwaway clarify
  session still uses one internally). After creation you edit a routine by editing its `stages/` /
  `main.md` / `traits/` directly — the routine page gains a navigable **Recipe** file-tree for exactly
  that — and there is no recompile step to undo those edits.

## [0.27.0] — 2026-07-15

### Changed
- **Per-model attributes moved off endpoints into a named model catalog.** A new
  `models:` catalog in the server config (`ServerConfig.models`, Settings → Models) binds a
  provider model id to an endpoint and owns the PER-MODEL attributes — `multimodal`,
  `context_chars`, `effort`, `temperature` (each `None` inherits the endpoint-kind default
  or the endpoint's own value). Endpoints hold only transport + auth + those defaults;
  `multimodal` is no longer an endpoint property (one endpoint serves many models with
  different windows and vision support). Every routine/conversation references models **by
  catalog name** (`routine.yaml` `models:` maps role → name), as does the server's
  `system_model`; `EndpointRegistry.resolve()` / `.for_model()` / `.for_system()` return a
  fully resolved `ModelRef` (endpoint, model id, effort, multimodal, context_chars,
  temperature). Editing a catalog model updates every routine that names it.
- `supports_media()` and compaction take the resolved model's values; `complete()` gains a
  `temperature` kwarg honored by all three adapters.

### Added
- A one-shot `rsched migrate-model-catalog` converted a pre-0.27 endpoint-attribute config
  (deleted after production convergence, per the migration policy).

## [0.26.0] — 2026-07-15

### Added
- **Detached background tasks — long fire-and-forget in conversations (`detach`).** A conversation can
  now kick off a LONG job (a 20-minute scrape, a bulk conversion), keep chatting about other things, and
  be told when it lands. Unlike a within-reply `subtask`/`spawn` (a thread that dies when the reply's
  process exits), a detached task runs as its OWN daemon-managed `engine-run` process and survives across
  reply-finishes, reporting its result back into the conversation on completion. The new `detach` action
  (fields `prompt` / optional `workflow` + `label`) is deliberately tiny on the engine side — it drops an
  intent file in a new `background_home` (a config peer to `routines_home`/`conversations_home`) and
  returns, so the assistant `finish`es the reply ("started it — I'll report back") and the conversation
  continues normally. See `docs/background-tasks.md`.
- **The `DetachedManager` (`daemon/detached.py`) owns the whole lifecycle, all on disk (restart-safe).**
  Ticked from the scheduler after the cron-fire loop (+ a boot reconcile), it: materializes each task dir
  (`childrun.materialize_to_disk`, `routine.yaml` carrying `owner: {slug, dir}`, permissions/models/fs-
  roots copied from the owner but a background-sized budget of its own) and `runner.fire`s it on a third
  `BACKGROUND_SLOTS` pool; polls `status.json` for completion (the `EventBus` is lossy); on terminal
  DELIVERS (exactly-once via a `delivered.json` marker + a deterministic message filename) — copies the
  task's artifacts into `<owner>/artifacts/from-bg-<taskid>/` and writes a durable inbox message — then
  WAKES the conversation (`runner.resume` if idle, else the live reply drains it) with an optional Discord
  ping when the owner holds `communication`; rebuilds `<owner>/state/background.json` (inlined into the
  reply's state digest so the assistant can answer "how's the scrape going?"); and gc's delivered tasks.
- **Monitor + cancel.** `GET /api/conversations/{slug}/background` lists a conversation's tasks,
  `POST …/background` drops an intent (the human/test analog of the engine action), and
  `POST …/background/{id}/cancel` aborts one (`runner.abort` + a pid fallback for a task that outlived a
  restart). The conversation rail renders a **background** card (label · state · cancel);
  `web/api_runs.py`'s run resolution now searches `background_home`, so a detached run's transcript /
  task-tree resolve on the generic `/api/runs` endpoints for free. Deleting a conversation tears down its
  detached tasks.
- **New `background-tasks` permission** (`requires: {actions: [detach]}`) — default-ON for conversations,
  opt-in for routines; `detach` joined `GATED_KINDS`.

### Changed
- Detached runs are **excluded from the self-update drain gate** (`ActiveRun.background` →
  `Runner.active_states` skips them): the engine child survives the daemon's SIGTERM via
  `start_new_session`, so a long background job never blocks a deploy, and the manager's disk-poll delivers
  it after the restart. Detached tasks also use **deferred asks only** (coerced in `interact.handle_ask`)
  so one can never park in `waiting_user` and hold a restart. `RoutineConfig` gained an `owner` field.
- The `converse` seed workflow's decompose guidance learned a `detach` branch (long/independent →
  detach; short/interactive → inline or `subtask`).

## [0.25.0] — 2026-07-15

### Added
- **Sequential subtasks — recursive task decomposition as a first-class concept.** A run can now
  decompose its work into an ORDERED sequence of subtasks, each run to completion before the next —
  distinct from the existing PARALLEL subruns (`spawn`). The realization: a subtask and a subroutine
  are the SAME thing — a child task materialized from a workflow pattern and run recursively — so the
  new `subtask` action and `spawn` are two schedulers over one child-task executor (`engine/childrun.py`,
  generalized from `subruns.py`). `subtask` is NON-BLOCKING: it starts a sequential child in the
  background (its own thread + context + pattern) and the parent keeps sequential order by `wait`-ing
  for it before the next; the completion is delivered by the turn-boundary hook, and `wait` is
  RESPONSIVE — it yields to a waiting user message so the conversation stays live while children run.
  Fields: `prompt` (self-contained brief), optional `workflow` (a library pattern for the step's
  purpose), `label`, `turns` (its budget). Decomposition is recursive (a child hits its own gate; depth
  ≤ `max_subrun_depth`). See `docs/subtasks.md`.
- **The decompose-decision gate in the seed workflows.** Concrete subtasks are never known statically,
  so the `general-task` (v9) and `converse` (v2) patterns now carry a standardized `decompose_decision()`
  step that decides inline | sequential (subtasks) | parallel (subruns) — reaching existing routines on
  recompile, new ones at creation.
- **In-run workflow generation (gated).** A subtask with `workflow: "generate"` DRAFTS a new library
  pattern for its brief (`workflows/generate.py`, lint-gated, committed) when the routine holds the new
  `workflows: generate` capability — covered by the `workflow-generation` permission, off by default,
  skipped when the token budget is nearly spent. The generation call's system-model spend folds into the
  run's budget.
- **The recursive task-tree visualization.** The run and conversation rails carry a live task-tree card
  (`static/components/tasktree.js`, fed by the `web/tasktree.py` read-model over the on-disk `sub/`
  transcripts): sequential subtasks (→) and parallel subruns (⇉), each a node with a state icon, its
  workflow pattern, and a per-node turn-budget meter (amber ≥85%, red over), children nested. `run-once`
  prints the same tree.

### Changed
- **Budgets are now one unified primitive** (`engine/budget.py`): a `Budget` is a stop condition over a
  resource, a `BudgetLedger` is an ordered set of them, and `allocate()` slices a child's ledger from
  the parent's remainder. The run, a conversation reply window, a subtask, and a subrun all share it —
  `RunContext` holds the live meter, the ledger holds the limits (single-writer `status.json` preserved;
  wording and status shape unchanged). Per-subtask budgets are SOFT at the parent: a child that overruns
  its own turn cap force-finishes `partial` and the parent re-plans; only run-level budgets hard-stop.
- `subrun_start`/`subrun_end` transcript events gained a `mode` (sequential/parallel) and the child's
  allotted budget — payload EXTENSIONS, so every existing consumer keeps working. Children are threads
  that die with the process, so a resume marks any still-running child aborted and notes it
  (`history.orphaned_children`) rather than letting the parent `wait` forever. `wait` also became
  responsive to pending user messages (`inbox.has_pending_messages`).

## [0.23.0] — 2026-07-15

### Fixed
- **Recompile no longer silently reverts routine hand-edits (the "rematerialization" bug).**
  `recompile_routine` re-derives a routine's `steps/` from its instruction × workflow; it used to
  do so unconditionally, discarding any hand-edits (the routine-improver's or a person's) that the
  routine page's drift banner already reported but the action ignored. This is what kept reverting
  newsletter-digest's fixes back to the library pattern's design. Recompile now consults
  `provenance.drift()` first: when the steps have drifted from the compile baseline and the edits
  are not in the seed, it **refuses** (`RecompileDriftError`; surfaced as `state=error`,
  `reason=steps_drift`) so nothing is lost silently. Pass `?force=true` to overwrite — and even
  then the pre-recompile `main.md` + `steps/` are backed up to `state/recompile-backups/<ts>/`
  first. The refusal keys off `provenance.drift()`, which reports no steps-drift for a routine that
  has no compile baseline, so only a routine whose steps drifted from its baseline trips the guard.

## [0.22.0] — 2026-07-15

### Changed
- **The graceful self-restart now DRAINS in-flight new-routine wizard builds** instead of only
  cleaning up their fallout (complements 0.20.1's boot-time `recover_orphan_builds`). A wizard
  build (`api_wizard._build_routine`) is an unpersisted web-process background task; restarting
  mid-build stranded a half-scaffolded routine. Now the scheduler tracks in-flight builds
  (`Scheduler.wizard_builds`, registered by `finalize`, cleared when the build ends) and the
  restart state machine treats a build as finishable work: `restart_action` gained a
  `builds_active` count, so a pending restart stays in **drain** (fires nothing new) until both
  active runs **and** builds have finished before it exits. While draining, `finalize` refuses a
  new build with **503** so the drain converges. A build is never "parked", so it can only hold
  the restart in drain, never defer it. (AUDIT follow-up: "drain builds as well instead of just
  dealing with the fallout.")

## [0.21.0] — 2026-07-15

### Added
- **Refusal referral now covers the main orchestrator loop and subroutine loops** (extends the
  0.20.0 `llm`-tool-call referral; AUDIT decision **D8 → C**). In an agent loop a turn is a
  schema-constrained *action*, so a model refusal surfaces as a free-text reply that fails to
  parse as an action **and** reads as a decline (`executor._looks_like_refusal`). When that
  happens and the routine has an `uncensored` model configured, `EngineLoop._next_action`
  re-issues the SAME turn to it once; a schema-valid action from the uncensored model continues
  the run untouched and the `assistant_action` transcript event is tagged `referred: true`.
  Subroutines run the same loop, so both are covered by one code path. Strictly **opt-in and
  inert**: no `uncensored` role → no referral, unchanged behaviour. A malformed-but-not-refusing
  reply still takes the normal schema-retry path (the uncensored model is consulted only on a
  genuine decline, at most once per turn); referral usage is folded into the turn's usage. No
  new action kind or transcript `EVENT_TYPE` — `referred` is an additive field on the existing
  `assistant_action` event, mirroring 0.20.0's observation field. `docs/endpoints.md` scope note
  updated.

## [0.20.1] — 2026-07-15

### Fixed
- **Wizard builds orphaned by a server restart/crash no longer hang forever.** A new-routine
  build (`api_wizard._build_routine`) runs as a web-process background task with no
  persistence; if the process dies between `finalize.json` = `building` and the terminal
  `done`/`error` write — e.g. a self-restart, which drains engine **runs** but not in-flight
  **builds**, or a crash/SIGKILL — the setup was stranded: `finalize.json` stuck at
  `building`, a half-scaffolded routine dir with no `routine.yaml`, and nothing to complete
  it (`Runner.recover_orphans` reconciles engine runs only). The user saw a setup that "never
  finishes" with no LLM call in flight. Boot now runs `wizard_store.recover_orphan_builds`:
  any `building` state in a fresh process is by definition orphaned, so it is marked a
  recoverable `error` (retry/cancel from the wizard) and its half-built dir (no `routine.yaml`)
  is removed — mirroring `_build_routine`'s own exception handler. (AUDIT note.)

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

## [0.18.0] — 2026-07-15

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

