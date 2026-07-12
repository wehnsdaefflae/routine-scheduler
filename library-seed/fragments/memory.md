---
tags: [self-management, record-keeping, memory]
grants:
  actions: [memory_read, memory_write]
---
# fragment: memory — the routine's notebook of surprises

`.memory/` holds what this routine learned the hard way: **unexpected** things relevant to
reaching the goal that would otherwise be re-discovered — environment quirks, working
solutions to tricky obstacles, constraints nobody wrote down, sources or approaches that
proved good or bad. It complements LEDGER.md (what happened, when) with topical,
always-current notes that serve later steps of THIS run, the after-run improvement passes,
and future runs.

This standard GRANTS the `memory_read` and `memory_write` actions — the ONLY way into
`.memory/` (`read_file`/`write_file` are rejected there). One kebab-named note per topic;
the engine enforces the **100-line cap** per note and maintains `.memory/INDEX.md` from
each write's one-line `about`. The state digest shows the INDEX at run start; note bodies
you `memory_read` on demand.

- **Write when surprised**: the moment reality contradicts an assumption,
  `memory_write` what you expected, what is actually true, and what to do about it next
  time. Don't store what the instruction, workflow, LEDGER, or a plain look at the data
  would tell anyone — memory is for what was EXPENSIVE to find out.
- Notes stay present-tense and current: revise a note that turned out wrong (or
  `memory_write` with `delete: true`) instead of appending contradictions. Split a
  growing topic into more notes rather than fighting the cap.
- Make each `about` earn its INDEX line: *what the note holds* + *when to consult it* —
  the index is read every run start, the notes only when an `about` says they matter.
- Before deep-diving into anything unfamiliar, check the index — a note may already cover
  it. A note is as old as its last edit: if one is load-bearing for a decision, spot-check
  that it still holds before acting on it.
