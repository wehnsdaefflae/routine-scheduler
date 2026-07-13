# Orient

Build the candidate list: every routine that may be improved this run.

## Do
1. Read `state/visits.json` — a map `{slug: {last_visit: <iso>, last_run_seen: <run ts>}}`.
   Missing/empty means no routine has been visited yet.
2. List `~/routines` with the `dir-tree` util (depth 1). Keep real routine directories only:
   **skip dot-directories** (`.control`, `.ui-traces`, …) and anything without a
   `routine.yaml`.
3. For each candidate, `read_file` its `routine.yaml`:
   - `exclude_from_improvement: true` → drop it (note the skip for the LEDGER — the user
     chose this; never argue with it).
   - otherwise keep `{slug, enabled, description}`. **You are a candidate too** — apply the
     same flag check to yourself, nothing else is special about you.
4. For each kept candidate, list `runs/` (dir-tree, depth 1) and note whether it has runs
   newer than `last_run_seen`.

## Next
Write `state/phase.json = {step: "select-targets", cursor: {candidates: [...]}}`.
Read `steps/select-targets.md`.
