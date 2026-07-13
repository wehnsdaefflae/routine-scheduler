# Orient

Build the candidate list: every routine with runs you haven't processed yet.

## Do
1. Read `state/visits.json` — a map `{slug: {last_visit: <iso>, last_run_seen: <run ts>}}`.
   Missing/empty means no routine has been visited yet.
2. List `~/routines` with the `dir-tree` util (depth 1). Keep real routine directories only:
   **skip dot-directories** (`.control`, `.ui-traces`, …) and anything without a
   `routine.yaml`.
3. For each candidate, `read_file` its `routine.yaml`:
   - `exclude_from_improvement: true` → drop it (note the skip for the LEDGER — the user
     chose this; never argue with it).
   - **You are a candidate too** — apply the same flag check to yourself, nothing else is
     special about you.
4. For each remaining candidate, list its `runs/` (dir-tree, depth 1) and note its **newest
   finished run timestamp**. A candidate qualifies ONLY if that newest run is newer than
   `last_run_seen[slug]` — i.e. it has run since you last processed it. No new run since
   your last visit → drop it this sweep (nothing new to learn from).

## Next
Write `state/phase.json = {step: "select-targets", cursor: {candidates: [{slug,
newest_run}, ...]}}`. Read `steps/select-targets.md`.
