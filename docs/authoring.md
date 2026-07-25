# Authoring library building blocks

Everything routines are built from lives in **one git-backed library** (Library tab):
workflow **patterns**, **traits**, **permissions**, **playbooks**, and **utils**. This
guide shows how to write each one, with a working example per type. The general shape is
always the same: a small file with a machine-read header and a human-read body, linted on
save, versioned in the library repo.

## Utils — the only way routines run code

A util is a **self-contained PEP 723 Python script** (`utils/<name>/main.py` in the
library) that any routine can call with `util name=<name> args=[…]` — and that you can run
yourself as `gu <name> …`. Routines have no shell; the util catalog *is* their toolbox.

The **docstring header is the only machine-read surface** — the engine, the catalog, and
the Settings page all parse it:

```python
# /// script
# dependencies = []
# ///
"""dir-tree — list a directory tree to a bounded depth (routines have no shell).

usage: gu dir-tree ROOT [--depth N] [--max N] [--all] [--json]
calls: (none)
secrets: (none)
tags: files, listing, meta
net: none

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
    "description": "Orient, do the work in small verified steps, record, commit.",
    "when_to_use": "Most recurring instructions with no more specific pattern…",
    "version": 9,
    "tags": ["general", "research", "tool-use"],
    "includes": ["ask-policy", "global-utils"],   # traits this pattern presumes
    "tools": None,   # or a list restricting action kinds ("finish" is always allowed)
}

PHASES = ["bootstrap", "steady", "wrap-up"]       # the cross-run progression
COMPLETION = "per run: a concrete increment, a LEDGER entry, everything committed"

def main():
    """One run — the top-level control flow, one function per step below."""
```

Rules of the form:

- **`from routine.params import …`** — dummy imports that *name the parameters* the
  clarifier must pin down for a concrete task (type + meaning in the trailing comment).
  They resolve to nothing; they are the pattern's parameter contract.
- **`META`** must be a literal dict; `tools:` restricts which action kinds materialized
  routines may use (how `clarify-instruction` is held to ask/read/write/finish).
- **`PHASES` / `COMPLETION`** are literals; PHASES names the cross-run progression (the
  UI's state graph itself comes from the materialized routine's stage modules — the
  engine tracks the run's live position from its stage-module reads).
- One top-level `main()` whose body is the per-run control flow; one function per step.

`workflows/lint.py` gates every save (the Library editor shows the findings inline). A
routine may also *generate* a pattern mid-run when it holds the `workflows: generate`
capability — drafts land in the same library, subject to the same lint.

## No named utils — a recipe says WHAT, never which tool

A workflow pattern, a materialized recipe (`main.md` + `stages/`), a trait and a playbook all
describe the WORK. None of them may name a util or show a util's flags. They name the CAPABILITY
a step needs — "fetch the page", "run the repo's test suite", "publish the site" — and the run
picks the tool: it is shown the whole util catalog in its CAPABILITIES prompt section (name +
one-line summary, derived live from disk, so it can never be stale) and gets any single util's
exact `usage:` line for one cheap turn via the `util` action with name `list`. Which util worked,
and with which arguments, is then persisted in the ROUTINE'S OWN memory/notes — that, not the
recipe, is where tool knowledge accumulates across runs. A tool named in a recipe goes stale the
day it is renamed or removed, and it pre-empts the discovery that would have found a better one.
There is no exemption for meta or maintenance routines.

`lint.named_utils` enforces it at every gate: `rsched lint` for the library, `rsched validate`
for materialized routine recipes (the only linter that reaches the documents a run actually acts
from), and the save endpoints behind the Library editor. Two carve-outs keep it honest —

- A util name that is also an ordinary word, a service or a protocol (`shell`, `gmail`, `ftp`,
  `vision`, …) is flagged ONLY in an invocation shape, because a recipe may legitimately name the
  service it works with. Coined names (`static-publish`, `codemap`) are flagged anywhere; a util
  added later defaults to coined, which is the safe direction. The set lives in
  `lint.AMBIGUOUS_UTIL_NAMES`, each entry carrying its reason.
- A PATH named after the tool that writes it (`<repo>/.codemap/`,
  `.control/health-events.jsonl`) is a task fact the recipe must state exactly, so a bare name
  preceded by `.` or `/` is not a hit.

`state/` and `.memory/` are deliberately NOT linted: naming the tool that worked is exactly what
a routine's memory is for. The rule also binds recipe GENERATION — `workflows/adapt.py` states it
in the prompt that compiles a workflow + instruction into stage modules, so new routines start
clean rather than being swept later.

## Traits — reusable practice prose

A trait (`traits/<slug>.md`) is conduct prose — *how* to work, never *what* task to do:

```markdown
# trait: ledger-discipline — record what changed and why

After every increment, append one LEDGER.md line: what changed, why, what surprised you…
```

The heading form `# trait: <slug> — <summary>` is lint-enforced; traits carry **no**
`requires:` (they grant nothing). At routine creation the selected traits are **adapted
to the task** and written into the routine's own `traits/` — from then on they are the
routine's files, refined by the routine-improver, never toggled.

## Permissions — conduct docs over enforced capabilities

A permission (`permissions/<slug>.md`) is the *conduct* half of the two-layer permission
model; the enforced half is the routine's `capabilities:` mapping. The frontmatter's
`requires:` names what the doc presumes, which drives the UI cascades:

```markdown
---
tags: [communication, policy, notification]
requires:
  utils: [discord]
---
# permission: communication — Discord as a second decision surface

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

See also: [Getting started](getting-started.md) · [Traits & permissions](traits-permissions.md) · [Playbooks](playbooks.md)
