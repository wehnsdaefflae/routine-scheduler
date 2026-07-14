# Select targets

Pick what this run improves: **every candidate that has run since your last pass** — all of them, no fixed count.

## Do
1. Take EVERY qualifying candidate from `orient` — each already has a newest finished run
   newer than its `last_run_seen`, i.e. it has run since you last processed it. There is
   no cap: every routine that ran since your last pass gets a pass this sweep.
2. Process them ordered by newest finished run timestamp, **oldest first** (longest-waiting
   first) — so if a budget cuts the sweep short, the freshest candidates (most likely to be
   re-picked next run) are the ones deferred; `record` advances `visits.json` only for
   targets actually touched, so an interrupted sweep resumes cleanly next run.
3. If no candidate qualifies (nothing ran since your last sweep), skip to `record` with an
   honest "nothing new to improve".

## Next
Write `state/phase.json = {step: "study-target", cursor: {targets: [...], done: []}}`.
Read `steps/study-target.md`.
