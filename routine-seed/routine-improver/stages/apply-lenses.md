# Apply the five lenses

Run each lens module against the current target, in this order:

1. `stages/lens-bugfix.md` — what is broken or wrong
2. `stages/lens-research.md` — inputs and knowledge
3. `stages/lens-features.md` — the next missing capability
4. `stages/lens-ui.md` — the artifacts the user actually reads
5. `stages/lens-efficiency.md` — leaner process, tidier files

Read one module, act in that lens only, then the next. Shared rules for every lens:

- **Autonomy gate.** Safe, reversible edits to the target's RECIPE — its `main.md`, `stages/`,
  `traits/` (and `state/`) — do them now and commit the target's dir with `git-sync`. A target's
  `routine.yaml` is the user's config (budgets, models, permissions, capabilities, fs-roots) with
  ONE exception you may tune directly: the **`deliberation`** key (terse | standard | deliberate |
  think-on-paper — how much of the model's thinking lands on paper; it words the say contract).
  Change it with `edit_file` on the target's `routine.yaml`, ONLY that key, only on run evidence
  (see the efficiency lens), and log old → new + the evidence in `cursor.changes`. Every OTHER
  config change is proposed as a deferred `ask_user` NAMING THE TARGET — the engine blocks the
  write anyway. Same for changing the target's goal or a hard constraint, deleting
  large accumulated work, or anything outward/irreversible: file a deferred `ask_user`, and move on.
  A conversation (under `~/conversations`) gets a LIGHTER touch — NEVER edit its `instruction.md`
  (the user's own first message); improve only its recipe mechanics when run evidence shows friction.
- **Scale to the evidence.** A lens with nothing to say says nothing; do not manufacture a
  finding per lens. One real fix beats five cosmetic ones.
- **Verify every edit** — read the file back; a claimed-but-unverified change is the worst
  failure this system knows.
- Note each change (and each candidate you rejected, with why) in `cursor.changes` — the
  record step writes them to the LEDGERs.

## Next
Write `state/phase.json = {phase: "fresh-eyes", cursor: {...}}`. Read `stages/fresh-eyes.md`.
