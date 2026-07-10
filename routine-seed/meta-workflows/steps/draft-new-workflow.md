# New workflow on demand

Only if the evidence shows a **recurring instruction shape with no fitting workflow** (`cursor.clusters.missing_shape`, or your single-workflow review in a no-new-runs sweep).

## Do
1. Draft `workflows/<new-slug>.py` — a Python pattern file with a complete `META` dict (`"status": "draft"`), `PHASES` / `COMPLETION` literals, and a top-level `main()`.
2. Lint it.
3. Commit it (git-sync).

A draft only becomes `stable` after a proposal question is accepted — so if you want it adopted, also file a proposal + deferred question via the pattern in `propose-big-changes` (or note it for the next sweep). Do not promote a draft to stable in this run.

If no missing shape: skip.

## Next
Write `cursor.drafts`, set `step: "record"`. Read `steps/record.md`.
