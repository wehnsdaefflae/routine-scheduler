---
tags: [self-management, record-keeping, history]
requires:
  runs: last
---
# permission: run history — read previous runs

Unlocks `read_file` on previous run directories under `runs/` — transcripts, results,
and archived history files. How far back is the routine's previous-runs capability depth
(user-set): `last` covers only the most recent run; `all` covers every one. The state
digest already carries the last run's finish summary — reach for a raw transcript only
when the summary is not enough, e.g. to recover an exact value, URL, or error message it
compressed away. Full depth is for genuinely longitudinal work: comparing outcomes
across runs, tracing when a regression appeared, auditing what an earlier run actually
did. Transcripts are large: read them in ranges (`start_line`/`max_lines`), never whole,
and lean on each run's `result.md` before opening its transcript.
