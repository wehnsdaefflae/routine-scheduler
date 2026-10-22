// The budget vocabulary — ONE list feeding every budgets editor (today the routine page's
// limits section, views/routine-config-limits.js). key → short label → full help line;
// UNLIMITED_BUDGETS marks the -1-means-unlimited ones (their inputs allow -1). Two drifting
// copies of this list once disagreed on the labels; keep it here only.
export const UNLIMITED_BUDGETS = ["max_total_tokens", "max_wall_clock_min", "max_cost",
                                  "max_total_turns"];
// …and the ones where 0 is not "no budget" but "DERIVE me": max_total_turns resolves to
// 3x max_turns (src/rsched/engine/turnwindow.py), so 0 has to be enterable here.
export const DERIVED_BUDGETS = ["max_total_turns"];

export const BUDGET_FIELDS = [
  ["max_turns", "turns per run",
   "each model action is one turn; the run is stopped at the cap"],
  ["max_total_turns", "turns across all resumes",
   "the RUN-SPANNING cap: cumulative turns over every resume window (and every reply of a conversation). max_turns bounds ONE window and a resume re-bases it, so this is what bounds a resume chain; 0 = derive it as 3x max_turns (the default), -1 = deliberately unlimited"],
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
