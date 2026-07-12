---
tags: [self-management, record-keeping, history]
grants:
  runs: all
---
# permission: run history (full) — read all previous runs

Unlocks `read_file` on EVERY previous run's directory under `runs/` — transcripts,
results, and archived history files. For most needs the last run (run-history) or the
LEDGER is enough; full history is for genuinely longitudinal work: comparing outcomes
across runs, tracing when a regression appeared, auditing what an earlier run actually
did. Transcripts are large: read them in ranges (`start_line`/`max_lines`), never whole,
and lean on each run's `result.md` before opening its transcript.
