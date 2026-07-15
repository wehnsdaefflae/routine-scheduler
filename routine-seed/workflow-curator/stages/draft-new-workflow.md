# New workflow on demand

Only if the evidence shows a **recurring instruction shape with no fitting workflow** (`cursor.clusters.missing_shape`, or your single-workflow review in a no-new-runs sweep).

## Do
1. Draft `workflows/<new-slug>.py` — a Python pattern file with a complete `META` dict, `PHASES` / `COMPLETION` literals, and a top-level `main()`.
2. Lint it (the same lint util / gate as in `apply-small-edits`).
3. Commit it (git-sync).

A committed workflow is immediately in circulation — there is no approval or promotion step. Judge it in LATER sweeps by its usage evidence (workflow-usage.jsonl): revise or delete on what real runs show.

If no missing shape: skip.

## Next
Write `cursor.drafts`, set `step: "record"`. Read `stages/record.md`.
