# Traits & permissions

A routine's cross-cutting behavior is split into two sets with deliberately different
ownership:

- **Traits** — reusable *practice prose* (when to ask the user, research discipline, the
  after-run improvement passes). Selected at creation, **adapted to the routine's task**,
  and copied into the routine's own `traits/` directory. From that moment they are the
  routine's files: referenced from the end of its `main.md` (the *Standing practices*
  section), read on demand during runs, and refined by the routine itself as it learns.
  There is no toggle afterwards — changing a practice means editing the routine's files,
  like any other part of its recipe.
- **Permissions** — engine-enforced *capabilities* (writing utils, the Discord channel,
  the `.memory/` notebook, reading previous runs, rewriting its own recipe, the shell
  escape hatch). Held via `routine.yaml`'s `permissions:` list, changed **only by you**
  (the routine page's Permissions panel; the web layer blocks edits while a run is
  active), and enforced when every single action is interpreted. A routine can never
  grant itself anything.

One sentence each: **traits shape how a routine works; permissions bound what it may do.**

## Why the split

Practice prose wants to *live with the routine*: adapted to the task at creation, then
improved by the improvement passes as the routine discovers what works. Permissions want
the opposite: they must be tamper-proof against the very self-modification the traits
encourage. Fusing both into one mechanism (the old "fragments") meant the permission
surface was tangled with editable prose. Now the prose is fully self-owned and the
enforcement is fully user-owned.

## Traits

Library templates live in `<libraries_home>/traits/*.md` — a heading line
`# trait: <name> — <summary>`, `tags:` frontmatter, **no grants** (a trait carrying one
is a lint error). The shipped set:

| trait | what it teaches |
|---|---|
| `ask-policy` | when and how to involve the user: self-sufficiency by default, deferred asks, batching, self-contained questions |
| `global-utils` | util discovery (`util name=list`), composing utils, never silently working around a broken one |
| `web-research` | verify external facts by searching instead of recalling; provenance discipline |
| `ledger-discipline` | the append-only LEDGER entry every run writes, and its rotation |

Improvement passes are deliberately NOT traits anymore: the bundled **routine-improver**
meta routine sweeps every routine that doesn't set `improve: false` in its
routine.yaml (an include-by-default toggle on the routine page) and runs the five lenses — bugfix, research,
features, UI, efficiency — plus a fresh-eyes de-clutter pass on each, itself included.

At creation the wizard **preselects** traits from the refined instruction + chosen
workflow (editable before creating), and the generator LLM **adapts** each selected trait
to the task while it decomposes the workflow — concrete wording, task examples, inapplicable
rules cut. The adapted copies land in `<routine>/traits/<slug>.md` and `main.md` ends with
a *Standing practices* section referencing each one ("read it before the situation it
governs"). The prompt never inlines them — the state digest lists the files and the run
reads what it needs, which keeps every turn's prompt lean.

Trait files (like the whole recipe and routine.yaml) are read-only to the owning run —
not a permission but a fixed engine rule. The one unlock is a user-granted fs_write_root
covering the routine's dir, which is exactly how the routine-improver meta routine
refines every recipe centrally (conversations included).

## Permissions

Library docs live in `<libraries_home>/permissions/*.md` — a heading line
`# permission: <name> — <summary>` plus a machine-read `grants:` frontmatter key. The
LIBRARY copy is the only authority: routines keep no local copies, and nothing under a
routine directory is ever consulted for grants.

```yaml
---
tags: [tool-use, utils, authoring]
grants:
  actions: [write_util]        # gated action kinds this permission unlocks
  utils: [discord]             # utils reserved for holders of this permission
  confirm: true                # write_util approval: true | false | revisions-only
  runs: last                   # previous-run read access: last | all
---
# permission: <name> — <summary>
<a SHORT body: shown in the UI, and appended to the prompt's CAPABILITIES section when held>
```

The shipped set:

| permission | grants | default |
|---|---|---|
| `util-authoring` | `write_util`, every change user-approved | ✅ held by new routines |
| `util-authoring-autonomous` | `write_util`, revisions auto-approved after selftest, creations ask | opt-in |
| `util-authoring-full-auto` | `write_util`, fully autonomous (selftest-gated, committed) | opt-in |
| `memory` | `memory_read` / `memory_write` — the `.memory/` notebook | ✅ |
| `communication` | the reserved `discord` util — a second decision surface | opt-in |
| `run-history` | read the LAST previous run under `runs/` | opt-in |
| `run-history-full` | read ALL previous runs | opt-in |
| `shell` | the reserved `shell` util — arbitrary host commands | opt-in |

### What enforcement looks like

A run's allowed action kinds are **workflow `tools:` ∩ (base ∪ union of held grants)**
(`finish` always allowed). Gated calls — `write_util` without util-authoring, a reserved
util, a `read_file` into `runs/` without run-history, and any `write_file` into the run's
OWN `main.md` / `steps/` / `traits/` / `instruction.md` / `routine.yaml` (a fixed rule,
not a permission — unlocked only when a user-granted fs_write_root covers the routine
dir, the routine-improver's case) — are rejected inside the schema-retry cycle by
`validate_action`, with an error naming the way out (a permission the user could grant,
or a deferred `ask_user`). A rejected call never becomes a turn. The current run's own `runs/<ts>/` tree
(status, archived history) stays readable regardless — the engine itself points the model
there after compaction. `runs/` is never writable.

The model sees its permissions in the prompt's machine-facing **CAPABILITIES** section —
the held slugs, what they unlock, and each one's short capability note. Permission prose
never appears in the natural-language part of the prompt; that is what traits are for.

Sub-workflows (`spawn`) run with permissions off: no grants, no reserved utils, no recipe
writes, no traits of their own.

Budgets, `fs_read_roots` / `fs_write_roots` and schedules are resources, not permissions —
they stay plain `routine.yaml` config.

## Working with them

- **See** a routine's capabilities: the routine page's Permissions panel (each entry shows
  its `▸ grants …` line); its practices: the *Practice modules (traits)* file list.
- **Change** capabilities: toggle permissions on the routine page (takes effect next run).
  Change practices: edit the routine's `traits/*.md` files — or let its improve passes do it.
- **Create** a new granted capability: add a permission doc (Library tab → Permissions) with
  a `grants:` block. To reserve a util for a subset of routines, name it in a permission's
  `utils:` list — every routine without that permission loses access at its next run.
- Any future permission-ish lever becomes a `grants:` key on a permission doc, not a new
  yaml key.
