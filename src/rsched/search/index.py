"""Instance-wide full-text search: an SQLite FTS5 index over the searchable prose of
both homes (see sources.py). The index is a PURE CACHE of the filesystem — delete the
file and it rebuilds from disk; the flat files stay the only source of truth.

Location: `<routines_home>/.control/search.sqlite3` — the daemon-owned spot next to
workflow-usage.jsonl, outside every routine dir and outside the repo. ONE writer: the
daemon/web process (engine subprocesses and the CLI never import this). Freshness is
per-FILE stat fingerprints, registry.py's model: every refresh() re-stats the sources,
reindexes what changed, and prunes rows for files gone from disk (run retention, deleted
routines, gzip rotation) — there is no invalidation protocol to get wrong. refresh()
takes a time budget so no caller (scheduler-adjacent maintainer thread or a query-time
top-up) ever stalls on a cold index; what it can't finish, the next call continues.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from ..config import ServerConfig
from . import sources

log = logging.getLogger("rsched.search")

DB_NAME = "search.sqlite3"
SCHEMA_VERSION = 1
# Result snippets highlight matches between these private-use sentinels; the UI splits on
# them to build <mark> nodes via textContent — no HTML ever rides the payload.
MARK_START, MARK_END = "\ue000", "\ue001"
MAX_LIMIT = 200

# External-content FTS5: metadata lives in `docs` (queryable, per-file prunable via the
# docs_file index), the FTS table holds only the text and stays in sync through the
# insert/delete triggers. `files` carries the stat fingerprint driving incremental refresh.
# Porter stemming folds English inflections ("playbook" finds "playbooks"); tokens it
# doesn't recognize pass through effectively unchanged, so mixed-language prose keeps
# matching exact forms. Snippets always show the original text.
_SCHEMA = """
CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  fingerprint TEXT NOT NULL
);
CREATE TABLE docs (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  home TEXT NOT NULL,
  slug TEXT NOT NULL,
  run_ts TEXT NOT NULL DEFAULT '',
  sub TEXT NOT NULL DEFAULT '',
  turn INTEGER,
  phase TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL
);
CREATE INDEX docs_file ON docs(file_id);
CREATE VIRTUAL TABLE docs_fts USING fts5(text, content='docs', content_rowid='id',
                                         tokenize='porter unicode61');
CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
"""


def _fingerprint(path: Path) -> str:
    """(inode, mtime_ns, size) as one comparable string — atomic_write renames a fresh
    tmp file into place (new inode), so every cross-process rewrite is caught even
    inside one mtime tick. "" = the file vanished between listing and stat.
    """
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"


class SearchIndex:
    """The one per-process handle: owns the sqlite connection (created lazily, serialized
    behind a lock — FastAPI sync routes run in a threadpool) and the refresh/search API.
    """

    def __init__(self, server: ServerConfig):
        self.server = server
        self.path = server.routines_home / ".control" / DB_NAME
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ---- connection & schema ------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = self._open()
        except sqlite3.DatabaseError as exc:   # corrupt / not a database: it's a cache
            log.warning("search index unreadable (%s) — rebuilding from disk", exc)
            self._remove_db_files()
            conn = self._open()
        self._conn = conn
        return conn

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")   # a cache never needs full fsync
            conn.execute("PRAGMA busy_timeout=5000")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                if version != 0:
                    log.info("search index schema v%s → v%s: rebuilding", version, SCHEMA_VERSION)
                conn.close()
                self._remove_db_files()
                conn = sqlite3.connect(self.path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
        except BaseException:
            conn.close()
            raise
        return conn

    def _remove_db_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                Path(f"{self.path}{suffix}").unlink()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.close()
                self._conn = None

    # ---- indexing -------------------------------------------------------------------------

    def refresh(self, budget_s: float | None = None) -> dict:
        """One incremental pass: prune rows for files gone from disk, then walk the
        candidates NEWEST run first, fingerprinting and reindexing in a single budgeted
        loop — the budget covers the stat()s too, not just the reindexing (a stat-walk
        over 10⁴ files was previously spent before the first budget check). Checked
        AFTER each file, so every pass makes progress and any backlog eventually drains.
        Returns {"indexed", "pending", "files"}; pending counts files not yet CONFIRMED
        fresh this pass — a later call continues the work.
        """
        t0 = time.monotonic()
        with self._lock:
            db = self._db()
            found = {str(s.path): s for s in sources.iter_sources(self.server)}
            known = dict(db.execute("SELECT path, fingerprint FROM files").fetchall())
            for path in [p for p in known if p not in found]:
                self._drop_file(db, path)
            candidates = sorted(found.items(), key=lambda item: item[1].run_ts, reverse=True)
            indexed = scanned = 0
            for path, src in candidates:
                if _fingerprint(src.path) != known.get(path):
                    self._index_file(db, src)
                    indexed += 1
                scanned += 1
                if budget_s is not None and time.monotonic() - t0 >= budget_s:
                    break
            db.commit()
            return {"indexed": indexed, "pending": len(candidates) - scanned,
                    "files": len(found)}

    def _drop_file(self, db: sqlite3.Connection, path: str) -> None:
        row = db.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if row is None:
            return
        db.execute("DELETE FROM docs WHERE file_id=?", (row[0],))
        db.execute("DELETE FROM files WHERE id=?", (row[0],))

    def _index_file(self, db: sqlite3.Connection, src: sources.SourceFile) -> None:
        # stat BEFORE read: a file changing during/after extraction keeps a pre-change
        # fingerprint, so the very next refresh sees the mismatch and reindexes it.
        fp = _fingerprint(src.path)
        docs = sources.extract(src)
        path = str(src.path)
        row = db.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if row is not None:
            file_id = row[0]
            db.execute("DELETE FROM docs WHERE file_id=?", (file_id,))
            db.execute("UPDATE files SET fingerprint=? WHERE id=?", (fp, file_id))
        else:
            file_id = db.execute("INSERT INTO files(path, fingerprint) VALUES(?, ?)",
                                 (path, fp)).lastrowid
        db.executemany(
            "INSERT INTO docs(file_id, kind, home, slug, run_ts, sub, turn, phase, ts, text)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(file_id, d.kind, src.home, src.slug, src.run_ts, src.sub, d.turn, d.phase,
              d.ts, d.text) for d in docs])

    # ---- querying ---------------------------------------------------------------------------

    def search(self, q: str, limit: int = 50) -> list[dict]:
        """Ranked hits for `q`. The raw query is tried first so FTS5 syntax (phrases,
        OR, NEAR, prefix*) keeps working for those who speak it; anything the FTS5
        parser rejects ("foo-bar", stray quotes) is retried with every term escaped to
        a plain phrase — everyday queries never see a syntax error. Raises ValueError
        on an empty/unsearchable query (the API's 400).
        """
        q = q.strip()
        if not q:
            raise ValueError("empty query")
        limit = max(1, min(int(limit), MAX_LIMIT))
        # A dedicated read connection per query: WAL lets readers run WHILE the writer
        # indexes, so a long refresh pass never makes the search box hang behind
        # self._lock (the lock stays the writer's — refresh/close/schema).
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path, check_same_thread=False)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                return self._match(conn, q, limit)
            except sqlite3.OperationalError:
                try:
                    return self._match(conn, _escape_query(q), limit)
                except sqlite3.OperationalError:
                    return []   # schema not created yet (first boot, empty cache)
        finally:
            conn.close()

    def _match(self, db: sqlite3.Connection, fts_query: str, limit: int) -> list[dict]:
        rows = db.execute(
            "SELECT d.kind, d.home, d.slug, d.run_ts, d.sub, d.turn, d.phase, d.ts,"
            "       snippet(docs_fts, 0, ?, ?, ' … ', 14)"
            "  FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid"
            " WHERE docs_fts MATCH ? ORDER BY bm25(docs_fts) LIMIT ?",
            (MARK_START, MARK_END, fts_query, limit)).fetchall()
        return [{"kind": r[0], "home": r[1], "slug": r[2], "run_ts": r[3], "sub": r[4],
                 "turn": r[5], "phase": r[6], "ts": r[7], "snippet": r[8]} for r in rows]


def _escape_query(q: str) -> str:
    """Rewrite free text into FTS5 the parser always accepts: each whitespace token
    becomes a quoted phrase (implicit AND), a trailing * survives as a prefix query.
    """
    tokens = []
    for raw in q.split():
        prefix = raw.endswith("*")
        term = raw.rstrip("*").replace('"', '""')
        if not re.search(r"\w", term, re.UNICODE):
            continue   # punctuation-only tokens match nothing and can confuse the parser
        tokens.append(f'"{term}"*' if prefix else f'"{term}"')
    if not tokens:
        raise ValueError("query has no searchable terms")
    return " ".join(tokens)
