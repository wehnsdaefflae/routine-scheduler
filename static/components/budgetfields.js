// The budget vocabulary — ONE list feeding every budgets editor (routine page, the
// wizard's setup panel). key → short label → full help line; UNLIMITED_BUDGETS marks
// the -1-means-unlimited ones (their inputs allow -1). Two drifting copies of this
// list once disagreed on the labels; keep it here only.
export const UNLIMITED_BUDGETS = ["max_total_tokens", "max_wall_clock_min", "max_cost",
                                  "max_total_turns"];

export const BUDGET_FIELDS = [
  ["max_turns", "turns per run",
   "each model action is one turn; the run is stopped at the cap"],
  ["max_total_turns", "turns across all resumes",
   "cumulative turns over every resume window (a conversation's whole life); -1 = unlimited (the default — inert for single-window scheduled routines)"],
  ["max_wall_clock_min", "minutes per run",
   "wall-clock ceiling (time waiting on you is credited back); -1 = unlimited"],
  ["max_total_tokens", "tokens per run",
   "cumulative input+output tokens; -1 = unlimited (the default — turns bound the run)"],
  ["max_cost", "cost cap per run ($)",
   "whole-dollar ceiling on real provider spend (reported by metered endpoints like OpenRouter); -1 = unlimited (the default)"],
  ["max_subruns", "sub-workflows per run",
   "how many parallel children a run may spawn in total"],
  ["max_subrun_depth", "sub-workflow depth",
   "how deep children may nest (children get half the parent's remainder)"],
  ["ask_timeout_min", "blocking-question timeout (min)",
   "minutes a blocking decision waits for you before the run continues without it (the question stays open on the Decisions page)"],
];
