---
tags: [self-management, feedback, quality]
---
# fragment: improvement — act on the self-audit, same run

Immediately after the self-audit determined, **act** on what it found. Check LEDGER.md first
so you never re-try a known-bad edit; read the user's answers to earlier proposals (accept →
do it now; decline → drop it and record why it stays dropped).

Three modes:

1. **Heal broken instructions — now, not deferred.** A contradiction or stale rule in
   workflow.md or a playbook/ file: edit the smallest responsible file directly. Fix the
   *class*, not the instance — if one instruction rotted, look for siblings of the same
   shape and heal them in the same pass.
2. **Reorient** if a phase has drifted from the goal (padding output with weak entries,
   fabricating instead of verifying, ritual steps that no longer serve the instruction).
   Correct the module that caused the drift. Act on fresh-eyes / health-budget findings here
   too: an artifact grown into a wall or stale-but-valid copy is a restructure you do THIS
   run — its gate is "does this make the artifact easier to use" judged on a before/after,
   never a bias to leave things alone (that bias is what lets artifacts rot).
3. **R&D, two tracks.**
   - *Track 1 (outcomes):* close the feedback loop — read what the user pursued, rejected,
     answered, or ignored, and tune the routine's data/configuration toward it. Evaluate at
     most one new source or tool per run (`gu list` first; a missing reusable capability is
     worth proposing as a new global util). Log kept vs dead-end in LEDGER.md.
   - *Track 2 (process):* revise the smallest playbook/workflow module that underperformed;
     consolidate every file you touched (see the hygiene fragment).

**Autonomy + gate.** Autonomous where safe: state/config tuning, small reversible edits to
your own playbook/workflow files, restructuring your own outputs, committing. **Proposal-only**
(a deferred ask_user question, not an act): changing the instruction's goal or hard
constraints, deleting large amounts of accumulated work, outward/irreversible acts (sending,
publishing, spending) unless the instruction explicitly authorizes them. Surface only genuine
external blockers — do the rest yourself; never leave a to-do you could have done (the
autonomy trap is spotting internal work and handing it to the user).

Every change lands in LEDGER.md: what moved, why, and rejected candidates with reasons —
negative evidence prevents re-proposing.
