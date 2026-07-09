---
name: Meta — maintain the workflow library
slug: meta-workflows
description: Ingests the top-level transcripts and LEDGERs of all routines, finds flaws and optimization potential, revises library workflows (small edits directly, big changes as proposals), and drafts new workflows for recurring unmet shapes.
when_to_use: >
  Internal: the standing meta routine that maintains ~/.local/share/workflow-library. Not a
  template for user routines. Requires fs_read_roots over the routines home and
  fs_read/write_roots over the library (it commits via the git-sync util).
version: 3
status: stable
tags: [meta, maintenance]
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
   sub-workflows for bulk reading — one per routine, prompt = the transcript paths + the
   rubric below, then `wait` with all: true — so your own context stays small). From each top-level `transcript.jsonl` and
   LEDGER.md, collect:
   - outcome (finish status; authored or forced by budget?)
   - schema-retry storms, repeated-action warnings, fabrication-guard rejections
   - turns wasted re-deriving things the workflow could have stated
   - questions asked (blocking vs deferred; answered vs ignored)
   - places the run contradicted or ignored its workflow text
3. **Cluster findings by workflow** (the run header names the workflow slug + commit).
   A finding shared by several routines on the same workflow is a workflow defect; a finding
   unique to one routine belongs in that routine's own instruction/playbook (file it as a
   deferred question naming the routine, don't edit foreign routines).
4. **Small safe edits — apply now.** Ambiguous or contradictory wording, a missing hint that
   several runs stumbled over, a stale reference: edit `workflows/<slug>.md` (or a fragment)
   in the library with write_file (keep the three required sections and resolvable includes;
   bump `version:` in its frontmatter), then commit + push it with the git-sync util:
   `util git-sync ~/.local/share/workflow-library -m "meta: <slug> v<n> — <one line>"`.
5. **Big changes — propose, don't apply.** Restructuring a workflow, changing its phase
   model, retiring one: write `proposals/<date>-<slug>.md` in the library (problem →
   evidence → proposed change → blast radius: which routines materialized from it), commit
   it, and file ONE deferred ask_user question per proposal linking the file. Apply accepted
   proposals in the run after the user answers; record declines in the LEDGER.
6. **New workflow on demand.** If the evidence shows a recurring instruction shape without a
   fitting workflow, draft `workflows/<new-slug>.md` with `status: draft` frontmatter, lint
   it, commit. Drafts become `stable` only after a proposal question is accepted.
7. **Record.** Update `state/last_seen.json`; append the LEDGER entry (edits, proposals,
   rejected candidates). The engine commits your own directory automatically. Finish with a
   summary of findings → actions.

## Phases
- **steady** — every run is the same sweep; no cross-run milestones.

## Completion criteria
- Per run: last_seen advanced, every finding either fixed (committed), proposed (file +
  question), or explicitly dropped with a reason in the LEDGER.
