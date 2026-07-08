---
materialized_from: {slug: demo-haiku, commit: "", version: 1}
adapted: 2026-07-08
params: {}
---

## Run flow
1. Ensure the working directory is a git repository: run `git rev-parse --git-dir`; if that
   fails, run `git init`.
2. Write the haiku the instruction asks for into `haiku.txt` (write_file, overwrite).
3. Append one line to `LEDGER.md` (write_file with append true):
   `### <run id> — wrote haiku: <first line of the haiku>`
4. Commit: `git add -A && git commit -m "haiku run"`.
5. Finish with status ok; quote the haiku in the summary.

## Phases
- **only** — a single phase; no cross-run progression to track.

## Completion criteria
- `haiku.txt` contains a fresh 3-line haiku and the git commit succeeded.
