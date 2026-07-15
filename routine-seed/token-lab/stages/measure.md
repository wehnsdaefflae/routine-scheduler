# measure — refresh the instance's real usage baseline

Where do the tokens actually go? Answer from THIS instance's data, not intuition.

1. Enumerate recent runs across the readable homes: the `routine-runs` util
   (`util name=list args=["routine-runs"]` for its flags) walks a routines home and
   returns per-run records; run it for `~/routines` and, if readable, `~/conversations`.
2. For the most recent ~30 runs, read each run's `runs/<ts>/status.json` (read_file with
   `paths` batching — several files per action) and collect: routine, model, state,
   turns, `usage.in`, `usage.out`, `usage.cached_in`, `usage.cache_write`, elapsed.
3. Reduce to the numbers the report needs (compute yourself, or one `llm` subcall with
   the raw rows):
   - total in/out/cached over the window; tokens per turn by routine and by model;
   - cache hit share (`cached_in / (in + cached_in)`) by endpoint — a near-zero share on
     a metered endpoint is itself a top finding;
   - the three most expensive runs and WHY (long context? many turns? big outputs?).
4. Write the whole reduced table to `state/baseline.json` (overwrite — the transcript
   history and old reports keep the trend).

Cap the sweep: at most ~40 file reads. A consistent sample beats an exhaustive crawl.

Next state: `research` — write `{"phase": "research"}` to `state/phase.json`.
