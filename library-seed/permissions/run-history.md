---
tags: [self-management, record-keeping, history]
grants:
  runs: last
---
# permission: run history — read the previous run

Unlocks `read_file` on the LAST previous run's directory under `runs/` — its
`transcript.jsonl`, `result.md`, and archived `history/` files. The state digest already
carries the last run's finish summary; reach for the raw transcript only when the summary
is not enough — e.g. to recover an exact value, URL, or error message the summary
compressed away. Transcripts are large: read them in ranges (`start_line`/`max_lines`),
never whole. Older runs stay off limits (the run-history-full permission covers those).
