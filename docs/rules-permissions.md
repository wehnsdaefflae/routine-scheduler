# Rules, permissions & capabilities

A routine's cross-cutting behavior is split into sets with deliberately different
ownership:

- **Rules** — *general rules*: principle prose (when to ask the user, research discipline,
  what to record) that a run applies to its own particular case. A rule has exactly ONE copy,
  in the shared library; a routine holds SLUGS (`rules:` in routine.yaml), named at the end
  of its `main.md` (the *Standing practices* section) and read on demand with `read_rule`.
  Revising the library text therefore reaches every routine holding that rule at its next
  run, with no migration and no per-routine fork to drift. The two halves are owned apart:
  the SET is yours (the routine page's *General rules* panel), the TEXT is the library's —
  yours on the Library tab, and writable by a routine holding the **rule-authoring**
  permission.
- **Capabilities** — the atomic, engine-enforced surface: gated action kinds
  (`write_util`, `memory_read`, `memory_write`, `shell`), reserved utils held by name
  (`remote`, `discord`, `darknet`, `usenet` / `usenet-nzb`) or by TAG CLASS (`util_tags:` —
  every util carrying that docstring tag, including ones the library gains later), the
  write_util approval level, and the previous-run read depth. Held via `routine.yaml`'s
  `capabilities:` mapping, changed **only by you** (the routine page's panel; the web
  layer blocks edits while a run is active), and enforced when every single action is
  interpreted. A routine can never grant itself anything.
- **Permissions** — *conduct docs*: library prose stating HOW to use a capability well.
  Held via `routine.yaml`'s `permissions:` list; a held doc's short body reaches the
  prompt's CAPABILITIES section. A permission's frontmatter `requires:` names the
  capabilities its instructions presume — it grants nothing itself.

One sentence each: **rules shape how a routine works; capabilities bound what it may
do; permissions instruct it in what it may do.**

## The two permission layers and their cascade

Activating a permission switches on the capabilities its `requires:` names. Switching a
capability off deactivates every permission that requires it. Both cascades live in the
UI (the routine page shows the two layers side by side, each capability badged with the
docs requiring it); the server re-runs the same raise-then-**floor** on every path that
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

It exists because the necessary edge was the only one the system could see. `remote-machines`
requires the `remote` util — and is useless without a bound MACHINE, which no declaration named,
so the gap only ever surfaced as a run burning a turn on an empty host list. Now the doc says
`expects: {machine: ["*"]}` and the setup surface can show the gap before the run.

Two rules keep it from turning into a second `requires:`:

- **It is legal on a RULE**, where `requires:` stays a lint error. A rule must never switch a
  capability on; it must be able to say what it presumes. `status-page` tells a run to build its
  page to a shared contract it can only learn by READING the shared kit, and the engine confines
  `read_file` to granted roots — that is an `expects:`, not a grant.
- **It stays advisory forever.** The moment it blocks a save it is a worse `requires:`, and the
  value of naming a soft dependency is precisely that it stays soft.
- **Declare it only for an UNCONDITIONAL presumption** — one where a holder without the entity
  can do nothing the doc describes. `expects:` produces an `interrupts` row on EVERY holder, so
  a doc whose prose applies only sometimes turns that row into noise on every routine that holds
  it, and the bar has now been missed twice in the same way. `git-checkpoint` presumed a
  git-tracked project dir most of its holders never touch, so its `expects: {fs-write: ["*"]}`
  was added and removed within a day (library `512ef3a`, "noise on every holder"). `status-page`
  then carried the same declaration on the same reasoning — "a page has to be published from
  somewhere" — and it was wrong for all seven holders it warned: a status page is published
  through an UPLOAD channel (the file-transfer capability plus a `FTP_SOURCES` entry), and the
  documents a routine generates land in its own routine directory, which the sandbox always
  permits. Nothing any of the seven does needs a write root, so every one of those rows was
  false. Checked and dropped 2026-09-02.

  What survives the bar states a need the holder cannot route around: `remote-machines` expects a
  bound MACHINE, because every call returns nothing without one, and `status-page` expects READ
  access to the shared kit, because `read_file` is confined to granted roots and a routine that
  cannot read the contract cannot build to it. The test is not "would this be useful" — it is
  "can a holder without it do anything this doc describes".

Values are entity CLASS → names from the `entities.py` vocabulary, with `"*"` for "at least one
of this class"; the prose explaining WHICH one belongs in the doc body. Nothing is declared here
that some other declaration already carries: a reserved util's secrets and its private
filesystem stores come from the util's own docstring header, which the resolver walks
transitively over `calls:`. Duplicating them here would be a second copy that can drift.

### The setup surface — what reads all of this

`readmodels/surface.py` is the forward reading of the whole dependency graph: it joins the
routine's EFFECTIVE config (domain inheritance merged) with the library's `requires:`/`expects:`
and with the util HEADERS of every reserved util the routine holds, then reports what is still
unmet and what an unmet need will COST — `blocks` (the call is rejected or fails), `interrupts`
(the run stops mid-way to ask you) or `note`.

Nothing is stored. The library MOVES — a run may revise the utils and rules its routine is made
of, one copy each, reaching every holder at its next run — so a persisted resolution would be
stale the first time somebody ran `write_util`. It is recomputed at every read, which is what
lets one function answer at all four moments it matters:

| moment | who reads it | what it catches |
|---|---|---|
| the routine page (`GET /api/routines/{slug}/surface`) | you | first setup, and drift that landed since |
| `rsched validate` | CI and the deploy path | the same, with no page open; a `blocks` row fails the command |
| run boot (an engine note) | the RUN | `blocks`/`interrupts` it would otherwise discover at turn nine |
| the turn boundary | the engine | live grants folding into the policy (unchanged) |

The engine note is advisory and never refuses to start: a diagnostic that can stop a run is
worse than the gap it reports, so a broken library yields no note rather than a dead run.

It carries `blocks` and `interrupts` only (`surface.BOOT_SEVERITIES`), which is what its own
closing sentence explains and nothing more. A `note` is addressed to the OPERATOR — a cron the
routine's lane suppresses, a `state/phase.json` recorded under some other key — and the run can
neither act on it nor be saved a turn by it, so one in front of every run buys prompt noise. The
page and `rsched validate` still show all three.

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
  inventing an outbound send.

#### The gap it also closes: a capability no held doc asks for

Three deliberate designs meet at one blind spot, and each correctly declines to catch it:

1. the **floor** binds a routine's OWN mapping, at save;
2. a **domain's** shared config block is deliberately not floored at its own save — a member may
   hold the covering doc itself, so flooring the domain in isolation would delete a capability
   that is legitimately covered;
3. **enforcement reads capabilities only**, precisely so the doc layer can never widen what a
   run may do.

So a domain can hand its members a reserved util or a gated kind with no conduct doc behind it —
and every layer stays silent. Nothing is broken when it happens — the routine really can do the
thing — which is why it is REPORTED rather than corrected: the surface shows it per routine
however it got there (naming the domain when the domain supplied it), while the domain PATCH
returns a warning naming it at the moment somebody saves (`orphan_capabilities` on the
`/api/domains` record). Neither refuses, because refusing would break the legitimate
member-holds-the-doc arrangement.

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
carrying one is a lint error). One copy each; routines hold slugs. The shipped set:

| rule | what it states |
|---|---|
| `ask-policy` | when and how to involve the user: self-sufficiency by default, deferred asks, batching, self-contained questions |
| `web-research` | verify external facts by searching instead of recalling; provenance discipline |
| `decision-record` | keep the reasoning the artefacts cannot carry: read the record before exploring, append what changed and what you rejected, keep it bounded |
| `intent-inference` | read every user intervention as a standing preference — name the intention behind it, record it as a hypothesis, act on it, correct it in the open |
| `root-cause-fix` | repair the cause, never the symptom: trace it back until the answer names something changeable, install a GENERAL prevention at the level the cause lives at, in the run that found it |
| `problem-routing` | send a problem to whoever owns it, not upward — the artefact that must change names the owner; write a work order, not a hint; close what you receive |
| `git-checkpoint` | undo points for external project repos (and conversation dirs) the run edits — a checkpoint commit before risky edits and one after, named in the reply; never pushes unless asked |
| `evidence-discipline` | every reported claim traced to an observation from this run; verified-or-not as a binary, never a confidence score; failure reported as failure |
| `unexamined-is-not-clean` | a check reports on what it read, never on what it skipped: give every result its denominator, declare each exclusion with a reason, surface anything dropped that no reason covers, and count the denominator with something you did not write |
| `decision-commitment` | choose an approach and stop re-deciding: act when further lookup wouldn't change the action, revisit only on contradicting evidence, narrate the choice not the survey |
| `error-recovery` | read a failed observation before reacting to it: state the error, change something material before retrying, treat two failures at one step as "the approach is wrong" |
| `change-restraint` | the smallest change that does the job: no speculative structure, no compatibility shims, never hardcode past a check, say when the task itself is wrong |
| `independent-verification` | check work from outside the context that produced it — a mechanical check first, else a `subtask` verifier briefed without your reasoning; self-review is the weakest option |
| `review-recall` | for review/audit tasks: find first and filter second, label uncertainty instead of omitting, name what you did not cover |
| `teaching-insights` | explain the reasoning where a human is reading (conversations, reports) — short insights at real decision points, specific to this work; costs output length |
| `interface-design` | build UI that looks chosen rather than generated: pin the subject first, know the current default looks well enough to avoid them, plan a token system and critique it before coding, spend boldness in one place |
| `interface-copy` | words as design material — name things by what the reader controls, active voice with a stable vocabulary, errors that explain and direct, one job per element |
| `test-design` | a test earns its place by failing: name the regression first, assert behaviour not internals, watch it fail once before accepting it |
| `failure-visibility` | error handling *written into code* — never catch without a reaction, enumerate what a broad catch would swallow, fallbacks are features not safety nets, stubs never ship |

`ask-policy`, `web-research`, `decision-record` and `intent-inference` are the routine
`DEFAULT_RULES`. `git-checkpoint` is **not** a routine default — creation preselects it for
repo-editing tasks, and it is a standing default for **conversations** (see the Conversations
guide).

`root-cause-fix` pairs with `intent-inference` and the two are deliberately separate: one asks
what the user WANTED (a standing preference to predict), the other asks why they had to say it
at all (a defect with a cause to remove). Routines that had both folded into one local module
now hold both.

Three former modules are deliberately gone from this layer. `global-utils` became a
**permission**: it is mechanism prose (how to discover a util's flags, what to do when one
errors), and mechanism is what a conduct doc is for — a rule names no tool. `ledger-discipline`
became `decision-record`: the same purpose (a run must not re-buy a lesson a previous run
already paid for) stated as a principle, with the filename, entry format and rotation threshold
left to the workflow patterns that actually own the mechanism. `maintenance-routing` split along
the seam between its two independent halves: the REPORTING discipline is now the general
`problem-routing` rule, while the instance's ownership table — which routines exist here and
what each owns — is not general at all and lives in the `rules-review` routine's recipe.

The eleven below `git-checkpoint` are the **curated set** — distilled from Anthropic's
prompt-engineering guidance, the Claude Code plugins (their skills and prompt-snippet references as
well as the output-style hooks), OpenAI's agent prompting guide, and the self-correction and
verification literature (see the reasoning notes in
[`docs/curated-rules.md`](curated-rules.md)). None is a default: each is opt-in per routine, and
a rule that is not held contributes nothing at all — the whole point of a selectable set rather
than one always-on block. Deliberately **not** included, because the
evidence is against them or the harness already covers them: "double-check your own work" (unaided
self-correction breaks about as many correct answers as it fixes — hence `independent-verification`
instead), "don't be sycophantic" (measured as the least effective mitigation tested), numeric
confidence scores (verbalized confidence is systematically overconfident), and parallel tool calls
(architecturally impossible under one action per turn).

Improvement passes are deliberately NOT rules: the bundled **routine-improver**
meta routine sweeps every routine that doesn't set `improve: false` in its
routine.yaml (an include-by-default toggle on the routine page) and runs the five lenses — bugfix, research,
features, UI, efficiency — plus a fresh-eyes de-clutter pass on each, itself included.

### Which rules bind a routine (the SET)

`routine.yaml` `rules:` IS the state; main.md's Standing practices tail is a derived index
rebuilt from it on every change (`rsched/rules.py` — the one place that convergence lives).
The **user** binds or unbinds at any time from the routine page's *General rules* panel or the
conversation header (`POST /routines/{slug}/rules`, `POST /conversations/{slug}/rules` — one
shared implementation). Nothing is copied anywhere: binding records a slug.

Unlike other routine file edits this is **not** 409-guarded during a run — no run writes
routine.yaml, so the web layer is the only writer and no race exists. A newly bound rule even
reaches a run already in flight: the composed prompt is immutable (caching contract), so
`control.json` `add_rules` makes the engine append the prose (read from the library) as an
engine note at the next turn boundary. Unbinding takes effect at the next run — prose already
in a live context cannot be unsaid.

A **run** never changes which rules bind it. It may `read_rule` any rule in the library —
ungated, because a routine must be able to read what binds it and library prose has no side
effect. Reading one it does not hold applies it for that run only; `name: "list"` returns the
catalog with each entry flagged when it binds. A rule that keeps proving necessary belongs in
the run's finish summary or a deferred `ask_user`.

A new routine starts with the rules its PATTERN names (`META.includes`), and `main.md` ends with
a *Standing practices* section naming each ("read it before the situation it governs"). The prompt
never inlines the prose — the state digest lists the held slugs and the run reads what it needs,
which keeps every turn lean. Nothing judges the set at creation, because there is nothing to judge
yet: the recipe is what creation writes. That judgement is `recommend_setup` on the routine page,
against the finished recipe, as advice beside each toggle (D108).

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
    moment: observation        # observation | boundary | pre-finish
    predicate: observation-failed   # resolved by NAME from the engine's registry
    payload: remind            # remind → scaffold → do → hold; only remind is built
    line: >-
      Read this failure before reacting to it — the message, the exit code, the usage line.
```

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
  actions: [write_util, memory_read, memory_write]  # gated action kinds switched on
  utils: [discord]              # reserved utils switched on, BY NAME
  util_tags: [messaging]        # reserved util CLASSES switched on, by tag
  confirm: always               # write_util approval: always | creations | never
  rule_confirm: always          # write_rule approval: always | creations | never
  remind_confirm: always        # GLOBAL reminder approval: always | creations | never
  runs: none                    # previous-run read depth: none | last | all
  workflows: catalog            # subtask pattern sourcing: catalog | generate
  reminders: none               # consequence-reminder stores: none | local | global
```

A new routine's default: `write_util` (confirm `always`) + the memory pair, no reserved utils,
no run history, no rule authoring, no reminders — matching the default permission set below.
`read_rule` is not listed because it is not gated.

`reminders` is the one dial with no baseline at all. Run history floors at `last` for every
routine because reading the previous result costs nothing; a consequence reminder costs a TURN
every time it fires, so the layer stays off until you switch it on. At `local` a run reads and
writes its own store; at `global` it also reads the library's curated one (its own overriding it
where the patterns match) and is the only level that may WRITE there, under `remind_confirm`.
That second dial is separate from `confirm` and `rule_confirm` for the reason those two are
separate from each other: a new global reminder starts interrupting routines that never asked for
it. See [reminders](reminders.md).

### Settings templates — the named starting point

Reading the 28 live routines, the five setup layers are almost never chosen independently:
eight rules are held by two thirds of them, `memory` + `util-authoring` + `util-revision` by
nearly all, and the differences fall into a handful of recognisable JOBS. A **template**
(`<libraries_home>/templates/<slug>.md`) bundles that: the same keys a domain's shared config
carries.

**A template is a PRESELECTION, not a layer.** Adopting one COPIES its values into the routine's
own `routine.yaml` — once, at creation or from the routine page's *Start from a template* action
(`POST /api/routines/{slug}/adopt-template`) — and the link is then gone. Lists union, maps fill
only what the routine left unset, the routine's own value always winning: the domain merge's
rules applied as a WRITE. `grants` is the one shared key adoption never copies; a grant is a settled
decision a person made about one routine, and a template pre-answering one would be a template
exposing a secret.

Layering was tried first (0.262.0) and reversed on the operator's order (2026-08-30). It read
badly for a reason worth keeping written down: a routine's own file recorded only its
DIFFERENCES from its template, so opening `routine.yaml` told you almost nothing about what the
routine could do; the routine page had to explain a second inheritance chain stacked on the
domain's; and `template_except:` existed purely to subtract from a layer nobody could see. The
cost of copying is the leverage — editing a template no longer reaches its adopters — which is
the correct trade for a *starting point*. A live shared config is what a DOMAIN is — the only
live layer there is.

Consequently nothing resolves a template at config load, `RoutineConfig` has no field naming
one, and adopting twice is harmless: the write is a union that never overwrites, so a second
press changes nothing and a second template ADDS to the first.

The shipped six, inferred from what the live routines actually hold rather than invented:

| template | for |
|---|---|
| `basic` | thinks, reads and reports — nothing outside its own directory |
| `watcher` | checks the world on a schedule and records what changed |
| `correspondent` | drafts and sends things to people in your name |
| `steward` | tends one project over weeks and publishes a page you can check |
| `operator` | acts on files, folders and machines you own |
| `maintainer` | maintains this instance — its library, its routines, itself |

Two capabilities are deliberately NOT template defaults, because their blast radius is the
instance itself and they deserve a per-routine decision: `recipe-authoring` (rewriting a
routine's own instructions) and `shell`. Filesystem roots and machine bindings are absent for a
different reason — they name paths and hosts specific to this instance, so they stay per routine.

A template is an ordinary library document: versioned, linted (`lint_template_text`) and
editable on the Library tab. A revision reaches nobody retroactively — adopters copied their
values — so editing one only changes what the NEXT adoption writes.

**A new routine starts from one.** `workflows/scaffold` fits a template to what the creation
flow already decided — `templates.suggest`, a deterministic score over the requested permissions
and rules, not a model call: a wrong guess here writes a wrong DEFAULT into a config file, which
is worse than a slightly-narrow one you widen on the page. The fitted template's values are
written into the new `routine.yaml` in FULL, so the file says what the routine is from its first
line.

### A domain holds the shared half (D82)

A routine that names a **domain** (`domain:` in its own routine.yaml) inherits that domain's
`config:` block — permissions, capabilities, rules, machines, connections, secret grants,
models, budgets and fs roots set once for all its members (`domains.CONFIG_KEYS`). The domain is
a **default, not an override**: list keys union with the member's own, mapping keys merge per
key with the member's value winning, and a key the domain does not set is left entirely to each
member.

A routine has **at most one** domain, so there is exactly zero or one shared layer — which is
what makes the merge answerable at all. Two layers would have to be merged with each other,
which has no answerable rule: combining whatever a membership list turns up under "first
record's value wins the whole key" while unioning within one makes what a routine inherits
depend on the order rows sit in a JSON file. With one layer there is no order to depend on
(docs/lanes-domains.md).

Nothing is copied into routine.yaml — the merge happens at load — so clearing `domain:` returns
the routine to exactly what its own file says. The routine page marks each inherited value with
the domain it came from; edit the shared half once on the domain, then a routine's own file
wherever it must override. Joining or leaving is an ordinary routine config save, user-only like
every other key in that file: no run may write it, so membership lives in exactly one place and
cannot disagree with itself.

### Names gate one util; TAGS gate a class

`utils:` names individual utils. `util_tags:` switches on a whole class — every util whose
docstring `tags:` line carries one of them, including utils the library gains **later**. Both
sides read the same way: a doc's `requires:` declares what its conduct presumes, and the
routine's `capabilities:` is the user's switch.

The distinction is the difference between fail-open and fail-closed. A name list only gates
utils someone remembered to list, so every util the library gains is open by default — as of
2026-08-13 that was 108 of 114. A tag gate closes the class once, and a new util carrying a
gated tag is closed the moment it lands. Every util is required to declare at least one tag
(`utils_lib.header_problems`), so there is no way to slip past by omission.

The util catalog is read at policy load **only when some permission doc declares `util_tags`**;
with none, the policy is identical to the name-only one and no catalog is touched.

## Permissions (conduct docs)

Library docs live in `<libraries_home>/permissions/*.md` — a heading line
`# permission: <name> — <summary>` plus a machine-read `requires:` frontmatter key. The
LIBRARY copy is the only authority for `requires:`; routines keep no local copies. The
`requires:` panel on the Library tab's permission editor is prefilled from the
frontmatter and authoritative for that key on save.

```yaml
---
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util]        # gated action kinds these instructions presume
  utils: [discord]             # reserved utils these instructions presume, BY NAME
  util_tags: [messaging]       # reserved util CLASSES presumed, by docstring tag
  runs: last                   # minimum previous-run depth presumed: last | all
expects:                       # the SOFT edge — presumed, never enforced (see below)
  machine: ["*"]               # entity CLASS → names; "*" = at least one of the class
---
# permission: <name> — <summary>
<a SHORT body: shown in the UI, and appended to the prompt's CAPABILITIES section when held>
```

(No `confirm` in `requires:` — the approval level is your policy, never a doc's demand.)

The shipped set:

| permission | requires | default |
|---|---|---|
| `util-authoring` | `write_util` (the approval level is the capability's setting) | ✅ held by new routines |
| `memory` | `memory_read` / `memory_write` — the `.memory/` notebook | ✅ |
| `messaging-discord` | the reserved `discord` util — post as the user in their Discord | opt-in |
| `run-history` | previous-run reads (the depth — last / all — is the capability's setting) | opt-in |
| `shell` | the `shell` ACTION — one arbitrary host command per turn, in the run's own jail with no secret injected | opt-in |
| `remote-machines` | the reserved `remote` util — act on bound SSH hosts (see [remote-machines](remote-machines.md)) | opt-in |
| `darknet` | the reserved `darknet` util — read Tor hidden services (see [darknet](darknet.md)) | opt-in |
| `usenet` | the reserved `usenet` + `usenet-nzb` utils — read, search and post over NNTP (see [usenet](usenet.md)) | opt-in |
| `workflow-generation` | `workflows: generate` — a subtask may DRAFT a new pattern when none fits | opt-in |
| `background-tasks` | the `detach` action — launch a long job that outlives a reply and reports back | ✅ conversations; opt-in for routines |
| `global-utils` | nothing (`requires: {}`) — the `util` action is a base kind; this doc is pure conduct: discovery, composition, never silently routing around a broken util | ✅ |
| `rule-authoring` | the `write_rule` action — author or revise a general rule in the shared library (the approval level is `rule_confirm`) | opt-in |
| `scheduling` | the `schedule_run` action — arm/cancel a one-shot future run of a routine | opt-in |
| `recipe-authoring` | the `write_recipe` capability — revise this routine's OWN `main.md` / `stages/` / `tuning.yaml`. Hold it where refining the recipe IS the job; `routine.yaml` stays sealed regardless | opt-in |
| `scripts` | the `script` action — run the routine's OWN persistent `scripts/<name>.py` helpers (tooling, not a second interpreter; declared secrets only, no util/model access inside) | opt-in |
| `reminders` | `reminders: local` — the `remind` / `remind_feedback` fields: leave a `(regex → consequence)` caution that HOLDS a matching action before it runs (see [reminders](reminders.md); the shared store and its approval level are `reminders: global` + `remind_confirm`) | ✅ |

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
`action:<gated-kind>`, `util:<reserved-name>`, `secret:<STORE_NAME>`,
`connection:<provider>`, `machine:<name>`, `fs-read:<path>` / `fs-write:<path>`,
`runs:last|all`, `workflows:generate`, `recreate:<deleted-util-slug>`. A run that hits a
gate files an `ask_user` carrying `request: "<entity-id>"` (the question stays its prose —
WHY it needs the entity); the Decisions page renders the typed decision buttons — four
(allow/deny × now/forever) for every class, plus a fifth, **allow once (this action
only)**, offered for the turn-action classes — and each entity is always in exactly one
of four states (a once-grant passes through *allowed now* and back out):

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

- **allowed once** (D65, turn-action classes only: `action:` / `util:` / `runs:` /
  `workflows:`) — an allow-now that the engine REVOKES after exactly one use. The grant
  seeds the same run overlay (so it reaches the same enforcers, and the CAPABILITIES
  line marks it "(one action only)"); the first successfully-DISPATCHED matching action
  spends it — the engine drops it from the overlay and rebuilds the policy at that same
  boundary, appending an `[ONCE-GRANT SPENT: …]` line to the consuming observation so
  the next matching attempt is not an unexplained denial. A schema retry or a validation
  rejection never consumes (it never becomes a turn), and neither does a user gate
  refusing the call pre-execution (a declined write_util, refused secrets) or a bounced
  handler (unknown target, failed read, missing util) — the grant is spent by USE, not
  by attempt. Why only these classes: their use IS a turn action `validate_action`
  observes, so consume-once is exact. `secret:`/`fs-read:`/`fs-write:` are consumed
  inside a util SUBPROCESS the engine never sees as a turn — "once" for them could only
  mean "the next util call that touches it", a coarser promise than the button makes —
  so those classes stay four-state and the button is not offered (the API refuses it).

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
