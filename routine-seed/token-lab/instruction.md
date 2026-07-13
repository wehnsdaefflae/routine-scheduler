Research and empirically evaluate token-saving methods for this routine-scheduler
instance — measurement and experimentation ONLY, never integration.

Goal: a continuously maintained, evidence-backed ranking of the token-saving methods with
the greatest potential for this system, grounded in (a) the instance's own measured usage
and (b) bounded experiments you run yourself.

Each run:
1. Refresh the measured baseline from real run data (tokens in/out/cached per routine,
   per model, per turn; where the spend concentrates; cache hit rates).
2. Advance the research backlog (`state/backlog.md`): new candidate methods from the
   literature, each with source links and a falsifiable claim.
3. Run at least ONE bounded experiment from the backlog via the `llm` action — e.g. A/B
   the same task with two prompt layouts, two catalog sizes, two summary styles — and
   record measured token numbers under `state/experiments/`.
4. Rewrite `artifacts/report.html`: a self-contained, nicely formatted HTML report —
   dated header, the measured baseline, experiment results, and a ranked "greatest
   potential" list where every claim carries a measured number or a cited source.

Hard constraints:
- STRICTLY read-only toward the system: never modify the scheduler code, the library,
  utils used by others, other routines, or any config. Never enable, schedule, or change
  anything. Recommendations go in the report; adoption is the user's and the
  routine-improver's job.
- Experiments live entirely inside this routine's own directory and the `llm` action.
  Cap experiment spend at roughly 30 llm subcalls per run.
- The report must be regenerated whole every run (same filename, `artifacts/report.html`)
  with a short "what changed since last report" section.

Done when: report rewritten, backlog updated, experiment recorded, LEDGER entry appended.
