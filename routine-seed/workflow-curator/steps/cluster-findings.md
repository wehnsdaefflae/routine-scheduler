# Cluster findings by workflow

Decide what is a library defect versus a single routine's problem.

## Do
Group `cursor.findings` by the **workflow slug** named in each run header.

- A finding that shows up across **several routines materialized from the same workflow** → a **workflow defect**. It is fixable in the library.
- A finding **unique to one routine** → belongs in that routine's own instruction/steps. **Never edit a foreign routine.** Instead mark it to be filed as a **deferred question that names the routine**, handled in `record`.

For each workflow-defect cluster, judge the size:
- **small/safe** (ambiguous or contradictory wording, a missing hint several runs stumbled over, a stale reference) → route to `apply-small-edits`.
- **big** (restructuring, phase-model change, retirement) → route to `propose-big-changes`.

## Next
Write `cursor.clusters = {small: [...], big: [...], routine_local: [...], missing_shape: <bool>}` into `state/phase.json`, set `step: "apply-small-edits"`. Read `steps/apply-small-edits.md`.
