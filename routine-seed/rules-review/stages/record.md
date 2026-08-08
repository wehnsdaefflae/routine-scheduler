# record — close the pass honestly

1. Update `state/reviewed.json` for every rule you EXAMINED — `last_pass`, the verdict
   (`revised` when you changed the text, `left-alone` otherwise), and `last_change` when an
   edit landed. A rule you selected but ran out of turns on is NOT examined: leave it unstamped,
   or the next pass will skip it on the strength of work you did not do.
2. Append one LEDGER entry: the rules examined, the revisions applied (each with the defect it
   fixed and the runs that showed it), the rules deliberately left alone and why, and every
   problem you routed elsewhere with its `R` id. The left-alone reasons are the part that pays off — they stop the next pass
   re-opening a settled question.
3. Note anything about the rules layer as a whole that no single edit captures: a rule no
   holder has ever read, two rules that overlap, a class of failure no rule covers. A missing
   rule is a real finding, and one you can act on — author it, and say in the summary that it
   binds nobody until the user adds it to a routine.
4. Finish with a summary that leads with what CHANGED, since every holder picks it up at its
   next run, then the deletions you proposed and the approvals still waiting.
