# select-rules — what this pass will actually examine

Take from `state/candidates.json` only the rules with `fresh_runs` above zero, least-recently
reviewed first. Depth beats breadth here: reviewing one rule against six runs produces a
defensible revision, while skimming ten produces plausible wording nobody can trace.

- **Fit the selection to the turns you have.** Two or three rules is a normal pass. If the
  budget only supports one, take one and say so — a half-gathered rule is worse than an
  unexamined one, because it looks reviewed in `state/reviewed.json`.
- **Prefer a rule with holders that disagree.** Two routines reading the same sentence
  differently is the strongest available signal that the sentence is the problem. A rule with
  one holder can still be reviewed, but its evidence is about that routine as much as the text.
- **Skip a rule you revised last pass** unless its holders have run since that revision landed —
  otherwise you are grading your own edit against evidence that predates it.
- **A rule nobody exercised is not a finding.** Do not fabricate a review for coverage. Record
  it as unexamined and move on; a rule no run ever reads is itself worth reporting, but as an
  observation about the rule's usefulness, not as a text problem.

Write the ordered selection into `state/phase.json` as the cursor
(`{"phase": "gather-evidence", "cursor": {"queue": [<slug>…], "current": <slug>}}`) and advance
to **gather-evidence**.
