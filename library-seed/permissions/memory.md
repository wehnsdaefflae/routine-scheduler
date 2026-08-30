---
effect:
  with: keep its own notebook of what it learned the hard way, and read it back later
  without: starts every run knowing only its recipe and its last result
  when: it keeps hitting the same surprises run after run
tags: [self-management, record-keeping, memory]
requires:
  actions: [memory_read, memory_write]
---
# permission: memory — the routine's notebook of surprises

Unlocks the `memory_read` / `memory_write` actions — the ONLY way into `.memory/`, the
notebook of things this routine learned the hard way. Write the moment reality contradicts
an assumption: what you expected, what is actually true, what to do next time. Memory is
for what was EXPENSIVE to find out (environment quirks, working solutions, unwritten
constraints, vetted sources) — never what the instruction, LEDGER, or a plain look at the
data would tell anyone. One kebab-named note per topic, 100 lines max, present tense;
revise or delete notes that turned out wrong instead of appending contradictions. The
engine maintains `.memory/INDEX.md` from each write's `about` line and shows it in every
run's state digest — check it before deep-diving into anything unfamiliar.
