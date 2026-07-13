# Select targets

Pick what this run improves. Depth beats breadth — a few routines done well.

## Do
1. Order the candidates least-recently-visited first (never visited = first). Prefer, within
   that order, candidates with new runs since your last visit — fresh evidence makes the
   lenses sharper.
2. Take the first ~3. A routine with no new runs still qualifies (fresh-eyes works on the
   recipe alone); a disabled routine gets fresh-eyes only.
3. If there are no candidates at all, skip to `record` with an honest "nothing to improve".

## Next
Write `state/phase.json = {step: "study-target", cursor: {targets: [...], done: []}}`.
Read `steps/study-target.md`.
