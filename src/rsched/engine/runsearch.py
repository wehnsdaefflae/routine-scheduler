"""Search this routine's PAST RUNS — `memory_read` with `args` instead of a note `name`.

What a run knows of its routine's history is what something CARRIED forward: the notes, the
LEDGER, the notes tail, the last run's result. Everything else a past run saw — the exact error
a util returned three weeks ago, the command that finally worked, what the operator wrote mid-run
— sits in `runs/<ts>/transcript.jsonl` where no run looks, because reading a transcript cold is a
large, blind read. `engine/recall.py` brings back the CURRENT run's archived middle; this is the
same question asked across runs.

Hermes Agent's `session_search` is the model, measured there and kept here in shape: SQLite FTS5
with BM25 ranking — no model call, no embedding, nothing outside the standard library (the
dependency `engine/recall.py` declines) — returning a few hits from DISTINCT runs, the best one
with a little context around it and the rest as one snippet each, so a search costs a bounded
observation rather than a transcript.

THE INDEX is `runs/.recall.sqlite`, engine-owned and gitignored with `runs/` (sealed from the
run's own writes, `ownseal.SEALED_DIRS`). It is brought up to date at each search: a run whose
transcript changed size or mtime is re-indexed, a run that was pruned is dropped, and the run
doing the search is left out — its own history is in its context or its archive. What is indexed
is what the run SAW and SAID: each action's `say` and brief, each observation as rendered
(`observations.format_observation`, capped), a person's messages, questions and answers, and each
finish summary. Engine notes are not: they are the engine's boilerplate, the same in every run.

A query is FTS5 syntax (words, `"a phrase"`, `prefix*`, `OR`). Plain words are matched ALL
together first, then ANY of them when that finds nothing, and the observation says which.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .transcript import read_events

#: The index file inside `runs/`.
INDEX_NAME = ".recall.sqlite"

#: Hits returned, each from a different run.
HITS = 3

#: Neighbouring records shown around the best hit, either side.
AROUND = 2

#: Characters indexed per record, and shown per neighbouring record.
DOC_CHARS = 4_000
SHOW_CHARS = 400

#: Newest past runs kept in the index (the routine's own pruning keeps fewer).
MAX_RUNS = 120

_TS = re.compile(r"^\d{8}-\d{6}$")


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5("
               "text, run UNINDEXED, turn UNINDEXED, etype UNINDEXED, kind UNINDEXED)")
    db.execute("CREATE TABLE IF NOT EXISTS indexed (run TEXT PRIMARY KEY, size INTEGER, "
               "mtime REAL)")
    return db


def _transcript(run_dir: Path) -> Path | None:
    for name in ("transcript.jsonl", "transcript.jsonl.gz"):
        if (run_dir / name).is_file():
            return run_dir / name
    return None


def _text(ev: dict) -> tuple[str, str]:
    """(kind, searchable text) for one transcript event, or ("", "") when it is not indexed."""
    from .actionschema import brief_value
    from .observations import format_observation

    etype, p = ev.get("type"), ev.get("payload")
    if not isinstance(p, dict):
        return "", ""
    if etype == "assistant_action":
        kind = str(p.get("kind") or "")
        extra = str(p.get("summary") or "") if kind == "finish" else brief_value(p)
        return kind, f"{p.get('say') or ''}\n{extra}"
    if etype == "observation":
        return str(p.get("kind") or ""), format_observation(p)
    if etype == "user_injection" and p.get("source") != "engine":
        return "", str(p.get("text") or "")
    if etype in ("question", "answer"):
        return "", json.dumps(p, ensure_ascii=False)
    if etype == "finish":
        return "finish", str(p.get("summary") or "")
    return "", ""


def _refresh(db: sqlite3.Connection, runs_dir: Path, skip: str) -> int:
    """Bring the index up to date with `runs_dir`; return how many runs it covers."""
    present = sorted((d for d in runs_dir.iterdir() if d.is_dir() and _TS.match(d.name)
                      and d.name != skip), key=lambda d: d.name)[-MAX_RUNS:]
    known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT run, size, mtime FROM indexed")}
    keep = {d.name for d in present}
    for gone in set(known) - keep:
        db.execute("DELETE FROM docs WHERE run = ?", (gone,))
        db.execute("DELETE FROM indexed WHERE run = ?", (gone,))
    for run_dir in present:
        path = _transcript(run_dir)
        if path is None:
            continue
        st = path.stat()
        if known.get(run_dir.name) == (st.st_size, st.st_mtime):
            continue
        db.execute("DELETE FROM docs WHERE run = ?", (run_dir.name,))
        events, _ = read_events(path)
        rows = []
        for ev in events:
            kind, text = _text(ev)
            if text.strip():
                rows.append((text[:DOC_CHARS], run_dir.name, ev.get("turn") or 0,
                             ev.get("type"), kind))
        db.executemany("INSERT INTO docs (text, run, turn, etype, kind) VALUES (?,?,?,?,?)",
                       rows)
        db.execute("INSERT OR REPLACE INTO indexed VALUES (?,?,?)",
                   (run_dir.name, st.st_size, st.st_mtime))
    db.commit()
    return len(keep)


def _match(db: sqlite3.Connection, query: str) -> list[tuple]:
    sql = ("SELECT rowid, run, turn, etype, kind, snippet(docs, 0, '«', '»', ' … ', 24) "
           "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT 60")
    return db.execute(sql, (query,)).fetchall()


def _any_of(terms: str) -> str:
    """The same words as an OR of quoted tokens — the broadened second try."""
    return " OR ".join(f'"{w}"' for w in re.findall(r"\w+", terms))


def search(routine_dir: Path, run_ts: str, terms: str) -> dict:
    """The `memory_read` observation for a search of the routine's past runs."""
    out: dict = {"kind": "memory_read", "search": terms}
    runs_dir = Path(routine_dir) / "runs"
    if not terms.strip():
        return {**out, "error": "a search needs words in `args`"}
    if not runs_dir.is_dir():
        return {**out, "runs": 0, "hits": []}
    try:
        with closing(_connect(runs_dir / INDEX_NAME)) as db:
            out["runs"] = _refresh(db, runs_dir, run_ts)
            broadened = False
            try:
                rows = _match(db, terms)
            except sqlite3.OperationalError:
                rows = []
            if not rows and (fallback := _any_of(terms)):
                rows, broadened = _match(db, fallback), True
            out["broadened"] = broadened
            out["hits"] = _hits(db, rows)
    except sqlite3.Error as exc:
        return {**out, "error": f"the past-run index could not be read: {exc}"}
    return out


def _hits(db: sqlite3.Connection, rows: list[tuple]) -> list[dict]:
    hits: list[dict] = []
    for rowid, run, turn, etype, kind, snippet in rows:
        if any(h["run"] == run for h in hits):
            continue
        hit = {"run": run, "turn": turn, "etype": etype, "kind": kind, "snippet": snippet}
        if not hits:   # only the best hit is shown with its neighbours
            near = db.execute(
                "SELECT turn, etype, kind, substr(text, 1, ?) FROM docs WHERE run = ? AND "
                "rowid BETWEEN ? AND ? AND rowid != ? ORDER BY rowid",
                (SHOW_CHARS, run, rowid - AROUND, rowid + AROUND, rowid)).fetchall()
            hit["around"] = [{"turn": t, "etype": e, "kind": k, "text": x} for t, e, k, x in near]
        hits.append(hit)
        if len(hits) >= HITS:
            break
    return hits


def render(obs: dict) -> str:
    """The observation text for a search (`obs_files`, memory_read)."""
    terms = obs.get("search")
    if err := obs.get("error"):
        return f"OBSERVATION (memory_read search {terms!r} FAILED): {err}"
    hits = obs.get("hits") or []
    runs = obs.get("runs", 0)
    if not hits:
        return (f"OBSERVATION (memory_read search {terms!r}): no past run of this routine "
                f"matched ({runs} runs searched; this run is not among them). The search is "
                'by word: try other or fewer words, a quoted "exact phrase", or prefix*.')
    how = (" — no run had ALL the words, so these match ANY of them" if obs.get("broadened")
           else "")
    lines = [(f"OBSERVATION (memory_read search {terms!r} over {runs} past runs of this "
             f"routine, best match first{how}):")]
    for n, hit in enumerate(hits, 1):
        what = hit["etype"] + (f" ({hit['kind']})" if hit.get("kind") else "")
        lines.append(f"{n}. run {hit['run']}, turn {hit['turn']}, {what}: {hit['snippet']}")
        for near in hit.get("around") or []:
            body = " ".join(str(near["text"]).split())
            lines.append(f"   · turn {near['turn']} {near['etype']}: {body}")
    lines.append("A past run's whole record is runs/<ts>/ — its result.md first.")
    return "\n".join(lines)
