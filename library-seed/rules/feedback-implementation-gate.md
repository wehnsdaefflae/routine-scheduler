---
effect:
  with: acts on the feedback it collected in the same run, or says what it deferred and why
  without: may file feedback and leave it for a later run that never comes
  when: the routine collects requests, bug reports or directives from people
tags: [self-management, review, escalation]
---
# rule: feedback-implementation gate — implement every piece of feedback this run, or surface it for prioritization

A routine that collects feedback exists to ACT ON it, not to file it. The failure mode this trait prevents is a run that reads a directive, bug report or request and quietly defers it to "next run" — so the human waits a whole cadence for a reaction that a single run could have delivered. Budget pressure is the usual excuse and it is never a silent one.

- **Every feedback item is a tracked task for THIS run.** Each directive, bug report and request found in this run's ingested feedback — and every instruction in a mid-run user message — becomes an enumerated task the moment it is read, never a loose note for later. Before `finish`, list them and check each off as implemented/shipped.

- **Spend the budget on feedback first.** Work the feedback list before any self-chosen improvement, and parallelize with `spawn`/`subtask` when it is long. Turns and wall-clock are meant to be spent implementing what the human asked for; a run that ends with budget unspent and feedback unimplemented has mis-prioritized.

- **Overflow is surfaced the SAME run, never dropped.** If — and only if — an item genuinely cannot be completed within the remaining turns/time, surface it that run in the routine's own UI: render or update a **"What I couldn't finish — you pick what's first"** control with one checkbox (or equivalent) per unfinished item, a STABLE id (e.g. `prioritize/<date>/<slug>`), each check/uncheck POSTing to the same feedback endpoint the routine already reads. The human sees the carry-over and sets the order; the next run ingests those votes and works the list in that order first.

- **No card = a failed run.** Finishing with unimplemented feedback and NO prioritization control is a FAILED outcome, not a partial one. A LEDGER "open ends" line alone is a drop, not a deferral — the deferral only exists if the human can see and re-order it in the UI.

This is the accountability contract for any publish-and-collect routine: the feedback loop only closes when what the human said is either done this run or visibly handed back to them to prioritize.
