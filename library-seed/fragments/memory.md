---
tags: [self-management, record-keeping, memory]
---
# fragment: memory — the routine's notebook of surprises

`.memory/` holds what this routine learned the hard way: **unexpected** things relevant to
reaching the goal that would otherwise be re-discovered — environment quirks, working
solutions to tricky obstacles, constraints nobody wrote down, sources or approaches that
proved good or bad. It complements LEDGER.md (what happened, when) with topical,
always-current notes that serve later steps of THIS run, the after-run improvement passes,
and future runs.

- One markdown file per topic under `.memory/`, **at most 100 lines each** — split a
  growing file rather than let it sprawl. Notes stay present-tense and current: revise or
  delete what turned out wrong instead of appending contradictions.
- `.memory/INDEX.md` is the catalog: one line per file — `- <file>: what it holds, when to
  consult it`. Update it in the same turn as every note you add, split, or delete. The
  engine shows the index in the state digest at run start; bodies you read_file on demand.
- **Write when surprised**: the moment reality contradicts an assumption, capture what you
  expected, what is actually true, and what to do about it next time. Don't store what the
  instruction, workflow, LEDGER, or a plain look at the data would tell anyone — memory is
  for what was EXPENSIVE to find out.
- Before deep-diving into anything unfamiliar, check the index — a note may already cover
  it. A note is as old as its last edit: if one is load-bearing for a decision, spot-check
  that it still holds before acting on it.
