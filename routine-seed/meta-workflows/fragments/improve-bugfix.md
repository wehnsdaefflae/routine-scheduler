# fragment: improve-bugfix — find and fix what's broken or wrong

Run this after the main work, as one of the routine's improvement passes. First, **infer the
routine's intention** from what this run just did — inferring from behaviour, not from a fresh
reading of the instruction, is how you catch drift. Based on that intention, hunt for **bugs**:

- **Wrong outputs.** Read back what this run produced as a first-time user would. Results that
  disagree between two places, stale-but-valid claims, numbers that don't reconcile — a
  "functional but bad" output is a bug, not a nitpick.
- **Broken instructions.** Contradictions, stale guidance, or friction in `main.md`, a `steps/`
  module, or a state convention. An instruction that produced a *wrong* action is a defect; one
  that changed no action this run is dead weight.
- **Fix the class, not the instance.** If one instruction rotted, find siblings of the same
  shape and heal them in the same pass. Edit the smallest responsible file directly.
- **Research the fix, not just the symptom.** When the right fix is uncertain — an error you
  half-recognize, a tool's behaviour, a convention — look up current best practice online (the
  `websearch` util) before patching. A guessed fix is a future bug.

**Check LEDGER.md first** so you never re-apply a known-bad fix. **Autonomy:** safe, reversible
fixes to your own `steps/`/`main.md`/state — do them now. Changing the instruction's goal or a
hard constraint, or deleting large accumulated work — **don't act; file a deferred `ask_user`**.
When you're unsure how to proceed, ask: the question appears on the Decisions page (respect the
ask cap in ask-policy). Record every fix — and every candidate you rejected, with why — in
LEDGER.md.
