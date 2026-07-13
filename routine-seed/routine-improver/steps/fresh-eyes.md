# Fresh eyes — the de-clutter pass

Recipes rot by accretion: each revision made sense at the time, and the sum is a maze.
Incremental eyes — the target's own, and yours after studying it — learn to ignore that
accretion. This pass is the antidote. Read the target's recipe (`instruction.md`,
`main.md`, every `steps/` and `traits/` file, the `state/` inventory) **as if you had
never seen it or its history**, and hunt specifically for what accumulated:

- **Contradictions between eras** — a practice added in one revision that fights an older
  one; two steps that give different answers to the same question.
- **Dead weight** — steps no transcript ever routes to; state keys nothing reads; trait
  advice about tools or paths that no longer exist; references to removed features.
- **History narrated as instructions** — "previously…", "as of the last change…", counts
  and dates that were true once. Files must describe the current design; history lives in
  the LEDGER and git.
- **Duplication** — the same rule stated in three files, drifting apart; inline snippets
  that should be one helper or util.
- **LEDGER and state bloat** — a LEDGER so long its tail is noise (roll up the old part
  into a dated summary block); `state/` files that grew monotonically for many runs.

When the accretion is too thick to see past, `spawn` a sub-workflow whose prompt contains
ONLY the pasted recipe (it must not see your run history or the target's) and ask: "as a
first-time reader, what here is confusing, redundant, contradictory, stale, or obviously
vestigial?" Its naive reading is the finding.

Same autonomy gate as the lenses: prune and consolidate reversibly, directly; anything
that might delete meaning the user relies on → deferred `ask_user` naming the target.

## Next
More targets in `cursor.targets`? Move the finished one to `cursor.done`, then
`state/phase.json = {step: "study-target", ...}`. Otherwise
`state/phase.json = {step: "record", ...}`. Read that module.
