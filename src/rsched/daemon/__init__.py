"""The always-on process around the engine: cron scheduling, run subprocess ownership,
live catalog derivation, event fan-out, and the graceful self-update restart.

The daemon never touches a run's files — the engine subprocess owns `runs/<ts>/*` and
`status.json`; the daemon's only write into a routine dir is `inbox/`.
"""
