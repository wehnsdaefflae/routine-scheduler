"""The VECTOR STORE — the embedded conditions, persisted, with cosine search over them.

A real store rather than a cache, because the two answer different questions. The cache in
`scoring.EmbeddingBackend` answers "what is the vector for this text"; the store answers "which
stored items are nearest this query, right now, without re-embedding the library". That second
one is what a push channel on every turn needs.

## Why this shape, and not a vector database

The index is **774 conditions** on this instance (56 utils, ~445 memory notes, ~248 reminders,
25 rules). At that size an exact scan over a contiguous float array is the correct algorithm:
774 × 384 floats is ~1.2 MB, a full scan is ~300k multiply-adds, and `array`+`zip` does it in
single-digit milliseconds. An ANN index (faiss, hnswlib, qdrant) buys nothing until the corpus
is orders of magnitude larger — it would add a dependency, a build step and an approximation
error to a search that is already exact and fast. The store is written so the swap is local:
`search()` is the only place that scans, so an ANN backend replaces one method.

## What is persisted, and why it is keyed by content

`vectors.jsonl` holds one record per condition — `{key, store, ident, owner, detail, condition,
dim, vec}` — where `key` is a hash of (model, condition text). Keying by CONTENT, not by the
item's name, is what makes the store self-maintaining:

* a util whose summary is edited gets a new key, so it is re-embedded;
* a note that has not changed keeps its key, so it is never re-embedded;
* a deleted item simply stops being asked for, and `prune()` drops its row.

So `sync()` is idempotent and costs exactly one embedding call per *changed* condition. The
model id is part of the key because vectors from two models are not comparable — switching
models re-embeds rather than silently mixing spaces.

One file, appended and rewritten whole on prune: at this size that is simpler than a database
and leaves the store readable with `head`, which matters for a surface whose whole purpose is
letting someone check what it did.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path

from .stores import Condition

#: The store's filename inside its directory.
VECTORS_FILE = "vectors.jsonl"

#: How many conditions are embedded per provider call when syncing.
SYNC_BATCH = 64


def condition_key(model: str, text: str) -> str:
    """The content address of one condition under one model."""
    return hashlib.sha256(f"{model}\u0000{text}".encode()).hexdigest()


@dataclass
class Row:
    """One embedded condition, as the store holds it in memory."""

    key: str
    store: str
    ident: str
    condition: str
    owner: str
    detail: str
    vec: array           # float32, L2-normalized at write time

    def as_record(self) -> dict:
        return {"key": self.key, "store": self.store, "ident": self.ident,
                "owner": self.owner, "detail": self.detail, "condition": self.condition,
                "dim": len(self.vec), "vec": [round(float(x), 6) for x in self.vec]}


class VectorStore:
    """The persisted vectors for one embedding model, with exact cosine search.

    Thread-safe for the web process: a bench request and a background sync can touch it at
    once, and the lock is held only around the in-memory swap, never around a provider call.
    """

    def __init__(self, directory: Path, model: str) -> None:
        self.dir = Path(directory)
        self.model = model
        self._rows: dict[str, Row] = {}
        self._lock = threading.Lock()
        self._loaded = False

    # ---- persistence ---------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """One file per MODEL: vectors from two models share no space, so they share no file."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.model)
        return self.dir / safe / VECTORS_FILE

    def load(self) -> int:
        """Read the store from disk. A malformed line is skipped, never fatal — the file is a
        cache of embeddings and a torn write must not take the page down with it.
        """
        rows: dict[str, Row] = {}
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                rows[str(rec["key"])] = Row(
                    key=str(rec["key"]), store=str(rec.get("store") or ""),
                    ident=str(rec.get("ident") or ""), condition=str(rec.get("condition") or ""),
                    owner=str(rec.get("owner") or ""), detail=str(rec.get("detail") or ""),
                    vec=array("f", [float(x) for x in rec["vec"]]))
            except (ValueError, KeyError, TypeError):
                continue
        with self._lock:
            self._rows = rows
            self._loaded = True
        return len(rows)

    def _append(self, rows: list[Row]) -> None:
        if not rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row.as_record(), ensure_ascii=False) + "\n")

    def rewrite(self) -> None:
        """Rewrite the file from memory (after a prune), atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with self._lock:
            rows = list(self._rows.values())
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row.as_record(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    # ---- maintenance ---------------------------------------------------------------------

    def missing(self, conditions: list[Condition]) -> list[Condition]:
        """The conditions this store has no vector for — what a sync would have to embed."""
        if not self._loaded:
            self.load()
        with self._lock:
            have = set(self._rows)
        out, seen = [], set()
        for cond in conditions:
            key = condition_key(self.model, cond.condition)
            if key in have or key in seen or not cond.condition.strip():
                continue
            seen.add(key)
            out.append(cond)
        return out

    def sync(self, conditions: list[Condition], embed) -> dict:
        """Embed and store every condition this store is missing.

        `embed` is `EmbeddingBackend.embed` (text -> vector); it is called in batches and
        OUTSIDE the lock. Returns a report the page can show: how many were already held, how
        many were added, and the dimension.
        """
        if not self._loaded:
            self.load()
        todo = self.missing(conditions)
        added: list[Row] = []
        for i in range(0, len(todo), SYNC_BATCH):
            batch = todo[i:i + SYNC_BATCH]
            vectors = embed([c.condition for c in batch])
            for cond in batch:
                vec = vectors.get(cond.condition)
                if not vec:
                    continue
                added.append(Row(
                    key=condition_key(self.model, cond.condition), store=cond.store,
                    ident=cond.ident, condition=cond.condition, owner=cond.owner,
                    detail=cond.detail, vec=array("f", _normalize(vec))))
        with self._lock:
            for row in added:
                self._rows[row.key] = row
            total = len(self._rows)
        self._append(added)
        return {"model": self.model, "held": total, "added": len(added),
                "missing_before": len(todo),
                "dim": len(added[0].vec) if added else self.dim()}

    def prune(self, conditions: list[Condition]) -> int:
        """Drop vectors for conditions that no longer exist, and rewrite the file."""
        if not self._loaded:
            self.load()
        live = {condition_key(self.model, c.condition) for c in conditions}
        with self._lock:
            dead = [k for k in self._rows if k not in live]
            for key in dead:
                del self._rows[key]
        if dead:
            self.rewrite()
        return len(dead)

    # ---- search --------------------------------------------------------------------------

    def dim(self) -> int:
        with self._lock:
            for row in self._rows.values():
                return len(row.vec)
        return 0

    def count(self, stores: list[str] | None = None) -> int:
        with self._lock:
            if stores is None:
                return len(self._rows)
            return sum(1 for r in self._rows.values() if r.store in stores)

    def search(self, query_vec: list[float], *, stores: list[str] | None = None,
               limit: int = 8) -> list[tuple[float, Row]]:
        """Exact cosine search — the ONE place that scans, so an ANN index replaces it alone.

        The query is normalized here, and every stored vector was normalized at write time, so
        cosine similarity is a plain dot product.
        """
        if not self._loaded:
            self.load()
        q = array("f", _normalize(query_vec))
        if not len(q):
            return []
        with self._lock:
            rows = [r for r in self._rows.values()
                    if stores is None or r.store in stores]
        scored: list[tuple[float, Row]] = []
        for row in rows:
            if len(row.vec) != len(q):
                continue        # a vector from another model or a truncated row
            scored.append((_dot(q, row.vec), row))
        scored.sort(key=lambda pair: (-pair[0], pair[1].ident))
        return scored[:limit]


def _normalize(vec) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec))
    return [float(x) / norm for x in vec] if norm else [float(x) for x in vec]


def _dot(a, b) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b, strict=False))
