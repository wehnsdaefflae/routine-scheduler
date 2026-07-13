# Apply the five lenses

Run each lens module against the current target, in this order:

1. `steps/lens-bugfix.md` — what is broken or wrong
2. `steps/lens-research.md` — inputs and knowledge
3. `steps/lens-features.md` — the next missing capability
4. `steps/lens-ui.md` — the artifacts the user actually reads
5. `steps/lens-efficiency.md` — leaner process, tidier files

Read one module, act in that lens only, then the next. Shared rules for every lens:

- **Autonomy gate.** Safe, reversible edits to the target's own files — do them now.
  Changing the target's goal or a hard constraint, deleting large accumulated work, or any
  outward/irreversible act — file a deferred `ask_user` NAMING THE TARGET, and move on.
- **Scale to the evidence.** A lens with nothing to say says nothing; do not manufacture a
  finding per lens. One real fix beats five cosmetic ones.
- **Verify every edit** — read the file back; a claimed-but-unverified change is the worst
  failure this system knows.
- Note each change (and each candidate you rejected, with why) in `cursor.changes` — the
  record step writes them to the LEDGERs.

## Next
Write `state/phase.json = {step: "fresh-eyes", cursor: {...}}`. Read `steps/fresh-eyes.md`.
