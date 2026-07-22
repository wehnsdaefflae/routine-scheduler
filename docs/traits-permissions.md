# Traits, permissions & capabilities

A routine's cross-cutting behavior is split into sets with deliberately different
ownership:

- **Traits** — reusable *practice prose* (when to ask the user, research discipline).
  Selected at creation, **adapted to the routine's task**, and copied into the routine's
  own `traits/` directory. From that moment they are the routine's files: referenced from
  the end of its `main.md` (the *Standing practices* section), read on demand during
  runs, and refined by the routine itself as it learns. There is no toggle afterwards —
  changing a practice means editing the routine's files, like any other part of its recipe.
- **Capabilities** — the atomic, engine-enforced surface: gated action kinds
  (`write_util`, `memory_read`, `memory_write`), reserved utils (`discord`, `shell`), the
  write_util approval level, and the previous-run read depth. Held via `routine.yaml`'s
  `capabilities:` mapping, changed **only by you** (the routine page's panel; the web
  layer blocks edits while a run is active), and enforced when every single action is
  interpreted. A routine can never grant itself anything.
- **Permissions** — *conduct docs*: library prose stating HOW to use a capability well.
  Held via `routine.yaml`'s `permissions:` list; a held doc's short body reaches the
  prompt's CAPABILITIES section. A permission's frontmatter `requires:` names the
  capabilities its instructions presume — it grants nothing itself.

One sentence each: **traits shape how a routine works; capabilities bound what it may
do; permissions instruct it in what it may do.**

## The two permission layers and their cascade

Activating a permission switches on the capabilities its `requires:` names. Switching a
capability off deactivates every permission that requires it. Both cascades live in the
UI (the routine page shows the two layers side by side, each capability badged with the
docs requiring it); the server re-applies the activation cascade on save, so the
invariant — *a held doc's requirements are always on* — holds regardless of the client.
A capability may also be enabled bare, without any conduct doc: that is your call to
make, fully visible in the panel.

Enforcement reads **capabilities only** (`grants.py` builds the run policy from the
routine's own mapping); a doc-without-capability misconfiguration therefore fails
closed. Which utils are reservable at all is library-defined (the union of every doc's
`requires.utils`); which action kinds are gateable is engine-defined (`GATED_KINDS`) — a
library edit can reserve a new util, but can never retract a base action kind from every
routine.

## Why the split

Practice prose wants to *live with the routine*: adapted to the task at creation, then
improved as the routine discovers what works. Enforcement wants the opposite: it must be
tamper-proof against the very self-modification the traits encourage. And conduct prose
for a capability wants to be *toggleable with it* without conflating the two: the old
model (permission docs whose `grants:` both unlocked and instructed) meant you could
never enable a capability without one specific prose bundle, and every policy variant
needed its own doc (three util-authoring docs existed only to carry three approval
levels). Now the prose is a doc, the switch is config, and the approval level is a
per-routine setting.

## Traits

Library templates live in `<libraries_home>/traits/*.md` — a heading line
`# trait: <name> — <summary>`, `tags:` frontmatter, **no requires** (a trait carrying
one is a lint error). The shipped set:

| trait | what it teaches |
|---|---|
| `ask-policy` | when and how to involve the user: self-sufficiency by default, deferred asks, batching, self-contained questions |
| `global-utils` | util discovery (`util name=list`), composing utils, never silently working around a broken one |
| `web-research` | verify external facts by searching instead of recalling; provenance discipline |
| `ledger-discipline` | the append-only LEDGER entry every run writes, and its rotation |
| `git-checkpoint` | undo points for external project repos (and conversation dirs) the run edits — a checkpoint commit before risky edits and one after, named in the reply; never pushes unless asked |
| `evidence-discipline` | every reported claim traced to an observation from this run; verified-or-not as a binary, never a confidence score; failure reported as failure |
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

The first four are the routine `DEFAULT_TRAITS`. `git-checkpoint` is **not** a routine default —
the wizard preselects it for repo-editing tasks, and it is a standing default for **conversations**
(see the Conversations guide).

The eleven below `git-checkpoint` are the **curated practice set** — distilled from Anthropic's
prompt-engineering guidance, the Claude Code plugins (their skills and prompt-snippet references as
well as the output-style hooks), OpenAI's agent prompting guide, and the self-correction and
verification literature (see the reasoning notes in
[`docs/curated-traits.md`](curated-traits.md)). None is a default: each is opt-in per routine, and
a trait that is off contributes nothing at all — the whole point of keeping practice prose in
selectable modules rather than one always-on block. Deliberately **not** included, because the
evidence is against them or the harness already covers them: "double-check your own work" (unaided
self-correction breaks about as many correct answers as it fixes — hence `independent-verification`
instead), "don't be sycophantic" (measured as the least effective mitigation tested), numeric
confidence scores (verbalized confidence is systematically overconfident), and parallel tool calls
(architecturally impossible under one action per turn).

Improvement passes are deliberately NOT traits anymore: the bundled **routine-improver**
meta routine sweeps every routine that doesn't set `improve: false` in its
routine.yaml (an include-by-default toggle on the routine page) and runs the five lenses — bugfix, research,
features, UI, efficiency — plus a fresh-eyes de-clutter pass on each, itself included.

### Changing the set after creation

The `traits/` directory IS the state; main.md's Standing practices tail is a derived index
rebuilt from it on every change (`rsched/traits.py` — the one place that convergence lives).
The **user** adds or removes a module at any time from the routine page's *Practice modules*
panel or the conversation header (`POST /routines/{slug}/traits`, `POST
/conversations/{slug}/traits` — one shared implementation). A later add copies the library
text **verbatim**: only creation adapts, and an LLM round-trip between flipping a switch and
the module taking effect would not be worth it for a set written to be generally applicable.

Unlike other routine file edits this is **not** 409-guarded during a run — a run may never
write its own `traits/`, so the web layer is the only writer there and no race exists. An
addition even reaches a run already in flight: the composed prompt is immutable (caching
contract), so `control.json` `add_traits` makes the engine append the prose as an engine note
at the next turn boundary. A removal takes effect at the next run — prose already in a live
context cannot be unsaid.

A **run** never changes its own set. With the `practice-library` permission it may
`read_trait` — consult one module from the library for the current run only (`name: "list"`
for the catalog, entries flagged when already held). Nothing is written, so the recipe stays
the user's; a module that keeps proving necessary belongs in the run's finish summary or a
deferred `ask_user`. Default-on for conversations, opt-in for routines.

At creation the wizard **preselects** traits from the refined instruction + chosen
workflow (editable before creating), and the generator LLM **adapts** each selected trait
to the task while it decomposes the workflow — concrete wording, task examples, inapplicable
rules cut. The adapted copies land in `<routine>/traits/<slug>.md` and `main.md` ends with
a *Standing practices* section referencing each one ("read it before the situation it
governs"). The prompt never inlines them — the state digest lists the files and the run
reads what it needs, which keeps every turn's prompt lean.

Trait files (like the whole recipe and routine.yaml) are read-only to the owning run —
not a capability but a fixed engine rule. The one unlock is a user-granted fs_write_root
covering the routine's dir, which is exactly how the routine-improver meta routine
refines every recipe centrally (conversations included).

## Capabilities

`routine.yaml`:

```yaml
capabilities:
  actions: [write_util, memory_read, memory_write]  # gated action kinds switched on
  utils: [discord]              # reserved utils switched on
  confirm: always               # write_util approval: always | creations | never
  runs: none                    # previous-run read depth: none | last | all
  workflows: catalog            # subtask pattern sourcing: catalog | generate
```

A new routine's default: `write_util` (confirm `always`) + the memory pair, no reserved
utils, no run history — matching the default permission set below.

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
  utils: [discord]             # reserved utils these instructions presume
  runs: last                   # minimum previous-run depth presumed: last | all
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
| `communication` | the reserved `discord` util — a second decision surface | opt-in |
| `run-history` | previous-run reads (the depth — last / all — is the capability's setting) | opt-in |
| `shell` | the reserved `shell` util — arbitrary host commands | opt-in |
| `remote-machines` | the reserved `remote` util — act on bound SSH hosts (see [remote-machines](remote-machines.md)) | opt-in |
| `workflow-generation` | `workflows: generate` — a subtask may DRAFT a new pattern when none fits | opt-in |
| `background-tasks` | the `detach` action — launch a long job that outlives a reply and reports back | ✅ conversations; opt-in for routines |

### What enforcement looks like

A run's allowed action kinds are **workflow `tools:` ∩ (base ∪ enabled capabilities)**
(`finish` always allowed). Gated calls — `write_util` switched off, a reserved
util, a `read_file` into `runs/` beyond the enabled depth, and any `write_file` into the run's
OWN recipe — `main.md` / `stages/` / `traits/` (a fixed rule, not a capability — unlocked only
when a user-granted fs_write_root covers the routine dir, the routine-improver's case) — are
rejected inside the schema-retry cycle by `validate_action`, with an error naming the way out
(the covering permission the user could activate, or a deferred `ask_user`). A run NEVER writes
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
that is what traits are for.

Sub-workflows (`spawn`) run with permissions and capabilities off: no gated kinds, no
reserved utils, no recipe writes, no traits of their own.

Budgets, `fs_read_roots` / `fs_write_roots` and schedules are resources, not capabilities —
they stay plain `routine.yaml` config.

## Working with them

- **See** a routine's surface: the routine page's *Permissions & capabilities* panel —
  conduct docs left (each with its `▸ needs …` line), capabilities right (each badged
  with the held docs requiring it). Its practices: the *Practice modules (traits)* file list.
- **Change** either layer there (takes effect next run; the cascades keep them
  consistent). Change practices: edit the routine's `traits/*.md` files — or let the
  routine-improver do it.
- **Create** a new conduct doc: Library tab → Permissions — the `requires:` panel is
  editable and prefilled. To reserve a util for a subset of routines, name it in a doc's
  `requires.utils` — it becomes a capability every routine must have switched on to call.
- Any future permission-ish lever becomes a capability (a `capabilities:` key +
  a `requires:` entry on the covering doc), not a new yaml key.
