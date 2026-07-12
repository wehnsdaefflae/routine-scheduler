---
tags: [improvement, research, quality]
---
# trait: improve-research — sharpen the routine's inputs and knowledge

Run this after the main work, as one of the routine's improvement passes. First, **infer the
routine's intention** from what this run just did. Based on that intention, do focused **R&D**
on what the routine draws from:

- **Close the feedback loop.** Read what the user pursued, rejected, answered, or ignored, and
  tune the routine's data/configuration toward it. Silence after ~2 runs is a signal too —
  deprioritise what nobody engages with.
- **Yield check.** Which of this run's efforts produced real results, and which was waste?
  Steer future effort toward what pays off.
- **At most one new source or tool per run.** Run `util name=list` (or `gu list`) first; a
  genuinely missing reusable capability is worth proposing as a new global util rather than a
  one-off. Don't churn — evaluate one thing, keep it or log it as a dead end.
- **Evaluate online, not from memory.** Judge a candidate source or tool by actually looking it
  up (the `websearch` util): does it still exist, is it maintained, has something better
  appeared? Your recall of the landscape is stale by default — currency is the point.
- **Fresh eyes.** As a first-time reader of the routine's output: is it researching the right
  things, or padding with weak sources to look busy?

**Check LEDGER.md first.** **Autonomy:** tuning data/config and trying one new source are
reversible — do them. When a change would alter the goal or spend/publish outwardly, or you are
unsure it serves the intention, **file a deferred `ask_user`** (Decisions page; respect the ask
cap). Log kept-vs-dead-end in LEDGER.md — negative evidence stops you re-trying it.
