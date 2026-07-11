"""FastAPI web layer: the ops console's JSON API + SSE streams + static frontend.

Routers are grouped by page (routines, runs, questions, audit, wizard, workflows,
settings). The web layer edits routine config only when no run is active — the engine
subprocess owns everything under a live run.
"""
