# fragment: self-audit — determine the routine's health

Run this near the end of every run, after the main work. This phase **determines**; it does
not act (acting is the improvement fragment, same run). Check LEDGER.md first, and read the
answers to earlier audit questions in the state digest, so still-open items carry forward.

Audit through six lenses:

1. **Goal alignment** — for each phase of this routine's lifecycle: healthy, weak, or broken?
   Has anything drifted from what the instruction actually asks for? (the hero lens)
2. **Broken instructions** — contradictions, stale guidance, or friction in workflow.md,
   playbook/ files, or state conventions (self-healing candidates).
3. **Improvement opportunities** — R&D: new sources/tools (`gu list`), sharper configuration,
   yield: which effort produced results, which was waste?
4. **Instruction → decisions → actions mirror** — trace what each in-play instruction led to
   this run. An instruction that never changes behavior is dead weight; one that produced a
   wrong action is a defect.
5. **Carried-over items** — earlier audit questions to the user and their answers (or
   silence). An accepted proposal is a work order for the improvement phase; a declined one
   is settled — record it and stop re-proposing; silence after 2 runs means deprioritize.
6. **Holistic artifact audit (fresh eyes)** — judge the routine's **accumulated outputs as a
   first-time reader meets them** (see the fresh-eyes fragment). Lenses 1-5 are blind to slow
   drift: each run's +1 note or +1 file is locally justified, so nothing ever flags "this has
   become a wall".

Write the findings compactly into `state/audit.json`
(`{run, verdict, phases: [...], healing: [...], improvements: [...], asked: [...]}`).
Findings only — **edit nothing yet**, so the audit cannot be bent to justify a change
already decided on. Items that need the user's judgment become **deferred ask_user
questions** (respect the ask cap in the ask-policy fragment; give each a self-contained
question so it makes sense in the questions inbox without this run's context).
