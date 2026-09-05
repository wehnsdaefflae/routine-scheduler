# Authoring library building blocks

Everything routines are built from lives in **one git-backed library** (Library tab):
workflow **patterns**, **rules**, **permissions**, **playbooks**, and **utils**. This
guide shows how to write each one, with a working example per type. The general shape is
always the same: a small file with a machine-read header and a human-read body, linted on
save, versioned in the library repo.

## Utils — how routines run code

A util is a **self-contained PEP 723 Python script** (`utils/<name>/main.py` in the
library) that any routine can call with `util name=<name> args=[…]` — and that you can run
yourself as `gu <name> …`. The util catalog *is* a routine's toolbox. (The one thing that runs
outside it is the `shell` action — the escape hatch, granted to few routines, meant for the
command you run ONCE. Anything a routine runs twice belongs here, as a util.)

The **docstring header is the only machine-read surface** — the engine, the catalog, and
the Settings page all parse it:

```python
# /// script
# dependencies = []
# ///
"""dir-tree — list a directory tree to a bounded depth.

usage: gu dir-tree ROOT [--depth N] [--max N] [--all] [--json]
calls: (none)
secrets: (none)
tags: files, listing, meta
net: none
fs: roots

The routine-safe replacement for `ls`/`find`: prints each entry as an indented name…
"""
```

Line by line:

- **First line** — `<name> — one-line summary`. This is the catalog entry every routine
  sees in its prompt; make the summary earn its tokens.
- **`usage:`** — the exact CLI invocation. Shown when a routine asks
  `util name=list args=["dir-tree"]`.
- **`calls:`** — sibling utils this one execs via `gu <name>` (`(none)` otherwise). The
  sandbox resolves secrets and network need TRANSITIVELY across this graph — an
  undeclared sibling call means the callee's secrets never reach it.
- **`secrets:`** — the env vars it needs, e.g. `secrets: OPENROUTER_API_KEY`. The engine
  **rejects** a util whose code reads a credential env var it doesn't declare — declared
  secrets are what the Settings page can prompt for, and the ONLY store keys injected
  into the util's environment at run time.
- **`tags:`** — required; the catalog groups and filters on them.
- **`net:`** — required: `outbound` (opens network connections) or `none`. Utils run in a
  filesystem/network sandbox (see [sandboxing](sandboxing.md)); a `none` (or undeclared)
  util gets no TCP at all.

Two gates run before a util reaches the library:

1. `header_problems` — the docstring standard above (missing `tags:`/`net:`, undeclared
   secrets).
2. `--selftest` — every util must implement a **fully offline** selftest; the engine runs
   it before saving. A util that can't prove itself in a sandbox doesn't land.

Whether a routine may write utils at all is its `write_util` capability; the approval
level (`always` / `creations` / `never`) decides when you're asked first. A run proposes,
you approve on the Decisions page, the selftest passes, the util is committed — and is
immediately available to every other routine. One rule sits above all of that: a util the
user **deleted** from the library is never recreated silently — the engine rejects the
write and has the routine ask first ([sandboxing](sandboxing.md) § never recreate).

## Workflow patterns — control flow as a Python file

A pattern (`workflows/<slug>.py`) **depicts** a routine's control flow; it is *never
executed*. It's parsed statically and, at routine creation, `decompose` turns it into the
routine's own `main.md` + `stages/` markdown. Python is the notation because branches,
loops, and error handling read better as code than as prose.

The required pieces (see `general-task.py` in the library for the full example):

```python
"""General task — the sane default workflow.  (docstring = orientation for humans)"""

from routine.params import (
    DELIVERABLE,    # str       — the concrete artifact this routine produces
    SOURCES,        # list[str] — the inputs each run draws from
)

META = {
    "name": "General task",
    "slug": "general-task",
    "description": "Orient, work everything that is due in verified steps, record, commit.",
    "when_to_use": "Most recurring instructions with no more specific pattern…",
    "version": 9,
    "tags": ["general", "research", "tool-use"],
    "includes": ["ask-policy", "decision-record"],  # general rules this pattern presumes
    "tools": None,   # or a list restricting action kinds ("finish" is always allowed)
}

PHASES = ["bootstrap", "steady", "wrap-up"]       # the cross-run progression

def main():
    """One run — the top-level control flow, one function per step below."""
```

Rules of the form:

- **`from routine.params import …`** — dummy imports that *name the parameters* the
  clarifier must pin down for a concrete task (type + meaning in the trailing comment).
  They resolve to nothing; they are the pattern's parameter contract.
- **`META`** must be a literal dict; `tools:` restricts which action kinds materialized
  routines may use. It must COVER the pattern's `from routine.actions import` line:
  `kindsurface.effective_kinds` narrows the schema to `tools:`, so a kind the pattern
  imports but the allowlist excludes is prose describing a channel the run cannot
  emit. `workflows/lint.py` fails on the disagreement.
- **`PHASES`** is a literal naming the cross-run progression (the UI's state graph
  itself comes from the materialized routine's stage modules — the engine tracks the run's
  live position from its stage-module reads). Record it under the `phase` key of
  `state/phase.json`: that is what the composer reads and what scopes a stopping condition
  to a stage, and a routine that invents its own key writes a file matching nothing.
- There is deliberately **no `COMPLETION`** literal. What DONE means is the USER's, and it
  lives in the routine's `state/stopping.json` where they can edit it and where the finish
  gate makes it impossible to ignore. A completion text frozen into main.md is not editable
  from there and could only ever disagree with it. That store answers two questions, and a
  pattern should be clear which one it is about: a `run`-scoped condition bounds ONE run, a
  `goal`-scoped one is the state after which the ROUTINE is finished — and meeting every goal
  condition RETIRES the routine (it stops firing, and the operator is asked to confirm). A
  pattern for a job with an end should say so in its `when_to_use`, since that is what the
  clarify flow ranks on.
- One top-level `main()` whose body is the per-run control flow; one function per step.

`workflows/lint.py` gates every save (the Library editor shows the findings inline). A
routine may also *generate* a pattern mid-run when it holds the `workflows: generate`
capability — drafts land in the same library, subject to the same lint.

## No named utils — a recipe says WHAT, never which tool

A workflow pattern, a materialized recipe (`main.md` + `stages/`), a rule and a playbook all
describe the WORK. None of them may name a util or show a util's flags. They name the CAPABILITY
a step needs — "fetch the page", "run the repo's test suite", "publish the site" — and the run
picks the tool: it is shown the whole util catalog in its CAPABILITIES prompt section (name +
one-line summary, derived live from disk, so it can never be stale) and gets any single util's
exact `usage:` line for one cheap turn via the `util` action with name `list`. Which util worked,
and with which arguments, is then persisted in the ROUTINE'S OWN memory/notes — that, not the
recipe, is where tool knowledge accumulates across runs. A tool named in a recipe goes stale the
day it is renamed or removed, and it pre-empts the discovery that would have found a better one.
There is no exemption for meta or maintenance routines.

The rule is enforced where the prose is WRITTEN, not by a check over the finished file.
`workflows/adapt.py` states it in the prompt that compiles a workflow + instruction into
main.md and the stage modules, and `workflows/generate.py` states it in the prompt that drafts
a new library pattern — so a recipe is born clean instead of being swept afterwards. Both
prompts spell out the forbidden forms rather than describing them, because that is what an
LLM can actually comply with.

A post-hoc linter was tried and removed: nearly every one of these documents is LLM-written,
so a validator over the output leaves the generator producing the same defect forever while
adding a false-positive surface — and, because the util catalog is dynamic (`global-utils-review`
creates and removes utils with no human in the loop), any name-matching check turns unrelated
files red the day a util is named after an ordinary word. Fix the generator; repair existing
recipes once, by hand.

Two things a recipe may legitimately name, whoever is writing it:

- The SERVICE or PROTOCOL the work touches — "the newsletters in the Gmail inbox", "published
  over FTP", "post to the Discord channel". A util that shares that name is a coincidence; what
  is forbidden is an invocation (`gu <name>`, `util name=<name>`, "the `<name>` util", flags).
- A PATH named after the tool that writes it (`<repo>/.codemap/`,
  `.control/health-events.jsonl`) — a task fact the recipe must state exactly.

`state/` and `.memory/` are outside the rule entirely: naming the tool that worked is exactly
what a routine's memory is for.

## Rules — the general rules routines follow

A rule (`rules/<slug>.md` in the library) is principle prose — *how* to work, never *what*
task to do:

```markdown
# rule: decision-record — keep the reasoning the artefacts cannot carry

Read the record before you explore. Append one entry per run: what changed, why, and the
candidates you rejected with the reason…
```

The heading form `# rule: <slug> — <summary>` is lint-enforced, three tags are the minimum,
and rules carry **no** `requires:` (they grant nothing).

A rule is GENERAL by construction and has exactly ONE copy. Routines hold slugs
(`routine.yaml` `rules:`), read the prose on demand with `read_rule`, and apply it to their
own case — so revising the library text reaches every holder at its next run, with no
migration and no per-routine fork to drift. That leverage is also the hazard: write for the
routines you did not have in mind. A rule that needs to name a util, a routine or a file has
stopped being general — that mechanism belongs in a recipe or a permission doc.

Two owners, deliberately split. WHICH rules bind a routine is config, so only the user
changes it (the routine page's *General rules* panel). The TEXT is the library's: the user
edits it on the Library tab, and a routine holding the **rule-authoring** permission may
`write_rule` — under its own approval dial (`rule_confirm`), because a revision lands on every
holder. Deletion is nobody's action: it would silently un-bind every holder, so a rule that
should go is reported and the user removes it.

## Permissions — conduct docs over enforced capabilities

A permission (`permissions/<slug>.md`) is the *conduct* half of the two-layer permission
model; the enforced half is the routine's `capabilities:` mapping. The frontmatter's
`requires:` names what the doc presumes, which drives the UI cascades:

```markdown
---
tags: [communication, messaging, outbound]
requires:
  utils: [discord]
---
# permission: discord messaging — reach a person on Discord

Keep channel messages short; the durable record is always the Decisions page…
```

Bodies are **short** (≤14 lines reach the prompt when held). `requires:` may name
`actions`, `utils`, `runs`, `workflows` — never `confirm` (approval levels are user
policy, not a doc's demand).

## Playbooks — reusable conversation briefs

A playbook (`playbooks/<slug>/MAIN.md` + optional detail files) seeds a **conversation**
with a proven brief — the save-instruction / use-instruction pattern. Front matter is
`slug / title / when / tags / axis / updated` (`axis` = what varies between uses); the
body is `## Parameters` (with `{{named}}` placeholders) + `## Instructions`. You rarely
write one from scratch: finish a conversation that went well and click **Save as
playbook** — the system distils it from the transcript. See the [Playbooks](playbooks.md)
guide.

See also: [Getting started](getting-started.md) · [Rules & permissions](rules-permissions.md) · [Playbooks](playbooks.md)

## Settings templates

A new routine starts from one at creation: the fitted template's values are COPIED into its own `routine.yaml` in full, so the file says what the routine is from its first line. See [rules-permissions](rules-permissions.md#settings-templates--the-named-starting-point) for why a template is a preselection rather than a layer, and for the six shipped templates.
