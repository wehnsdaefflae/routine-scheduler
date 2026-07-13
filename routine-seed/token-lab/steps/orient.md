# orient — pick this run's focus

Cheap reads only; no experiments yet.

1. `read_file state/backlog.md` (missing → this is the first run: you will seed it in the
   `research` step from the starter entries listed there).
2. Consult the `.memory/` index in your state digest; `memory_read` any note relevant to
   the backlog's open entries before re-deriving anything.
3. The previous run's result is in your state digest. Note what it left open — an
   unfinished experiment beats a new one.
4. Decide and write down (in `say`, not a file): this run's research focus and which ONE
   backlog entry becomes today's experiment.

Rules of thumb:
- Prefer the entry with the highest (potential saving × confidence you can measure it
  today) — not the most interesting one.
- If the last THREE runs experimented on the same method, force a different one: breadth
  beats a rut.

Next state: `measure` — write `{"phase": "measure"}` to `state/phase.json`.
