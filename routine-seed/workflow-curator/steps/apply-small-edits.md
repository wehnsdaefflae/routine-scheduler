# Small safe edits — apply now

Fix the small/safe workflow defects directly in the library.

## Do
For each cluster in `cursor.clusters.small`:
1. `write_file` the fix to `workflows/<slug>.py` (or the relevant fragment). Keep `META` complete, `PHASES` / `COMPLETION` present, a top-level `main()`, and every `include` resolvable.
2. **Bump `"version"`** in the file's `META`.
3. **Lint-gate**: run the library lint via a util (`util name=list` to find one; if none exists, `write_util` one that runs `uv run --project /home/mark/git-repos/routine-scheduler rsched lint` in a subprocess and reports pass/fail). Do not commit a file that fails lint.
4. Commit + push with the git-sync util, one commit per slug:
   `util git-sync ~/.local/share/routine-scheduler-libraries -m "meta: <slug> v<n> — <one line>"`

Keep each edit minimal and defensible by the evidence — a wording clarification, a hint, a corrected reference. Anything larger is not a small edit; move it to the big list instead.

## Next
Write the list of applied edits into `cursor.applied`, set `step: "apply-big-changes"`. Read `steps/apply-big-changes.md`.
