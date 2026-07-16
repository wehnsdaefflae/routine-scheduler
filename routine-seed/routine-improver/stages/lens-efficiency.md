# Lens: efficiency — a leaner process and tidier files

Cut waste in **how** the target works and keep its files clean:

- **Process.** Which of the target's steps earned their keep in the recent runs, and which
  are ritual that no longer serves the intention? Revise the smallest underperforming
  `stages/`/`main.md` module; drop or merge a step that produces nothing. Removing a
  capability, or changing a convention other steps rely on → deferred `ask_user`.
- **File hygiene, on every file you touch.** Present tense — files describe the *current*
  design; strip diff-narration ("previously…", stale counts) — that history lives in the
  LEDGER and git. Keep any `state/`/`stages/` file under ~350 lines (over → split along a
  read-together seam, or roll old material into `archive/` with a one-paragraph summary).
- **Deliberation level** (`tuning.yaml: deliberation` — part of the recipe you may edit;
  see the autonomy gate). Judge it from the transcripts, both directions:
  - says that merely restate the action beside them ("Reading X", "Running Y") on a task
    full of judgment calls → the level is too LOW: decisions leave no reasoning on paper,
    later turns re-derive or drop context. Raise one stop.
  - long contextualizing says (or notes-file ceremony at `think-on-paper`) on mechanical
    pipeline work where nothing is ever decided → the level is too HIGH: pure token spend.
    Lower one stop.
  Move ONE stop at a time, note old → new + the evidence in `cursor.changes`, and let the
  next visit's transcripts confirm or revert.
- **Look up the idiomatic way** before hand-rolling a leaner mechanism — the leanest
  process is usually the standard one, not a clever bespoke one.
- **Scratch is scratch:** flag stray temporary files (they belong under `state/tmp/`,
  deleted at run end).
