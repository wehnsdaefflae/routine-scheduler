# Settings patterns

A routine's settings — its schedule and run gate, what it may do, which general rules it
practises, its budgets, roots, models and finish line — are mostly the settings of its KIND of
work. A **settings pattern** is that kind written down once: a library document every routine of
the kind starts from and is read against. The instruction stays the task alone; a pattern is
where the rest of what a routine is comes from.

## What a pattern is

One YAML file per pattern, `<library>/patterns/<slug>.yaml` (`rsched/patterns/store.py`):

```yaml
title: Watcher
summary: Checks its sources for what is new since the last run, verifies it and delivers …
workflow: watch-and-report        # the abstract workflow the pattern is designed around
when: The job is to keep an eye on sources … and report what is new
asks:                             # what creation must ask when the task does not answer it
  - field: run_gate
    question: Where does new material arrive — which mailbox and senders, which feed …?
settings:                         # every governed value (rsched/patterns/fields.py)
  schedule: {friendly: {frequency: daily, time: "07:00"}, catchup: run_once}
  run_gate: {enabled: false, timeout_s: 30, checks: []}
  permissions: [steward-publishing, util-authoring]
  capabilities: {actions: [revise_util, write_util], confirm: creations, reminders: local, …}
  rules: [ask-policy, audit-coverage, decision-record, evidence-discipline, …]
  reminders: []                   # curated reminders listed for this kind of work
  finish_line: {outcomes: [], until: ""}
  budgets: {max_turns: 100, max_wall_clock_min: 60, …}
  keep_runs: 30
  …
```

A pattern is **immutable**: it is created, never overwritten, and deleted only as a whole. A
routine that should differ from its pattern differs in its own file (an OVERRIDE); a new kind
of work is a new pattern. The library ships fourteen, each built on one abstract workflow:

| pattern | workflow | for |
|---|---|---|
| `watcher` | `watch-and-report` | keeping an eye on sources and reporting what is new |
| `radar` | `curate-and-act` | a board of listings to pick from, acted on after sign-off |
| `project-steward` | `steward-a-project` | a project over weeks: feedback, backlog, a published page |
| `personal-steward` | `steward-a-project` | a personal project steered through a page |
| `mailbox-service` | `answer-a-mailbox` | classifying and answering one mailbox |
| `daily-operator` | `run-daily-operations` | the day's outward operations, verified and logged |
| `code-maintainer` | `maintain-a-codebase` | sweeping, fixing and shipping a codebase |
| `instance-builder` | `maintain-a-codebase` | building the scheduler's own decided backlog |
| `instance-auditor` | `audit-and-propose` | measuring one side of the instance, handing decisions on |
| `library-curator` | `curate-a-library` | revising the shared library from how runs used it |
| `model-trainer` | `train-a-model` | training a model over many runs on a shared GPU |
| `queue-worker` | `drain-a-work-queue` | draining a queue of files or items through a worker |
| `mirror-sync` | `sync-a-mirror` | keeping a copy in step with its source |
| `one-job` | `do-one-job` | one dated piece of work, then done |

## A routine follows a pattern — it does not inherit from one

`routine.yaml` names it (`pattern: watcher`) and holds **every value itself**: the pattern's
values were copied in when the routine was created, or when it started following the pattern.
Nothing is layered at run time; the file says what the routine IS. The pattern is the
reference the routine page reads it against:

- an **override** is a governed field whose value differs from the pattern's
  (`fields.overrides`); the page marks each one. A routine in a scheduled lane runs on the
  lane's clock, so its own schedule is neither an override nor compared.
- **Identity** fields are the routine's alone and never governed: name, description, the
  Steward hub tab, a webhook trigger's token. Tags are the routine's too, though a pattern may
  carry a starting set.

`GET /api/routines/{slug}/settings` returns the routine's values (`fields`), the field
vocabulary (`meta`), its pattern, the overrides, the pending changes (`draft`) and which
patterns already carry exactly these values (`identical`).

## Pending changes and the one accept

Nothing proposes a change by writing it. A proposal — from creation, from "recommend for this
routine", from following another pattern, from the one-shot migration — is a DRAFT
(`.control/settings-drafts/<slug>.json`, `rsched/patterns/drafts.py`): per field, the proposed
value and one sentence of reason, under a message the page leads with ("check the changes i
recommend."). The page shows each pending value in place, highlighted against the saved one;
the person keeps or drops each, edits any other value, and presses ONE **accept changes**
(`POST /api/routines/{slug}/settings`, `{changes, pattern?}`). Every kept change lands in one
validated write — the routine.yaml fields through the PATCH writer, the triggers and the finish
line through their own owners — and the draft is cleared. `DELETE …/settings/draft` discards
the whole proposal. Accepting is refused while a run is active: a live run keeps the
configuration it booted with.

A proposed value is validated before it is offered, with the same validators the accept uses —
a proposal the accept button would refuse is not a proposal.

## Where patterns meet a routine

**Creation.** The creation conversation settles the task, then the pattern: the draft
observation lists the patterns built on the chosen workflow with the questions each asks
(`asks`); every question the task does not already answer is put to the person as a
decision. Their answers ride `create_routine` as `setup`; what one finished run leaves behind
rides as `done_when`, when the routine is done for good as `finish_line`, what a run must never
do as `never`. The routine is saved with its pattern's values; what is specific to it — the
setup answers turned into settings (folders, a mailbox watch list for the gate, a cadence), the
finish line, whatever else the recommender finds — waits as pending changes under "check the
changes i recommend." (`patterns/recommend.at_creation`). So does the pattern's own `grants`:
an ask-first field is never saved without the person's click, because a pattern deciding which
secrets a routine receives would be a pattern granting them.

**Recommend for this routine** (`POST …/settings/recommend`). The system model picks the pattern
that fits the routine as it now is — the one built on its workflow when there is exactly one —
and proposes what this routine needs beyond it; switching patterns proposes the new pattern's
values as well. A proposal, never a write.

**Follow another pattern** (`POST …/settings/follow`, `{pattern}`). Its governed values that
differ from the routine's become pending changes; accepting them with `pattern` set makes the
routine follow it.

**Save as new pattern** (`POST …/patterns`, `{title, summary, when?, workflow?, fields?}`).
Offered exactly when the routine's values match no existing pattern: they become a new library
pattern (committed to the library repo) and the routine follows it. Refused, naming the
pattern, when one already carries exactly these values.

**Deleting a pattern** (`DELETE /api/patterns/{slug}`). Its followers keep every value they
hold — those were always their own — and stop naming it, in the same operation. The Library tab
lists every pattern with its followers (`GET /api/patterns`).

## What a pattern carries that is easy to miss

- **A run gate.** A pattern for a kind of work that often has nothing to do carries the checks
  that tell (`docs/run-gates.md`); its `asks` collect the specifics (which mailbox, which feed).
- **Shared reminders.** `reminders` lists the curated reminders that belong to its kind of work
  (reach `listed`, `docs/reminders.md`); a follower's `shared_reminders` holds them.
- **A finish line**, when the kind of work ends — usually empty, with an `asks` entry, because
  the finish line is the routine's own.
- **The rules of its kind** on top of the defaults every routine holds.

## The library

`rsched lint` checks every pattern (`workflows/lint.lint_patterns`): a sound document naming
only rules, permissions and a workflow the library holds — a pattern pointing at a deleted rule
would hand every follower a binding to nothing — and no instance credential store among its
folder grants. Creation copies a pattern's roots into the new routine past the guard the routine
page applies, so such a root is never written there either, and "Save as new pattern" refuses a
routine's values that hold one. A file in `patterns/` that is no pattern at all
(it does not parse, is not a mapping, or is not named by a slug) is reported there and passed
over by every page and flow that lists patterns, so one broken file never takes them down.
Patterns ride the library repo's sync like
rules and permissions; the seed sync only adds a pattern a library lacks, never overwrites one.
