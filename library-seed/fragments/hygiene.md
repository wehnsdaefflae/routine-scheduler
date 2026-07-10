---
tags: [self-management, cleanup, quality]
---
# fragment: hygiene — files stay small, present-tense, and consolidated

Apply to every file you touch in a run (main.md, steps/, state/, outputs):

- **Present tense.** Files describe the current design. Strip diff-narration ("we moved off
  X", "previously", stale counts, references to revisions that no longer exist) — that
  history lives in LEDGER.md and git. Preserve *live* rationale (why the current design is
  the way it is).
- **Line budget ~350 per file.** Over budget → split along a "read-together" seam (each
  detail file is opened once, for one situation), or roll old material into `archive/` plus
  a one-paragraph summary. Prefer few cohesive files over many tiny ones.
- **Never duplicate inline logic.** A snippet used twice (the same shell pipeline, the same
  parsing step) becomes one documented helper: a steps/ note, or — if genuinely reusable
  beyond this routine — a proposal for a new `gu` util.
- **Scratch is scratch.** Temporary files go under `state/tmp/` and are deleted before the
  run finishes; the run directory is for the engine, not for your intermediates.
- Consolidation happens **on touch** — don't schedule cleanup for later; a file you edited
  leaves the run cleaner than you found it.
