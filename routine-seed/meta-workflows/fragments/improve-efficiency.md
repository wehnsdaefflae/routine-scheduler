# fragment: improve-efficiency — a leaner process and tidier files

Run this after the main work, as one of the routine's improvement passes. First, **infer the
routine's intention** from what this run just did. Based on that intention, cut waste in **how**
the routine works and keep its own files clean:

- **Process.** Which steps earned their keep this run, and which were ritual that no longer
  serves the intention? Revise the smallest underperforming `steps/`/`main.md` module; drop or
  merge a step that produces nothing.
- **File hygiene, on every file you touched this run.** Present tense — files describe the
  *current* design; strip diff-narration ("previously…", stale counts) — that history lives in
  LEDGER.md and git. Keep any `state/`/`steps/` file under ~350 lines (over → split along a
  read-together seam, or roll old material into `archive/` with a one-paragraph summary). Never
  duplicate inline logic — a snippet used twice becomes one helper (or a proposed util).
- **Look up the idiomatic way.** Before hand-rolling a leaner mechanism, search online (the
  `websearch` util) for the current idiomatic approach or a well-maintained package — the
  leanest process is usually the standard one, not a clever bespoke one.
- **Scratch is scratch:** temporary files under `state/tmp/`, deleted before the run finishes.
- **Fresh eyes:** ~5 runs of additive-only growth with no simplification → force a simplify pass.

Consolidation happens **on touch** — a file you edited leaves the run cleaner than you found it;
don't schedule cleanup for later. **Autonomy:** these are reversible edits to your own files —
do them. Removing a capability, or changing a convention other steps rely on → **file a deferred
`ask_user`** (Decisions page; respect the ask cap). Record structural changes in LEDGER.md.
