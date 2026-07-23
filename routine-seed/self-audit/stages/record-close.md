# Step: record-close

Make the run incremental and finish.

## Do
1. **Advance the anchor** — update `state/audit.json`:
   - `last_commit` = HEAD of `/home/mark/git-repos/routine-scheduler` after this run's commits
     (read it back via the git-log util from gather-evidence; `write_util` one if it is missing).
   - `last_ts` = now (iso8601), the new "since" watermark for routine runs.
   - `last_run` = this run id.
2. **Refresh the codemap if you committed code** (`committed_code=true`):
   `util codemap args=["--repo", "/home/mark/git-repos/routine-scheduler"]` — so the working
   tree carries a map matching HEAD for anything reading it between runs. (The next run
   regenerates at orient regardless; this just keeps the tree honest.)
3. **Append the LEDGER entry**: findings by severity, edits committed (with hashes), any reverts,
   decisions surfaced, and how each piece of reviewer feedback was reconciled.
4. The engine commits your own routine directory automatically — you don't commit it yourself.
5. **Finish with a short summary**: headline health; findings by severity count; commits made;
   decisions awaiting the user; whether a restart was requested.

## Done
Set `state/phase.json` = `{"phase": "orient-baseline"}` so the next run starts a fresh sweep.
This run is complete.
