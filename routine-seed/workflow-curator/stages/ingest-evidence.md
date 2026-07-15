# Ingest evidence per routine

Extract findings from the selected runs without blowing up your own context. **Do the reading in sub-workflows, not inline.**

## Do
1. For each routine in `cursor.worklist` with runs, `spawn` one parallel sub-workflow. Its prompt = that routine's transcript paths + the rubric below. One sub-workflow per routine.
2. `wait` with `all: true` for them to finish.
3. Collect each sub-workflow's structured findings back into `cursor.findings` (keep it compact — bullet facts, not raw transcript).

If `cursor.no_new_runs` is true, skip spawning; carry the single chosen workflow forward with no findings.

## Rubric (give this to each sub-workflow)
From each top-level `transcript.jsonl` and the run's `LEDGER.md`, collect:
- **outcome** — finish status; was it authored, or forced by budget exhaustion?
- **schema-retry storms** — repeated malformed-output retries.
- **repeated-action warnings** and **fabrication-guard rejections**.
- **wasted turns** re-deriving something the workflow could have just stated.
- **questions asked** — blocking vs deferred; answered vs ignored.
- **contradictions** — places the run ignored or contradicted its own workflow text.
For every finding, capture the **workflow slug + commit** from the run header so it can be clustered later.

## Next
Write `state/phase.json = {phase: "cluster-findings", cursor: {...cursor, findings}}`. Read `stages/cluster-findings.md`.
