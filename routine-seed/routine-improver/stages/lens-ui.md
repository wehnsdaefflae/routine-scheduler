# Lens: UI — the artifacts the user actually reads

Audit the target's **user-facing output** (reports, dashboards, digests, the state files a
person opens) as a first-time reader, not as its author:

- **"Functional but bad" is the target.** Renders fine, valid data, no error — but a wall
  of text, redundant sections, stale-but-valid claims, or two places that disagree: fix it
  on this visit, don't park it.
- **Health budgets** (a trip = a restructure finding): ~7 top-level sections per artifact;
  ~200-word summaries; no single artifact a wall. Several visits of additive-only growth
  with no simplification → force a simplify pass.
- **Read the interaction traces when they exist.** The web console records UI events to
  `~/routines/.ui-traces/<YYYYMMDD>.jsonl` (fields: ts/kind/view/target). `error` events
  are broken flows, repeated clicks on one target are friction — direct evidence of what a
  person actually struggled with, stronger than any guess.
- **Borrow current conventions** (the `websearch` util) when restructuring — report
  structure, dashboard layout, digest format — rather than inventing a layout.
- If a change would drop information the user relies on, or alter what they asked for —
  deferred `ask_user` naming the target.
