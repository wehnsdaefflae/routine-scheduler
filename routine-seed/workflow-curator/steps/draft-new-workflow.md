# New workflow on demand

Only if the evidence shows a **recurring instruction shape with no fitting workflow** (`cursor.clusters.missing_shape`, or your single-workflow review in a no-new-runs sweep).

## Do
1. Draft `workflows/<new-slug>.py` — a Python pattern file with a complete `META` dict (`"status": "draft"`), `PHASES` / `COMPLETION` literals, and a top-level `main()`.
2. Lint it (the same lint util / gate as in `apply-small-edits`).
3. Commit it (git-sync).

Promote a draft to `stable` only in a LATER sweep, on evidence: at least one routine materialized from it finished ok (workflow-usage.jsonl) and its lint is clean. Note promotion candidates in the LEDGER so the next sweep checks them.

If no missing shape: skip.

## Next
Write `cursor.drafts`, set `step: "record"`. Read `steps/record.md`.
