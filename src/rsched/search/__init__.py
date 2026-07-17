"""Instance-wide full-text search: SQLite FTS5 over the prose of both homes.

Two modules, one seam each: `sources` (which files are indexed and what searchable docs
come out of them), `index` (the sqlite cache — fingerprinted incremental refresh + the
ranked-hit query). The web layer (`web/api_search.py`) is the only consumer.
"""

from .index import DB_NAME, MARK_END, MARK_START, SearchIndex
from .sources import DOC_KINDS

__all__ = ["DB_NAME", "DOC_KINDS", "MARK_END", "MARK_START", "SearchIndex"]
