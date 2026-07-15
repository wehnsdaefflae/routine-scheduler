# record-close — leave the trail, finish

1. Append the LEDGER entry (consult `traits/ledger-discipline.md`): what was measured,
   what was tested, the verdict, backlog movements, and candidates REJECTED with why.
2. Reset `state/phase.json` to `{"phase": "orient"}` so the next run starts clean.
3. Finish with an authored summary (8-20 lines): the baseline's headline numbers, the
   experiment + verdict, the current top-3 greatest-potential methods, and what the next
   run should pick up. The summary is the next run's orientation — write it for that
   reader.

Never modify anything outside this routine's directory — not the scheduler code, the
library, utils others use, other routines, or any config — and never enable, schedule, or
change anything in the system. You observe and test; you never integrate. Recommendations
go in the report; adoption is the user's and the routine-improver's job. If a finding
demands a system change, the report and a deferred `ask_user` are the ONLY channels.
