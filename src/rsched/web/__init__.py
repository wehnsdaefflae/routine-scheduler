"""FastAPI web layer: the ops console's JSON API + SSE streams + static frontend.

Routers are grouped by surface: routines, conversations, background tasks, runs,
schedule, stats, summary, questions (decisions), audit, traces, settings (incl. oauth +
machines), workflows/library, playbooks, wizard, LLM tasks, hooks (the one
unauthenticated ingest), search, and the fs picker — see app._include_api_routers for
the authoritative list.

Ownership: the engine subprocess owns everything under a live run (run dirs,
status.json, git commits in the routine dir). The web layer edits routine CONFIG only
when no run is active (409 otherwise) — with two deliberate live-edit exceptions: a
conversation's settings, and trait add/remove (the traits/ dir is web-owned; a live run
is told via control.json `add_traits`). Web-side routine-dir commits take the same
per-repo lock the engine's autocommit does.
"""
