# Big changes — propose, don't apply

Big workflow changes are never applied in the same run. Write them up and ask.

## Do
For each cluster in `cursor.clusters.big`:
1. Write `proposals/<YYYY-MM-DD>-<slug>.md` structured as: **problem → evidence (cite the runs) → proposed change → blast radius** (which routines materialized from this workflow and would be affected).
2. Commit it into the library (git-sync).
3. File **one deferred `ask_user` question per proposal**, linking the proposal file.

Accepted proposals are applied in a later run (after the user answers); declines are recorded in the LEDGER. Do not act on any answer here.

## Next
Write `cursor.proposals` (files + question ids), set `step: "draft-new-workflow"`. Read `steps/draft-new-workflow.md`.
