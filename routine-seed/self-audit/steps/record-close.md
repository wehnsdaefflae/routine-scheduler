# Step: record-close

Make the run incremental and finish.

## Do
1. **Advance the anchor** — update `state/audit.json`:
   - `last_commit` = HEAD of `/home/mark/git-repos/routine-scheduler` after this run's commits
     (read it back via the git-log util).
   - `last_ts` = now (iso8601), the new "since" watermark for routine runs.
   - `last_run` = this run id.
2. **Append the LEDGER entry**: findings by severity, edits committed (with hashes), any reverts,
   decisions surfaced, and how each piece of reviewer feedback was reconciled.
3. The engine commits your own routine directory automatically — you don't commit it yourself.
4. **Finish with a short summary**: headline health; findings by severity count; commits made;
   decisions awaiting the user; whether a restart was requested.

## Done
Set `state/phase.json` = `{"state": "orient-baseline"}` so the next run starts a fresh sweep.
This run is complete.
