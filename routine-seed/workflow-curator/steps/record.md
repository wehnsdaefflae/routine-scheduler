# Record

Close the sweep and prove every finding was resolved.

## Do
1. Update `state/last_seen.json`: set each swept routine's entry to its newest ingested run ts.
2. Append a **LEDGER.md** entry covering: applied edits (`cursor.applied`), proposals filed (`cursor.proposals`), routine-local deferred questions (`cursor.clusters.routine_local`), drafts (`cursor.drafts`), any rejected/dropped candidates **with a reason**, and prior-proposal declines if the user answered since last run.
3. Verify completion: `last_seen` advanced, and **every finding is either fixed, proposed, or explicitly dropped with a reason** — nothing silently lost.
4. Reset `state/phase.json = {step: "orient", cursor: {}}` for the next sweep.

The engine commits your own directory automatically — you only git-sync the library.

## Finish
End the run with a **findings → actions** summary: what was seen, what was fixed/proposed/drafted/dropped.
