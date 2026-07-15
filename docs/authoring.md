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

The routine-safe replacement for `ls`/`find`: prints each entry as an indented name…
"""
```

Line by line:

- **First line** — `<name> — one-line summary`. This is the catalog entry every routine
  sees in its prompt; make the summary earn its tokens.
- **`usage:`** — the exact CLI invocation. Shown when a routine asks
  `util name=list args=["dir-tree"]`.
- **`calls:`** — external services the util talks to (`(none)` for offline ones).
- **`secrets:`** — the env vars it needs, e.g. `secrets: OPENROUTER_API_KEY`. The engine
  **rejects** a util whose code reads a credential env var it doesn't declare — declared
  secrets are what the Settings page can prompt for and inject at run time.
- **`tags:`** — required; the catalog groups and filters on them.

Two gates run before a util reaches the library:

1. `header_problems` — the docstring standard above (missing `tags:`, undeclared secrets).
2. `--selftest` — every util must implement a **fully offline** selftest; the engine runs
   it before saving. A util that can't prove itself in a sandbox doesn't land.

Whether a routine may write utils at all is its `write_util` capability; the approval
level (`always` / `creations` / `never`) decides when you're asked first. A run proposes,
you approve on the Decisions page, the selftest passes, the util is committed — and is
immediately available to every other routine.

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

PHASES = ["bootstrap", "steady", "wrap-up"]       # tracked in state/phase.json
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
- **`PHASES` / `COMPLETION`** are literals; phases become the state-graph the UI renders.
- One top-level `main()` whose body is the per-run control flow; one function per step.

`workflows/lint.py` gates every save (the Library editor shows the findings inline). A
routine may also *generate* a pattern mid-run when it holds the `workflows: generate`
capability — drafts land in the same library, subject to the same lint.

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
