---
name: Meta — maintain the workflow library
slug: meta-workflows
description: Ingests the top-level transcripts and LEDGERs of all routines, finds flaws and optimization potential, revises library RECIPES (small edits directly, big changes as proposals), and drafts new recipes for recurring unmet shapes. Recipes are birth templates — editing one shapes future routines, not live ones.
when_to_use: >
  Internal: the standing meta routine that maintains the recipe library at
  ~/.local/share/workflow-library. Not a template for user routines. Requires fs_read_roots over
  the routines home and fs_read/write_roots over the library (it commits via the git-sync util).
version: 4
status: stable
tags: [meta, maintenance]
modules: []
params: []
default_budgets: {max_turns: 80, max_wall_clock_min: 60}
requires: {schema_output: false}
includes: [ask-policy, ledger-discipline, hygiene]
---

## Run flow
1. **Orient.** Read `state/last_seen.json` ({routine: last run ts analyzed}). List routines
   (`ls ~/routines`, skip dot-dirs and yourself) and each one's `runs/` for run dirs newer
   than last seen. No new runs anywhere → note it, still do step 6 on one workflow, finish.
2. **Ingest evidence per routine** (cap: the ~5 newest unseen runs each; `spawn` parallel
   sub-routines for bulk reading — one per routine, prompt = the transcript paths + the
   rubric below, then `wait` with all: true — so your own context stays small). From each top-level `transcript.jsonl` and
   LEDGER.md, collect:
   - outcome (finish status; authored or forced by budget?)
   - schema-retry storms, repeated-action warnings, fabrication-guard rejections
   - turns wasted re-deriving things the workflow could have stated
   - questions asked (blocking vs deferred; answered vs ignored)
   - places the run contradicted or ignored its workflow text
3. **Cluster findings by recipe** (the run header names the recipe slug + commit).
   A finding shared by several routines on the same recipe is a recipe defect; a finding
   unique to one routine belongs in that routine's own instruction/steps (file it as a
   deferred question naming the routine, don't edit foreign routines). **Note:** routines are
   SELF-CONTAINED now — each was materialized from a recipe at birth and owns its own main.md,
   so a recipe edit shapes only FUTURE routines, not the live ones (they evolve themselves).
   Your leverage is the recipe *library* — the birth templates.
4. **Small safe edits — apply now.** A recipe is a directory `workflows/<slug>/` with `main.md`
   (the entry, frontmatter + run flow) and optional `steps/<module>.md` modules listed in
   `modules:`. For ambiguous wording, a missing hint, a stale reference: edit the right file
   (`workflows/<slug>/main.md` or a `steps/<module>.md`, or a fragment) with write_file — keep
   the two required sections (`## Run flow`, `## Completion criteria`), resolvable `includes`,
   and every declared module resolving to a step file; bump `version:` in main.md's frontmatter.
   Then commit + push: `util git-sync ~/.local/share/workflow-library -m "meta: <slug> v<n> — <one line>"`.
5. **Big changes — propose, don't apply.** Restructuring a recipe, splitting/merging its
   modules, retiring one: write `proposals/<date>-<slug>.md` in the library (problem → evidence
   → proposed change → who it affects: only routines generated AFTER this lands), commit it, and
   file ONE deferred ask_user per proposal linking the file. Apply accepted proposals next run.
6. **New recipe on demand.** If the evidence shows a recurring instruction shape without a fitting
   recipe, draft `workflows/<new-slug>/main.md` (a recipe DIRECTORY) with `status: draft` and
   `modules: []` — add `steps/<module>.md` files and list them in `modules:` only if the recipe
   has distinct states. Lint it, commit. Drafts become `stable` only after a proposal is accepted.
7. **Record.** Update `state/last_seen.json`; append the LEDGER entry (edits, proposals,
   rejected candidates). The engine commits your own directory automatically. Finish with a
   summary of findings → actions.

## Phases
- **steady** — every run is the same sweep; no cross-run milestones.

## Completion criteria
- Per run: last_seen advanced, every finding either fixed (committed), proposed (file +
  question), or explicitly dropped with a reason in the LEDGER.
