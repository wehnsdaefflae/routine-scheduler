# fragment: improve-ui — the artifacts the user actually reads

Run this after the main work, as one of the routine's improvement passes. First, **infer the
routine's intention** from what this run just did. Based on that intention, audit the routine's
**user-facing output** (reports, dashboards, digests, the state files a person opens) **as a
first-time reader, not as its author** — incremental eyes learn to ignore accretion:

- **"Functional but bad" is the target.** Renders fine, valid data, no error — but a wall of
  text, redundant sections, stale-but-valid claims, or two places that disagree: that is a
  finding to fix THIS run, not something to park. A coherence fix has no user "approval signal"
  by nature; its gate is a usability rubric + a before/after.
- **Health budgets** (a trip = a restructure finding): ~7 top-level sections per artifact; ~200-
  word summaries; no single artifact a wall. **~5 runs of additive-only growth with no
  simplification → force a simplify pass.** Monotonic growth is the smell.
- **Borrow current conventions.** When restructuring, check online (the `websearch` util) how
  this artifact form is done well today — report structure, dashboard layout, digest format —
  rather than inventing a layout from scratch.
- **Every ~5 runs, or when structure changes:** `spawn` a sub-workflow whose prompt contains
  ONLY the rendered artifact (paste it; it must not see this run's history) and asks "as a
  first-time reader, what is confusing, overwhelming, redundant, stale, or contradicted here?"

**Autonomy:** restructuring your *own* outputs for clarity is safe — do it. If a change would
drop information the user relies on, or alter what they asked for, **file a deferred `ask_user`**
(Decisions page; respect the ask cap). Record restructures in LEDGER.md.
