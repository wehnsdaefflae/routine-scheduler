"""Read-models: derived views over the on-disk source of truth — never writers.

The disk (run dirs, transcripts, the workflow-usage stream) is the single source of
truth; everything in this package DERIVES a view from it on demand and caches ONLY
behind a stat fingerprint (`memo.memoized` — inode+mtime+size, so atomic rewrites and
appends always miss). Deleting any cache state must never lose data, and no module here
may write anywhere. The FTS index (`rsched.search`) follows the same discipline with
its own incremental machinery; `rsched.registry` is the catalog/run-index sibling.

Members: `surface` (the setup surface — what a routine still needs, joined from
`surface_nodes`' row vocabulary plus `surface_needs`, `surface_schedule` and
`surface_caps`), `remedies` (the same rows in words), `stats` (usage rollups),
`run_health` (recipe-version and time-window regression flags),
`util_stats` (per-util reliability), `statemap` (stage graph + per-phase instrument
panel), `fileactivity` (per-file read/write counts), `tasktree` (the recursive child
tree), `items` (the system-maintenance index: findings, decisions, bug reports),
`summaries` (each routine's latest finish message, shaped as a fourth item type so the
Messages page can serve both), plus
the shared primitives `memo` (stat-fingerprint cache with single-flight misses: a
burst of identical requests computes once), `library_reads` (the ONE memoized reader of
the library's parsed docs, util headers and whole-library lint), `usage_stream` (the
ONE parser of workflow-usage.jsonl) and `health_stream` (the ONE parser of
health-events.jsonl, plus the blocked-fleet fold behind `/api/health/blocked`).
`web/decisions_read` (the open-decisions list behind `/api/questions`) follows the same
discipline from the web package.
"""
