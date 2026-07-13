# Step: analyse-findings

Turn evidence into concrete, evidence-backed findings.

## Do
1. Cluster the gathered signal into discrete items and classify each severity:
   `problem` | `improvement` | `redundancy` | `systemic` | `info`.
   - **systemic** = a class of failure recurring across routines (e.g. the same schema-retry
     storm, the same budget-forced finishes, the same ignored-question pattern in several runs).
2. For every finding, note the **evidence**: run-id(s), file path(s), commit hash, or journal ref.
3. **Unprovable suspicion rule**: if you suspect an issue but the data can't confirm it, do NOT
   drop it — make it a finding whose *fix is the specific logging/telemetry to add* so the next
   audit can see it. That instrumentation is a real code change and goes through
   `act-apply-fixes` like any other (test-gated).
4. Reconcile reviewer **finding** feedback here: tune the wording/severity, close a resolved
   finding, or fold the correction in — and note how you resolved it (for the report + LEDGER).
5. Reuse stable ids from last run's report; assign new ids (F-next) only to genuinely new items.

## Next
Write `state/phase.json` = `{"state": "separate-decisions"}` and read `steps/separate-decisions.md`.
