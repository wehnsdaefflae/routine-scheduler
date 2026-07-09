# Small safe edits — apply now

Fix the small/safe workflow defects directly in the library.

## Do
For each cluster in `cursor.clusters.small`:
1. `write_file` the fix to `workflows/<slug>.md` (or the relevant fragment). Preserve the **three required sections** (`## Run flow`, `## Phases`, `## Completion criteria`) and keep every `include` resolvable.
2. **Bump `version:`** in the file's frontmatter.
3. Lint the workflow; do not commit a file that fails lint.
4. Commit + push with the git-sync util, one commit per slug:
   `util git-sync ~/.local/share/workflow-library -m "meta: <slug> v<n> — <one line>"`

Keep each edit minimal and defensible by the evidence — a wording clarification, a hint, a corrected reference. Anything larger is not a small edit; move it to the big list instead.

## Next
Write the list of applied edits into `cursor.applied`, set `step: "propose-big-changes"`. Read `steps/propose-big-changes.md`.
