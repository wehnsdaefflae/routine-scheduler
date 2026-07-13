# Lens: efficiency — a leaner process and tidier files

Cut waste in **how** the target works and keep its files clean:

- **Process.** Which of the target's steps earned their keep in the recent runs, and which
  are ritual that no longer serves the intention? Revise the smallest underperforming
  `steps/`/`main.md` module; drop or merge a step that produces nothing. Removing a
  capability, or changing a convention other steps rely on → deferred `ask_user`.
- **File hygiene, on every file you touch.** Present tense — files describe the *current*
  design; strip diff-narration ("previously…", stale counts) — that history lives in the
  LEDGER and git. Keep any `state/`/`steps/` file under ~350 lines (over → split along a
  read-together seam, or roll old material into `archive/` with a one-paragraph summary).
- **Look up the idiomatic way** before hand-rolling a leaner mechanism — the leanest
  process is usually the standard one, not a clever bespoke one.
- **Scratch is scratch:** flag stray temporary files (they belong under `state/tmp/`,
  deleted at run end).
