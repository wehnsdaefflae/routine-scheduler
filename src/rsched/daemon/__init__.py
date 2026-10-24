"""The always-on process around the engine: cron scheduling, run subprocess ownership,
live catalog derivation, event fan-out, and the graceful self-update restart.

A RUNNING engine is the single writer of its run's files (`runs/<ts>/*`, `status.json`). The
daemon writes a run dir only while no engine is in it — the queued status and brief before the
process exists, the admission gate's record and verdict in place of one, the close-out after
one died without a finish — and otherwise writes `inbox/`, retention (delete/gzip old run
dirs), detached-task delivery (artifacts + `state/background.json` on the owner), the edits
queued while a run was active, the `.control/` spools and ledgers, and the util-stats snapshot
under `XDG_STATE_HOME` (`util_stats_watch.py`, debounced on run events — one process computes
that fleet-wide read model instead of every engine at its own run end).
"""
