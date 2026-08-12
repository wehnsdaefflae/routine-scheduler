# Lens: efficiency — a leaner process and tidier files

Cut waste in **how** the target works and keep its files clean:

- **Process.** Which of the target's steps earned their keep in the recent runs, and which
  are ritual that no longer serves the intention? Revise the smallest underperforming
  `stages/`/`main.md` module; drop or merge a step that produces nothing. Removing a
  capability, or changing a convention other steps rely on → deferred `ask_user`.
- **Prose → code (user standing rule 2026-08-12).** A script is the recipe's TOOLING,
  never a co-equal interpreter: the recipe stays the single interpreter of the task and
  delegates judgment-free sub-steps to the target's OWN persistent `scripts/<name>.py`
  (PEP 723; the routine's venv; only header-declared granted secrets; no util or model
  access inside). Keep every responsibility in its best home, in BOTH directions:
  - *Which recipe steps are pure mechanism?* Repeated fetching/polling (mail, feeds,
    APIs), parsing/reformatting structured data, arithmetic on updated data,
    filtering/sorting/dedup, assembling a fixed artifact, regex extraction, date math —
    anything the transcripts show re-done identically every run with no judgment in it.
    Revise the step to direct the run to author the script ONCE into `scripts/` and call
    it thereafter (the target's run writes the script itself, at its own judgment). A
    GLOBAL util is for capability genuinely reusable ACROSS routines — never the default
    home for one routine's own pipeline work.
  - *Which coded steps actually need judgment?* Genuinely generative work (drafting
    prose, evaluating fit, creative synthesis, deciding what matters) belongs in the
    RECIPE — never force it into code, and pull it back into prose when you find it
    fossilized in a script that keeps needing edits.
  - *Is each helper in the right HOME?* A script another routine has started wanting is
    a util in hiding — propose promoting it to the shared library (`write_util`, via the
    target's own run or a deferred `ask_user`). A global util only the target ever calls
    is a script in hiding — flag it to the global-utils-review routine (`report`,
    addressed), which owns the demotion.
  Running a script needs the target's `script` capability — config is the user's, so
  when a clear candidate exists on a target without it, propose the grant as a deferred
  `ask_user` NAMING THE TARGET, the step, and the evidence, and leave the recipe
  unchanged. Don't churn a clean, stable routine for a marginal candidate; log each
  rejected candidate with why.
- **File hygiene, on every file you touch.** Present tense — files describe the *current*
  design; strip diff-narration ("previously…", stale counts) — that history lives in the
  LEDGER and git. Keep any `state/`/`stages/` file under ~350 lines (over → split along a
  read-together seam, or roll old material into `archive/` with a one-paragraph summary).
  `state/notes.md` (the engine-captured `note` stream) gets the same treatment plus one
  extra rule: a note a stranger can't understand from its line alone is BROKEN — rewrite
  it self-contained or prune it; drop notes that stopped being true.
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
