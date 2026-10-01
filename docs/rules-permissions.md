# Rules, permissions & capabilities

A routine's cross-cutting behavior is split into sets with deliberately different
ownership:

- **Rules** — *general rules*: principle prose (when to ask the user, research discipline,
  what to record) that a run applies to its own particular case. A rule has exactly ONE copy,
  in the shared library; a routine holds SLUGS (`rules:` in routine.yaml), named in every run's
  state digest with the moment each applies and read on demand with `read_rule`.
  Revising the library text therefore reaches every routine holding that rule at its next
  run, with no migration and no per-routine fork to drift. The two halves are owned apart:
  the SET is yours (the routine page's *General rules* panel), the TEXT is the library's —
  yours on the Library tab, and writable by a routine holding the **rule-authoring**
  permission.
- **Capabilities** — the atomic, engine-enforced surface: gated action kinds
  (`grants.GATED_KINDS` — `write_util`, `revise_util`, `remove_util`, `write_rule`,
  `write_recipe`, `schedule_run`, `shell`, plus `detach`, which is structural: a root
  conversation gets it at setup and no config names it, so a request for it is refused before
  it reaches the Decisions page; `revise_util` and `write_recipe` are
  capability TOKENS rather than emittable kinds), reserved utils or single VERBS of them (every
  entry a permission doc's `requires.utils:` names — `signal:send`, `gmail:send`,
  `fau-mail:send`, `usenet:post`, `ntfy`, `browser-session`, `darknet`), and the settings: the
  approval dials, the previous-run read depth, the reminder layer. What every routine does —
  the `util` action, its memory, its own `scripts/` — is no capability at all. Held via
  `routine.yaml`'s `capabilities:` mapping, changed **only by you** (the routine page), and
  enforced when every single action is interpreted. A routine can never grant itself
  anything.
- **Permissions** — *conduct docs*: library prose stating HOW to use a capability well.
  Held via `routine.yaml`'s `permissions:` list; a held doc's short body reaches the
  prompt's CAPABILITIES section. A permission's frontmatter `requires:` names the
  capabilities its instructions presume — it grants nothing itself.

One sentence each: **rules shape how a routine works; capabilities bound what it may
do; permissions instruct it in what it may do.**

## The two permission layers and their cascade

Activating a permission switches on the capabilities its `requires:` names — actions and
utils, the only two things a doc may require. In the UI the doc is the switch: the routine
page's ability cards hang each doc's requirements under it with no switch of their own, and
the one place a capability comes off (the card of capabilities "switched on by nothing")
offers only those no held doc requires — so no edit there can leave a held doc short, and
unticking one doc changes nothing about another. The server re-runs the same
raise-then-**floor** on every path that
persists a mapping — save *and* creation (routine scaffold, conversation create, the
composer's pre-start panel, the `/conversations/defaults` preview) — so two invariants
hold regardless of the client: *a held doc's requirements are always on*, and *a saved
mapping never expresses a capability its held permissions did not ask for*. A gated
action or reserved util cannot be enabled bare: the conduct doc is the switch, the
capability is only the means of asking for it. (The policy dials that ride a
capability — write_util's approval level, the run-history depth — stay yours and are
preserved across the floor.)

### `effect:` — what a toggle MEANS

Every rule and permission carries a one-line `effect:` in its frontmatter: what a routine
holding it DOES differently. The routine page labels its on/off control with that sentence, and
the linter requires the key.

It exists because the doc's TITLE cannot do the job. A title names a topic — `ask policy — when
and how to involve the user` — which tells a reader nothing they can act on, and beside a bare
checkbox it does not even say what ticking the box would do (operator, 2026-08-30). The two
facts a control needs are what changes, and that this control is what changes it; `effect:`
carries the first, and the panel frames it with the second ("on — this routine can: …" for a
permission, "bound — every run reads it and: …" for a rule, since holding one and binding the
other are not the same act).

The linter can only check presence and a length floor — whether a sentence is really a behaviour
is the author's job — so it also refuses one short enough to be a restatement of the title.

### `expects:` — the soft edge

`requires:` is the NECESSARY edge: activating a doc switches its capabilities on, and the
floor keeps them on. `expects:` is the optional counterpart — entities the instructions
*presume* but nothing enforces. It grants nothing, blocks nothing, and never fails a save.

It exists because the necessary edge was the only one the system could see. Publishing to the
Steward hub presumes READ access to the shared kit — the contract lives there; `read_file`
is confined to granted roots — which no capability can express. So `steward-publishing` says
`expects: {fs-read: ["…/web/steward"]}` and the setup surface shows the gap before the run.

Two rules keep it from turning into a second `requires:`:

- **It is legal on a RULE**, where `requires:` stays a lint error. A rule must never switch a
  capability on; it must be able to say what it presumes.
- **It stays advisory forever.** The moment it blocks a save it is a worse `requires:`, and the
  value of naming a soft dependency is precisely that it stays soft.
- **Declare it only for an UNCONDITIONAL presumption** — one where a holder without the entity
  can do nothing the doc describes. `expects:` produces an `interrupts` row on EVERY holder, so
  a doc whose prose applies only sometimes turns that row into noise on every routine that holds
  it, and the bar has now been missed twice in the same way. `git-checkpoint` presumed a
  git-tracked project dir most of its holders never touch, so its `expects: {fs-write: ["*"]}`
  was added and removed within a day (library `512ef3a`, "noise on every holder"). The hub's
  publishing doc then carried the same declaration on the same reasoning — "a page has to be
  published from somewhere" — and it was wrong for all seven holders it warned: a page is
  published through an UPLOAD channel; the documents a routine generates land in its own
  directory, which the sandbox always permits. Checked and dropped 2026-09-02.

  What survives the bar states a need the holder cannot route around: `steward-publishing`
  expects READ access to the shared kit, because `read_file` is confined to granted roots and a
  routine that cannot read the contract cannot build to it. The test is not "would this be useful" — it is
  "can a holder without it do anything this doc describes".

Values are entity CLASS → names from the `entities.py` vocabulary, with `"*"` for "at least one
of this class"; the prose explaining WHICH one belongs in the doc body. Nothing is declared here
that some other declaration already carries: a reserved util's secrets and its private
filesystem stores come from the util's own docstring header, which the resolver walks
transitively over `calls:`. Duplicating them here would be a second copy that can drift.

### The setup surface — what reads all of this

`readmodels/surface.py` is the forward reading of the whole dependency graph: it joins the
routine's config with the library's `requires:`/`expects:` and with the util HEADERS of every
reserved util the routine holds, then reports what is still unmet and what an unmet need will
COST — `blocks` (the call is rejected or fails), `interrupts` (the run stops mid-way to ask you)
or `note`.

`surface.py` is the JOIN only. The row vocabulary — the severities, `_node`, the one-row-per-
entity merge — is `readmodels/surface_nodes.py`, and the rows themselves come from three
emitters speaking it: `surface_needs.py` (util secrets, util filesystem stores, `expects:`),
`surface_schedule.py` (the suppressed cron, the started-by-nothing check, the phase key) and
`surface_caps.py` (capability coverage and the act that settles it). "Where a `fix` kind is emitted" is
therefore one of those four modules, never `surface.py` alone.

Nothing is stored. The library MOVES — a run may revise the utils and rules its routine is made
of, one copy each, reaching every holder at its next run — so a persisted resolution would be
stale the first time somebody ran `write_util`. It is recomputed at every read, which is what
lets one function answer at all four moments it matters:

| moment | who reads it | what it catches |
|---|---|---|
| the routine page (`GET /api/routines/{slug}/surface`) | you | first setup, and drift that landed since |
| `rsched validate` | CI and the deploy path | the same, with no page open — each unmet line ending in its remedy; a `blocks` row fails the command |
| run boot (an engine note) | the RUN | `blocks`/`interrupts` it would otherwise discover at turn nine |
| the turn boundary | the engine | live grants folding into the policy (unchanged) |

The engine note is advisory and never refuses to start: a diagnostic that can stop a run is
worse than the gap it reports, so a broken library yields no note rather than a dead run.

It carries `blocks` and `interrupts` only (`surface.BOOT_SEVERITIES`), which is what its own
closing sentence explains and nothing more. A `note` is addressed to the OPERATOR — a cron the
routine's lane suppresses, a `state/phase.json` recorded under some other key — and the run can
neither act on it nor be saved a turn by it, so one in front of every run buys prompt noise. The
page and `rsched validate` still show all three.

#### The remedy — what an unmet row also carries

A diagnosis that names no way out leaves every reader to find the control unaided — and each of
these readers would find it somewhere different. So an unmet row carries a **`fix`** beside its
`source`: the same idea pointed forwards, machine-readable for the same reason. It is a `kind`
plus that kind's parameters — `{"kind": "add_secret", "name": "FOO_TOKEN"}` — naming WHAT has to
happen and never where a console puts the control. A `:any` variant covers what `expects:` writes
as `"*"` ("at least one machine"), which no sentence can name by name.

The two halves stay honest apart because the callers share nothing:

- **the routine page** maps one kind to the one panel that owns that dial, opens any fold on the
  way, scrolls the reader onto the control and flashes it; a fix living off the page (Settings → Secrets, the Library)
  is a link instead. That map has ONE copy — `FIX` in `static/components/surface-view.js` —
  which the setup-check strip imports; the strip is what an operator reads first, so a row that
  offers a way out on one surface and not the other abandons the reader at the top of the page.
  The strip's BAND is weighted by its worst row, in the palette's own terms: `blocks` → err,
  `interrupts` → SUMMONS (a run that will stop and ask a person is the one case coral is for),
  and a note-only strip wears no band at all and folds into a `<details>` under a one-line
  summary. One band for every severity taught the reader that the band meant nothing, which is
  exactly the case it existed for. `surface.BOOT_SEVERITIES` weights the same two severities the
  same way on the boot note.
- **`rsched validate` and the boot note** have nothing to click, so `readmodels/remedies.py`
  spells each kind out in words: `REMEDIES` maps the kind to its sentence, `surface_lines` ends
  every unmet line ` — fix: <words>`. The phrasing sits in its own module because it reads
  nothing — no config is joined there and no verdict decided — so a wording change alters what
  an operator is TOLD and never what is true. A kind absent from that table renders no remedy
  rather than half a sentence.

That clause is free on a terminal and PROMPT TEXT at boot, where every `blocks`/`interrupts` line
is tokens the run buys — which is why the words are terse by contract (`remedies.REMEDIES`). What
they buy back is a run that can NAME what it needs when it asks the user or files a report,
instead of describing the symptom it hit.

**Only an UNMET row carries one** — the severities do not decide it. A `note` reporting a GAP
gets one: the cron its lane suppresses, a phase file keyed wrong, a capability switched on that
no held doc requires (`cover_or_drop` — hold a doc that requires it, or drop it). Exactly two
rows report a STATE and carry none. `action:write_recipe` "on" is a routine deliberately allowed
to rewrite its own instructions; `schedule:goal` "retired" is a routine whose every goal-scoped
condition is met. The only "fix" for a deliberate act is to undo it; offering that reads as a
defect report on a routine set up exactly as intended — the retired row worst of all, since what
it would undo is a finished job. An absent `fix` therefore means one thing, which is what makes
the rows that carry one worth reading.

`action:write_recipe` is the row where that rule is decided rather than observed: two checks
reach the id — the deliberate switch (no fix) and the uncovered-capability scan (`cover_or_drop`)
— and one row per entity survives. The deliberate-switch reading is the one that wins, because
naming what the routine IS beats offering to undo it; the precedence is pinned by a test, not
by the order the two appends happen to sit in.

#### Adding a `fix` kind — the four registrations

A kind is emitted once — in the `_node` call that names it — then consumed in four places. None
of them infers itself from that call; three of the four are held to the emission by an EQUALITY,
so the vocabulary cannot grow at one end alone:

- **its words** — `REMEDIES` in `readmodels/remedies.py`. Without them `rsched validate` and the
  boot note would print the gap with no way out. `test_every_remedy_can_be_said_in_words` parses
  both modules and asserts the emitted kinds are exactly the table's base kinds — a
  `kind:variant` entry is a second WORDING of one kind, so each is compared on the kind it
  belongs to. Miss this registration and that test reds on the set difference.
- **its panel** — the `FIX` map in `static/components/surface-view.js`, which the setup-check
  strip imports. Bound the same way from the browser side:
  `test_no_fix_kind_reaches_the_console_without_a_case_here` parses the map out of the file and
  asserts its keys are exactly the `REMEDIES` kinds, so missing it reds that test. At RUNTIME an
  unmapped kind renders no offer at all — a row that stays silent beats one pointing somewhere
  wrong — but silence is not a state a release may ship in: the operator would read the remedy on
  a terminal and find nothing on the page that owns the dial. The runtime is forgiving; the gate
  is not.
- **its anchor** — the section `id` the owning panel carries (`views/routine-config*.js` — the
  orchestrator plus its four `routine-config-{schedule,identity,models,access}.js` sections) or the
  `data-ability` / `data-drop` attribute an ability card stamps on the control
  (`components/abilities.js`). This is the ONE registration nothing static can see: a fix whose
  target is absent from the document disables itself, while a fix that travels to a panel where
  the act cannot be performed reads as correct until somebody presses it.
- **its journey** — a `CASES` row in `tests/ui/test_surface_fix.py` naming the ONE control that
  performs the act. The same equality covers it — the table is asserted equal to the console map
  — so a kind cannot reach the console without one. The row then presses the offer in a browser,
  asserting the flash lands on the destination rather than somewhere unrelated and that the
  control it named is there and operable. That run is what proves the anchor, which is why this
  table is where the pattern ends.

#### The reverse reading: who depends on THIS?

`library_impact.py` asks the same join backwards, because the library MOVES: one copy of every
util and rule, reaching every holder at its next run, with no migration and nothing to review.
That leverage is the design's best property, and it is why a routine nobody touched can stop
working overnight.

The break analysis is deliberately not a per-kind diff of headers and frontmatter. It computes
each holder's surface against the CURRENT library and against the PROPOSED one — over a shadow
library of symlinks, so the real one is never touched — and reports whoever gains a blocking or
interrupting row. The approval question and the routine page therefore cannot disagree about
what a gap means: one function, read forwards, twice.

Three writers, and each gets what it can carry:

- **the engine's authoring actions** — `write_util` and `write_rule` fold the blast radius into
  their existing approval question. `write_rule` already said "It binds: …"; now both say what
  the change would BREAK. Best-effort: an impact that could refuse a write would make a
  diagnostic the reason authoring fails, and the write gate is the selftest and the linter.
- **the Library tab** — no approval to hang it on, so `POST /api/library/{kind}/{slug}/impact`
  previews and the save carries the returned `impact_digest` back. A library that moved in
  between yields a different digest and the save is refused (409). Only a BREAKING change is
  gated: requiring a round-trip for every edit would train you to paste the token unread.
- **nothing at all** — a `git pull` by library-sync, an edit on disk, a restored bundle.
  `daemon/library_watch.py` compares the library's git HEAD on each scheduler tick and queues a
  `library-drift` record per newly-broken routine. A break is a DECISION (expose the secret,
  withhold it, unbind the rule), which is what the Decisions page already settles on entity ids
  — so it rides `pending.py` and inherits the page, the audit trail and browser push without
  inventing an outbound send. A record lives exactly as long as its gap: every re-resolve
  withdraws the records whose gap has closed — on a library change, at a daemon process's first
  check, and every ten minutes while any is open — since a secret added to the store or a
  settings accept closes a gap without the library moving.

#### The gap it also closes: a capability no held doc asks for

Two deliberate designs meet at one blind spot; each correctly declines to catch it:

1. the **floor** binds a routine's mapping at the web layer's save — never a file that reached
   disk another way (a hand edit, a restored backup, a migration);
2. **enforcement reads capabilities only**, precisely so the doc layer can never widen what a
   run may do.

So a routine.yaml can carry a reserved util or a gated kind with no conduct doc behind it — and
every layer stays silent. Nothing is broken when it happens — the routine really can do the
thing — which is why it is REPORTED rather than corrected: the surface shows it per routine as a
`cover_or_drop` row with its two ways out, both in the permissions panel — hold a doc that
requires the capability, or drop it in that panel's orphan card, where the save's floor makes the
drop stick. A reserved util the library no longer has is the same row with one more reading
(`install_util`): while a held doc requires it, the drop would be undone by the save's raise, so
the fix names that doc instead (`doc` on the fix, the `:doc` wording in `remedies.py`).

Enforcement reads **capabilities only** (`grants.py` builds the run policy from the
routine's own mapping); a doc-without-capability misconfiguration therefore fails
closed. Which utils are reservable at all is library-defined (the union of every doc's
`requires.utils`); which action kinds are gateable is engine-defined (`GATED_KINDS`) — a
library edit can reserve a new util, but can never retract a base action kind from every
routine.

## Why the split

Principle prose wants to be *shared and singular*: one text, many routines, improved from
what runs across all of them actually did with it — which is what the **rules-review** meta
routine does. Per-routine copies bought task-specific wording and paid for it with forks that
never received a fix. Enforcement wants something different again: it must be tamper-proof,
which is why changing rule TEXT is a capability and changing the rule SET is config no run can
write. And conduct prose
for a capability wants to be *toggleable with it* without conflating the two: the old
model (permission docs whose `grants:` both unlocked and instructed) meant you could
never enable a capability without one specific prose bundle, and every policy variant
needed its own doc (three util-authoring docs existed only to carry three approval
levels). Now the prose is a doc, the switch is config, and the approval level is a
per-routine setting.

## Rules

Every rule lives at `<libraries_home>/rules/<slug>.md` — a heading line
`# rule: <name> — <summary>`, `tags:` frontmatter (three minimum), **no requires** (a rule
carrying one is a lint error). One copy each; routines hold slugs. The shipped set — 16 rules, one file each in
`library-seed/rules/`, the authority this table is written from ([curated rules](curated-rules.md)
records where each came from):

| rule | what it states |
|---|---|
| `evidence-discipline` | every claim traced to an observation in this run; a blocked or unreadable step is reported as a gap |
| `ask-policy` | do what you can reach, request what you lack, bring the user only their decisions — batched, with options and one recommendation |
| `fix-the-cause` | a correction or a failure names a cause to remove, in this run, where the next run will read the fix |
| `problem-routing` | a problem goes to its owner as a diagnosis; a hand-off is answered in the run that gets it |
| `decision-record` | keep the reasoning the artefacts cannot carry, where the next run reads it first |
| `web-research` | look it up — facts and existing solutions — before relying on memory or building from scratch |
| `verify-independently` | check an outward-facing deliverable from outside the reasoning that made it |
| `audit-coverage` | report everything found and everything not examined; a narrowed scan must not read like a clean one |
| `change-scope` | change exactly what the task needs, then finish the change — every reference to a removed thing settled |
| `make-failure-visible` | code and tests you write must fail loudly; a test fails once on the regression it names |
| `work-order` | prove the uncertain part first, commit to it, then harden |
| `write-as-the-principal` | text that goes out as the user's reads as theirs and learns from their edits |
| `correspondence` | a message you sent is an open thread until it is answered, on every channel the counterpart uses |
| `feedback-loop` | act on what readers say in the run that reads it; notice when nobody responds |
| `interface-craft` | design for this brief and reader, then look at what you built |
| `git-checkpoint` | an undo point before you edit a project repo |

`evidence-discipline`, `ask-policy`, `fix-the-cause`, `problem-routing`, `decision-record` and
`web-research` are the routine `DEFAULT_RULES`: the conduct every kind of work meets.
Conversations take all but `decision-record` (their spine is the plan file) and add
`git-checkpoint`. The rest are the rules of a KIND of work, bound by the settings pattern for
that kind ([patterns](patterns.md)) — a steward's `feedback-loop` and `correspondence`, a
maintainer's `change-scope` and `make-failure-visible`.

Each rule's `effect:` states WHEN it applies; that line is what the state digest shows
beside the held slug (`rules.when_lines`), so a run knows which rule to read before which
moment without carrying the prose.

Improvement passes are deliberately NOT rules: the bundled **routine-improver**
meta routine sweeps every routine that doesn't set `improve: false` in its
routine.yaml (an include-by-default toggle on the routine page) and runs the five lenses — bugfix, research,
features, UI, efficiency — plus a fresh-eyes de-clutter pass on each, itself included.

### Which rules bind a routine (the SET)

`routine.yaml` `rules:` IS the state; nothing derives a copy of it — the state digest reads
it at every boot. The **user** binds or unbinds at any time from the routine page's *General rules* panel or the
conversation header (`POST /routines/{slug}/rules`, `POST /conversations/{slug}/rules` — one
shared implementation). Nothing is copied anywhere: binding records a slug.

Unlike other routine file edits this is **not** 409-guarded during a run — no run writes
routine.yaml, so the web layer is the only writer and no race exists. A newly bound rule even
reaches a run already in flight: the composed prompt is immutable (caching contract), so
`control.json` `add_rules` makes the engine append the prose (read from the library) as an
engine note at the next turn boundary. Unbinding reaches a live run the same way
(`drop_rules`): the prose already in its context cannot be unsaid, but its authority can — an
engine note says the rule no longer binds, and the opt-in `erase` also withdraws the text,
at the price of the provider cache from the first rewritten message on. Either way the rule's
[assists](rule-assists.md) arrive and leave with it.

A **run** never changes which rules bind it. It may `read_rule` any rule in the library —
ungated, because a routine must be able to read what binds it and library prose has no side
effect. Reading one it does not hold applies it for that run only; `name: "list"` returns the
catalog with each entry flagged when it binds. A rule that keeps proving necessary belongs in
the run's finish summary or a deferred `ask_user`.

A new routine starts with the rules of its settings pattern. The prompt never inlines the prose
— the state digest names each held rule with its moment and the run reads what it needs, which
keeps every turn lean. "Recommend for this routine" on the routine page proposes a different set
when the finished recipe calls for one, as a pending change ([patterns](patterns.md)).

### Who may change the TEXT

The library copy is the only copy, so a revision is leveraged: it reaches every holder at its
next run. Three writers, in increasing autonomy:

- **You**, on the Library tab, like any library doc.
- **A routine holding `rule-authoring`**, with the `write_rule` action — `content` to author a
  new rule, `anchor`/`replacement` to revise one in place. The library linter gates the write
  (heading, tags, no capabilities in frontmatter) BEFORE the approval ask, so a malformed draft
  never reaches you, and the approval question names every routine the change would reach.
- **The `rules-review` meta routine**, which is that capability pointed at the whole layer: it
  reads how runs actually interpreted each rule — followed, misread, ignored, or a good
  interpretation the text never contained — and revises from that evidence.

`write_rule` has its OWN approval dial, `rule_confirm`, rather than sharing write_util's
`confirm`. The decisions are different sizes: authoring your own tools affects you, rewording a
rule affects everyone holding it. Note the asymmetry that follows — a NEW rule binds nobody
until you bind it, so authoring is the cheap operation and revising is the expensive one.

There is deliberately **no `remove_rule`**. Deleting a rule silently un-binds every holder, and
unlike a util there is no callers check to catch it, so a run that believes a rule should go
says so in a `report` or a deferred `ask_user` and you delete it on the Library tab.

### `assists:` — surfacing the rule at the moment it applies

A rule may declare **assists**: `(moment, predicate) → line` entries in its own frontmatter
that surface its operative line exactly when it becomes relevant, so noticing the moment stops
being the model's job.

```yaml
assists:
  - id: after-a-failed-call
    moment: observation        # pre-action | observation | boundary | pre-finish
    predicate: observation-failed   # resolved by NAME from the engine's registry
    payload: remind            # remind | hold — coupled to the moment
    line: >-
      Read this failure before reacting to it — the message, the exit code, the usage line.
```

The moment and the payload are COUPLED (`assists.MOMENT_PAYLOADS`), not free to combine:
`pre-action` can only HOLD — at the point an action is chosen but not executed, the only way to
reach the model is to stop it — and the three free moments can only REMIND, because a hold needs
an action to stop and none of them has one. `rule-assists.md` carries the table and the worked
examples; this page does not restate it.

The rule does not shrink, it FACTORS: the trigger moves off the model, the operative line
becomes the surfaced payload, and the rationale stays in the body where `read_rule` reaches
it. A predicate is named, never shipped — a rule is prose in a git-synced multi-writer
directory. Validation runs in `lint_rule_text`, so one check covers `write_rule`, the Library
PUT, `rsched lint` and the Library page's per-rule problems.

An assist is NOT a capability. The user already decided the routine practises the rule
(`effect.when` is that decision), and an assist changes only when its line is read. It is
also why the seed sync cannot deliver one: that sync is add-only and every rule already
exists live, so each batch needs a one-shot migration. Full narration in
[rule assists](rule-assists.md).

## Capabilities

`routine.yaml`:

```yaml
capabilities:
  actions: [write_util, revise_util]  # gated action kinds switched on
  utils: [signal:send]          # reserved utils — or one VERB of one — switched on, by name
  confirm: creations            # write_util approval: always | creations | never
  rule_confirm: always          # write_rule approval: always | creations | never
  remind_confirm: always        # curated-reminder approval: always | creations | never
  runs: last                    # previous-run read depth: none | last | all
  reminders: local              # consequence reminders: none | local | global
```

A new routine's default is its settings pattern's; with no pattern at all it is
`config.base.DEFAULT_CAPABILITIES`: `write_util` + `revise_util` (confirm `creations`), no
reserved utils, the last run readable, and `reminders: local`. `read_rule`, the `util` action,
memory and the routine's own scripts are not listed because they are not gated.

`runs`, `reminders` and the three approval dials are SETTINGS: yours per routine, never switched
on by a permission and kept by the floor with none behind them. `reminders: local` is the
default because a caution a run leaves itself about its own actions is ordinary conduct; at
`local` a run writes its own reminders and applies them beside every curated one that reaches
it; `global` also lets it write the curated store — the curator's setting, every write approved
under `remind_confirm`. See [reminders](reminders.md).

### Where a routine's settings come from

Settings patterns ([patterns](patterns.md)) are where a routine's settings start: a routine's
settings are its own `routine.yaml`, read against the pattern it follows; nothing is layered
under that file.

### Names and verbs gate a util

`utils:` names what is reserved: a whole util (`browser-session`) or ONE verb of one
(`signal:send` — the util's first positional argument). Verb reservation is how a routine reads a
channel freely while sending through it stays a decision: every other verb of `signal` stays open.
A permission doc's `requires.utils:` declares what its conduct presumes; the routine's
`capabilities.utils:` is the user's switch. A util nobody's permission names is open to every
routine — the catalog is fail-open, so a new channel becomes a permission naming its sending verb.

### The gate follows `calls:` too

A util's docstring `calls:` line is a declaration with consequences: `utils_run.util_needs`
unions every callee's `secrets:`, `net:` and `fs:` into the caller's ONE jail and ONE env, and
the library root is on PATH for every util, so naming a sibling both receives that sibling's
credentials and lets the caller exec it. Gating only the name the model typed left the edge as
a second, unaudited door into a reserved channel — `captcha-fetch` (ungated, callable by every
routine) names `browser-session`.

So the gate (`rsched/utilgate.py`, asked by `GrantPolicy.deny`) walks the tree and refuses a
call whose `calls:` graph reaches a gated util the
routine does not hold, naming the EDGE (`util 'captcha-fetch' declares \`calls:
browser-session\`…`) and routing to the ordinary one-click request for the reserved util. A
routine that legitimately needs the caller holds the callee, exactly as it would to call it
directly. The walk costs a few header reads and only runs when the library has gated utils at
all.

## Permissions (conduct docs)

Library docs live in `<libraries_home>/permissions/*.md` — a heading line
`# permission: <name> — <summary>` plus a machine-read `requires:` frontmatter key. The
LIBRARY copy is the only authority for `requires:`; routines keep no local copies. The
`requires:` panel on the Library tab's permission editor is prefilled from the
frontmatter and authoritative for that key on save.

```yaml
---
tags: [tool-use, utils, authoring]
effect:                        # what holding it changes and when it applies
  with: …
  without: …
  when: …
requires:
  actions: [write_util]        # gated action kinds these instructions presume
  utils: [signal:send]         # reserved utils (or one verb of one) presumed, BY NAME
expects:                       # the SOFT edge — presumed, never enforced (see above)
  fs-read: ["/path"]           # entity CLASS → names; "*" = at least one of the class
---
# permission: <name> — <summary>
<a SHORT body: shown in the UI, and appended to the prompt's CAPABILITIES section when held>
```

(No setting in `requires:` — an approval level, a history depth or the reminder layer is your
choice per routine, never a doc's demand; the linter refuses one.)

The shipped set — 17 docs, one file each in `library-seed/permissions/`, which is what a fresh
install seeds and the authority this table is written from. A permission exists only where
holding it is a DECISION: a channel that reaches a person, an act that changes what every
routine shares, a reach outside the routine's own world.

| permission | requires | default |
|---|---|---|
| `util-authoring` | `write_util` + `revise_util` — create a util, or change one the library already has (the approval level is `confirm`) | ✅ held by new routines |
| `util-removal` | `remove_util` — delete a global util (and the never-recreate-deleted guard that follows it) | opt-in |
| `rule-authoring` | `write_rule` — author or revise a general rule in the shared library (the approval level is `rule_confirm`) | opt-in |
| `recipe-authoring` | the `write_recipe` capability — revise this routine's OWN `main.md` / `stages/` / `tuning.yaml`; `routine.yaml` stays sealed regardless | opt-in |
| `scheduling` | `schedule_run` — arm or cancel a one-shot future run of a routine | opt-in |
| `shell` | the `shell` action — one host command per turn, in the run's own jail with no secret injected | opt-in |
| `outbound-mail` | `gmail:send`, `fau-mail:send` — send email from the user's own mailboxes | opt-in |
| `steward-publishing` | nothing; expects READ access to the shared kit — publish this routine's page on the Steward hub (see [status pages](status-pages.md)) | opt-in |
| `browser-sessions` | `browser-session` — drive the shared browser that is already signed in (see [browser sessions](browser-sessions.md)) | opt-in |
| `darknet` | `darknet` — read Tor hidden services (see [darknet](darknet.md)) | opt-in |
| `usenet` | `usenet:post` — post articles over NNTP (see [usenet](usenet.md)) | opt-in |
| `notifications` | `ntfy` — push a notice to the user's own devices | opt-in |
| `messaging-discord` | `discord:send` — post through the bot | opt-in |
| `messaging-signal` | `signal:send` — send Signal messages as the user | opt-in |
| `messaging-telegram` | `telegram:send` — send Telegram messages as the user | opt-in |
| `messaging-whatsapp` | `whatsapp:send` — send WhatsApp messages as the user | opt-in |
| `messaging-zulip` | `zulip:send` — post to Zulip as the user | opt-in |

`util-authoring` is `config.base.DEFAULT_PERMISSIONS` and `conversations.CONVERSATION_PERMISSIONS`;
everything else arrives with a settings pattern or a click. `bootstrap.ADOPT_PERMISSIONS` is the
list a routine that already existed picks up once at boot when a default is added afterwards —
empty today.

The messenger docs are one per channel rather than one bundle: each names a different reserved
verb with a different credential; holding one is a decision about a different audience.

### What enforcement looks like

A run's allowed action kinds are **workflow `tools:` ∩ (base ∪ enabled capabilities)**
(`finish` always allowed). Gated calls — `write_util` switched off, a reserved
util, a `read_file` into `runs/` beyond the enabled depth, and any `write_file` into the run's
OWN recipe — `main.md` / `stages/` / `tuning.yaml`, gated by the **`write_recipe`** capability
(held through the `recipe-authoring` doc). Until 0.261.0 this was not a capability at all: it
unlocked as a SIDE EFFECT of a user-granted fs_write_root covering the routine's own dir, so
granting a routine write access to its working directory silently also granted it the right to
reword its own task. Those are different decisions; `routine.yaml` stays sealed under both — are
rejected inside the schema-retry cycle by `validate_action`, with an error naming the way out:
a typed ACCESS REQUEST for the denied entity (see the grant model below), or the settled
do-not-re-request wording when the user already declined it. A run NEVER writes
its own `routine.yaml` at all: config (budgets, models, permissions, capabilities, fs-roots) is
the user's, so even the routine-improver proposes a config change with a deferred `ask_user`
rather than editing the file. A rejected call never becomes a turn. The current run's own
`runs/<ts>/` tree (status, archived history) stays readable regardless — the engine itself
points the model there after compaction. `runs/` is never writable. One more write_util-
specific rule rides the same schema-retry rejection: a util the user **deleted** from the
library (a deletion in its git history) is never recreated silently — the run must `ask_user`
first, and only an explicit yes that run unblocks it (see [sandboxing](sandboxing.md)). Every
util also runs inside a Landlock sandbox scoped to the run's filesystem roots, declared
secrets, and declared network need — a distinct, always-on layer, not a capability.

The model sees its surface in the prompt's machine-facing **CAPABILITIES** section —
the enabled capabilities, the held permission slugs, and each held permission's short
conduct note. Permission prose never appears in the natural-language part of the prompt;
that is what the general rules are for.

Sub-workflows (`spawn`) run with permissions and capabilities off: no gated kinds, no
reserved utils, no recipe writes, no rules of their own.

Budgets, `fs_read_roots` / `fs_write_roots` and schedules are resources, not capabilities —
they stay plain `routine.yaml` config.

## Access requests — the grant model

Every grantable thing has ONE id in the entity vocabulary (`entities.py`):
`action:<gated-kind>`, `util:<reserved-name>` (or `util:<name>:<verb>`), `secret:<STORE_NAME>`,
`connection:<provider>`, `machine:<name>`, `fs-read:<path>` / `fs-write:<path>`,
`runs:last|all`, `reminders:local|global`, `recreate:<deleted-util-slug>`. A run that hits a
gate files an `ask_user` carrying `request: "<entity-id>"` (the question stays its prose —
WHY it needs the entity); the Decisions page renders the typed decision buttons — four
(allow/deny × now/forever) for every class, plus a fifth, **allow once (this action
only)**, offered for the once-grantable classes (`entities.ONCE_CLASSES`, below) — and each
entity is always in exactly one of four states (a once-grant passes through *allowed now*
and back out):

- **allowed forever** — the entity's NATIVE routine.yaml key: a capability switched on
  through the permission cascade (allow-forever activates a covering conduct doc and
  runs the same raise-then-floor the permissions editor uses), a connection/machine
  binding, an fs root. `secret:` is the one class with no native switch — its
  allow-forever is a `grants:` true row, and it covers the CENTRAL store only: a
  routine-scoped secret (D103, `secrets.d/<slug>.env`) is owned by its routine, so there is
  nothing to decide and the gate skips the name entirely. Only a REQUIRED secret declaration triggers the
  engine's own blocking request; an OPTIONAL one (`NAME?` in the util header, D51/F290)
  is withheld from the call instead of prompting — the run requests it explicitly when a
  call really needs it.
A reserved util may be requested as `util:signal:read`: the scope is exactly the
first argument, not any later argument. A bare `util:signal` grant still covers all
verbs. A broad conduct doc can cover a scoped grant; a scoped-only doc cannot cover
a broad grant or another verb. The forever request path enables only the named util
scope, not sibling utils or tag classes. Subsequent unrelated capability decisions
preserve that actual util/tag authority instead of re-raising old conduct docs.

Util denials retain the existing **declined request**, not capability-revocation,
contract. Declining `util:signal` suppresses further requests for that util, including
scoped requests, but does not revoke a separately approved `signal:read` capability or
run overlay. Exact scoped tombstones likewise suppress that request; they are not
subtractive exceptions to a previously granted broad capability. Explicit later
answers to already-pending requests still apply their named grant: answers are not
invalidated by a different entity's denial. To revoke existing authority, change the
capability itself on the routine page. This distinction also holds after YAML reload
and for once overlays (which are still spent by the matching dispatched use).
Selective reservation of only some subcommands of an otherwise public util is a
separate feature; this request grammar does not implement it.

- **denied forever** — a `grants: {<entity-id>: false}` tombstone. The run stops asking:
  denials switch to "the user has PERMANENTLY declined … do not request it again", the
  request itself is corrected in-cycle, and the catalog badges a tombstoned reserved
  util `[reserved — declined by the user]`. The routine page's *Declined access* panel
  removes a row (back to undecided).
- **allowed now / denied now** — this run only, in-memory on the RunContext (a resumed
  leg starts empty and re-asks). An allow-now folds into the live policy at the turn
  boundary — the transport schema is re-projected, so a granted kind becomes generatable
  on the very next turn — and reaches every enforcer: `validate_action`, the util
  sandbox's filesystem roots, and the declared-only env injection (a once-granted
  connection/machine/secret flows exactly like a bound one, for this run). Decided
  between runs (a deferred request answered on the Decisions page), the consuming run's
  boot seeds the overlay before the prompt is composed; decided while the run is LIVE
  (the same deferred request, answered mid-run), the next turn boundary's inbox drain
  bridges the decision into the running overlay — forever-decisions included, since the
  run's loaded config predates the click — so "usable now" holds for the running run
  too, not just the next one. Entity ids are canonicalized where the request enters
  (`fs-read`/`fs-write` paths expand to one absolute form), so the record, the config
  write and the overlay always name the same root.

- **allowed once** (D65 for the turn-action classes `action:` / `util:` / `runs:`; D76
  widened it to `secret:` / `fs-read:` / `fs-write:`) — an allow-now that the engine
  REVOKES after exactly one use. The grant
  seeds the same run overlay (so it reaches the same enforcers, and the CAPABILITIES
  line marks it "(one action only)"); the first successfully-DISPATCHED matching action
  spends it — the engine drops it from the overlay and rebuilds the policy at that same
  boundary, appending an `[ONCE-GRANT SPENT: …]` line to the consuming observation so
  the next matching attempt is not an unexplained denial. A schema retry or a validation
  rejection never consumes (it never becomes a turn), and neither does a user gate
  refusing the call pre-execution (a declined write_util, refused secrets) or a bounced
  handler (unknown target, failed read, missing util) — the grant is spent by USE, not
  by attempt. For the turn-action classes the spend is exact: their use IS a turn action
  `validate_action` observes. A secret or an fs root is consumed inside a util SUBPROCESS
  the engine never sees as a turn, so D76 (operator, 2026-08-06) accepted an explicitly
  COARSER promise for those: the grant is spent by the next action that RECEIVES it — a
  secret by the next util call whose declared env (its `calls:` tree included) carries it,
  an fs root by the next util invocation (every jail mounts the granted roots wholesale) or
  the next file action under it (`engine/requests._once_match`). `connection:` and
  `machine:` stay four-state — a binding carries an account or a host no single action
  uses up — `reminders:` is a dial for the whole run, and `recreate:` is a per-run unlock;
  the API refuses `allow once` for those four.

Ownership is strict: FOREVER decisions are persisted by the WEB layer at click time —
the engine never writes routine.yaml, not even to record an approval. Sub-workflows
cannot request; they inherit the parent's RESOURCE grants (fs/secret/connection/machine)
and none of its capability grants. `recreate:<slug>` deliberately has no allow-forever: a
fresh user deletion must
always outrank an old grant. Secret exposure (D39) rides this same flow: the first util
call declaring an undecided store secret files one blocking request covering every
undecided name.

What is deliberately NOT an entity — structurally impossible stays impossible, never a
deniable row: `routine.yaml` writes, `runs/` writes, `.memory/` via file actions,
own-recipe writes, base action kinds, which rules bind a routine (config, never a run's;
the grantable thing near it is `action:write_rule`, the right to change rule TEXT), conduct
docs (they ride the cascade; they grant nothing).

"Impossible" is a claim about WHERE each one is enforced, so each names its enforcer:

- **`routine.yaml` writes** — the action path, three times (`grantpolicy`,
  `fileops._write_gate`, `grants`). NOT in the kernel, and it cannot be: the file sits in a
  directory every callable kind must have read-write. A `shell` command, a script or an
  `fs: roots` util can write it, and `utils_run.run_jailed` reports the change rather than
  preventing it (docs/sandboxing.md § `routine.yaml` is sealed at the action layer).
- **`.memory/` via file actions** — refused twice, and it needs both: `engine/actions.py`
  tests the model's own string inside the schema-retry cycle (no turn spent), and
  `fileops._memory_gate` tests the RESOLVED path, which is what makes
  `state/../.memory/INDEX.md` and the absolute form refusals too.
- **`runs/` writes** — `fileops._write_gate` on the resolved path, with `_runs_read_gate`
  backstopping the relative form.

The same question applies to the three fs paths that are never grantable at all
(`entities.NEVER_GRANTABLE`: the instance config dir, `~/.credentials`, `~/.ssh`). They are
refused at the runtime ask, refused at the config PATCH, and REPORTED by the loader for a
file that already lists one — that last one deliberately does not drop the root, because a
routine that has legitimately been reading it would otherwise fail its next run with nothing
naming the cause. All three compare a root as written AND as resolved
(`entities.never_grantable_fs`): the jail opens a root to build its rule, opening follows
symlinks, so a link into a store — `/tmp` is writable in every jail — is the store.

## Working with them

- **See** a routine's surface: the routine page's *Permissions & capabilities* panel — one
  ABILITY CARD per conduct doc, with everything that ability depends on inside it: the
  capabilities its `requires:` names, and the secrets, private stores and bindings the resolver
  derived from the util headers. The card's badge states the verdict (`ready` / `needs a
  decision` / `will fail`) so the reader never adds it up. A final card collects capabilities no
  held doc asked for. Its rules: the *General rules* panel below it.

  This replaced a two-column layout (docs left, capabilities right, each badged with what
  required it). That was faithful to the model and asked the reader to do the join by eye —
  and only two of an ability's four halves were even on that screen; the secrets and the
  filesystem roots were on other panels. Rows are attributed by the surface's machine-readable
  `source`, never by parsing its prose.
- **Change** either layer there (takes effect next run; the cascades keep them
  consistent). Bind or unbind a rule in the *General rules* panel; change what a rule SAYS on
  the Library tab — or let the rules-review routine revise it from run evidence.
- **Create** a new conduct doc: Library tab → Permissions — the `requires:` panel is
  editable and prefilled. To reserve a util for a subset of routines, name it in a doc's
  `requires.utils` — it becomes a capability every routine must have switched on to call.
- Any future permission-ish lever becomes a capability (a `capabilities:` key +
  a `requires:` entry on the covering doc), not a new yaml key.
