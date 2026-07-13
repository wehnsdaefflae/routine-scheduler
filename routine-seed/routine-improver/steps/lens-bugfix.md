# Lens: bugfix — find and fix what's broken or wrong

Based on the target's inferred intention, hunt for **bugs**:

- **Wrong outputs.** Read what its runs produced as a first-time user would. Results that
  disagree between two places, stale-but-valid claims, numbers that don't reconcile — a
  "functional but bad" output is a bug, not a nitpick.
- **Broken instructions.** Contradictions, stale guidance, or friction in the target's
  `main.md`, a `steps/` module, or a state convention. An instruction that produced a
  *wrong* action in a transcript is a defect; one that changed no action is dead weight
  (leave that for fresh-eyes).
- **Fix the class, not the instance.** If one instruction rotted, find siblings of the same
  shape in the target and heal them in the same pass. Edit the smallest responsible file.
- **Research the fix, not just the symptom.** When the right fix is uncertain, look up
  current best practice online (the `websearch` util) before patching. A guessed fix is a
  future bug.

Check the target's LEDGER first so you never re-apply a known-bad fix.
