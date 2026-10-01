"""FastAPI web layer: the ops console's JSON API + SSE streams + static frontend.

Routers are grouped by surface: routines, conversations, background tasks, runs,
schedule, stats, summary, questions (decisions), audit, traces, settings (incl. oauth +
machines), workflows/library, playbooks, LLM tasks, hooks (the one
unauthenticated ingest), search, and the fs picker — see appwiring._include_api_routers for
the authoritative list.

Ownership: the engine subprocess owns everything under a live run (run dirs,
status.json, git commits in the routine dir); the web layer owns a routine's CONFIG and
reaches a live run only through its control.json. While a run is active, a routine.yaml
edit lands at once and applies from the next run (D35), the live run TOLD what changed
(F337 — rule bind/unbind reaches it the same way, as `add_rules`/`drop_rules`); a recipe
file, recipe revert or trigger edit is held in the pending-edit spool and replayed at run
end (D78-A, `routines_common.queue_or_apply`); and what a live run would overwrite or
cannot survive — archive, a local reminder's delete, the settings page's multi-field
accept — is a 409 (`routines_common.guard_not_active`). Web-side routine-dir commits take
the same per-repo lock the engine's autocommit does.
"""
