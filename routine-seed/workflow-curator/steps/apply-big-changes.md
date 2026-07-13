# Big changes — apply them too, carefully

You need no approval to change the library: the user's levers are EDIT and DELETE on the
Library tab, and every change you make is lint-gated, version-bumped, and committed —
trivially reversible via git. What makes a change BIG is the care it demands, not a gate:

## Do — per big finding
1. Re-read the current workflow file and the clustered evidence. Draft the LEDGER line
   FIRST: problem → evidence (cite runs) → change → blast radius (which routines
   materialized from it — they keep their own recipes, so only FUTURE routines see the
   new pattern).
2. Apply the restructure to `workflows/<slug>.py`: bump `version`, keep `status`,
   lint-check (the `rsched-lint` util), commit with `git-sync`.
3. If the change retires a pattern or shifts what a recurring instruction shape gets,
   say so prominently in the run summary — the user reads it on the dashboard.
4. Genuinely unsure a change serves its users? File a deferred `ask_user` NAMING the
   workflow — a question for guidance, not a gate: continue with what you are sure of.

## Next
Write `cursor.applied_big`, set `step: "draft-new-workflow"`. Read `steps/draft-new-workflow.md`.
