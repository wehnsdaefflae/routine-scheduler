"""Read-models: derived views over the on-disk source of truth — never writers.

The disk (run dirs, transcripts, the workflow-usage stream) is the single source of
truth; everything in this package DERIVES a view from it on demand and caches ONLY
behind a stat fingerprint (`memo.memoized` — inode+mtime+size, so atomic rewrites and
appends always miss). Deleting any cache state must never lose data, and no module here
may write anywhere. The FTS index (`rsched.search`) follows the same discipline with
its own incremental machinery; `rsched.registry` is the catalog/run-index sibling.

Members: `stats` (usage rollups), `run_health` (recipe-version regression flags),
`util_stats` (per-util reliability), `statemap` (stage graph + per-phase instrument
panel), `fileactivity` (per-file read/write counts), `tasktree` (the recursive child
tree), plus the shared primitives `memo` (stat-fingerprint cache) and `usage_stream`
(the ONE parser of workflow-usage.jsonl).
"""
