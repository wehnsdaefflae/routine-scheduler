---
tags: [self-management, review, quality]
---
# fragment: fresh eyes — holistic artifact audit with health budgets

Per-run checks are structurally blind to **slow drift**: each run's +1 card, +1 note, +1
section is locally justified, so the author's incrementally-adapting eye never flags "this
page is now a wall". Counter it with a gestalt pass **as a first-time reader, not as the
author**, over the routine's accumulated artifacts (reports, dashboards, state files the
user actually reads, published outputs).

- **Every run, a cheap heuristic scan** against the health budgets below.
- **Every ~5 runs, or whenever a budget trips or artifact structure changes: a deep
  fresh-eyes review** — `spawn` a sub-workflow whose prompt contains ONLY the rendered
  artifact (paste the content; the sub-workflow must NOT see this run's history) and asks:
  "As a first-time reader: what is confusing, overwhelming, redundant, stale, or
  contradicted by reality here?" Fresh eyes see accretion that incremental eyes have
  learned to ignore.
- **"Functional but bad" is a first-class miss.** Renders fine, valid data, no error — but
  a wall of text, stale-but-valid claims, or numbers disagreeing between two places: that is
  a broken finding for the improvement phase (a restructure done this run), not something to
  park or punt to the user. A coherence fix has no approval signal BY NATURE; its gate is a
  usability rubric + a before/after comparison.

**Health budgets** (soft caps; a trip = a restructure finding):
- top-level sections of any user-facing artifact: ~7
- open questions to the user: ~3 (the ask cap — see ask-policy)
- any state/ or steps/ file: ~350 lines (see hygiene)
- summary/result notes: ~200 words
- **K runs additive-only with no simplification (K≈5): force a simplify pass.** Monotonic
  growth is the smell; the budget is the tripwire that turns drift into a checkable miss.
