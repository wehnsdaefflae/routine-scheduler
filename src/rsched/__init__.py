"""rsched — LLM agent routine scheduler (engine, daemon, web UI)."""

# Single source of truth for the release version (pyproject reads it via hatch).
# Bump the minor on every user-facing revision; /api/status pairs it with the git
# commit stamp of the running checkout so a deploy is always identifiable.
__version__ = "0.95.0"
