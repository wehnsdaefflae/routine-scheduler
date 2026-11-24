"""The retrieval BACKENDS under comparison — lexical (the baseline) and embeddings.

The experiment's whole point is a head-to-head, so the two backends must be interchangeable
behind one call and must see *identical* inputs. Hence one protocol (`rank`) and one shared
notion of a hit.

**lexical** is deliberately the engine's own arithmetic, not a better lexical scorer: it reuses
`engine/recall.py`'s stopwords-and-overlap shape, because the question is whether embeddings beat
WHAT IS RUNNING TODAY. Making the baseline stronger than production would answer a question
nobody asked.

**embeddings** is config-driven: any OpenAI-compatible `/v1/embeddings` endpoint, named by URL +
key env var, so the operator can point it at a provider that actually serves embeddings without
a code change. Vectors are cached on disk keyed by (model, text hash) — a store's conditions
change rarely, so a replay over thousands of turns embeds each condition once. No network call is
made for a text already cached, which is what makes repeated replays affordable.

Cosine similarity on normalized vectors is a dot product; it is computed in pure Python because
this package must not become the repo's first numpy dependency to run its own baseline.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .stores import Condition

#: Tokens too common to carry signal — `engine/recall.py`'s list, kept in step deliberately so
#: the baseline is the engine's own behaviour rather than a variant of it.
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "at", "by",
    "is", "it", "this", "that", "what", "when", "read", "file", "files", "run", "turn", "md",
    "state", "util", "write", "json", "py", "self", "new", "get", "set", "how", "was", "were",
})
MIN_TOKEN = 3

#: The embeddings HTTP timeout. A replay makes many calls; a hung provider must not wedge a
#: page — but a LOCAL model is far slower than a cloud one: a 64-text batch against omen's
#: local-model-server exceeded 30s, and the gateway's own `local-embed` provider allows 870s.
#: So the default is generous and both knobs are env-tunable, because the right values differ
#: by an order of magnitude between a cloud endpoint and a laptop GPU.
EMBED_TIMEOUT_S = int(os.environ.get("RSCHED_PLAYGROUND_EMBED_TIMEOUT_S") or 300)
#: How many texts go in one embeddings request.
EMBED_BATCH = int(os.environ.get("RSCHED_PLAYGROUND_EMBED_BATCH") or 16)


@dataclass(frozen=True)
class Hit:
    """One ranked item, with the score the page shows and the promotion decision rests on."""

    ident: str
    store: str
    score: float
    condition: str
    owner: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ident": self.ident, "store": self.store, "score": round(self.score, 4),
                "condition": self.condition, "owner": self.owner, "detail": self.detail}


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(t) >= MIN_TOKEN and t not in STOPWORDS}


class LexicalBackend:
    """Token overlap, normalized by query size — the engine's baseline.

    Normalizing matters for the comparison: `recall.py` works on raw overlap counts because it
    ranks a fixed archive against one action, but here query lengths vary wildly between
    components (`I` is a clause, `O` can be a page), and an un-normalized score would make a
    long query score higher on every item.
    """

    name = "lexical"
    available = True

    def rank(self, query: str, items: list[Condition], limit: int) -> list[Hit]:
        q = tokens(query)
        if not q:
            return []
        out: list[Hit] = []
        for item in items:
            shared = q & tokens(item.condition)
            if not shared:
                continue
            score = len(shared) / math.sqrt(len(q))
            out.append(Hit(ident=item.ident, store=item.store, score=score,
                           condition=item.condition, owner=item.owner, detail=item.detail))
        out.sort(key=lambda h: (-h.score, h.ident))
        return out[:limit]


class EmbeddingBackend:
    """Cosine similarity over an OpenAI-compatible embeddings endpoint, cached on disk.

    `available` is False — with the reason in `why` — when the endpoint or its key is not
    configured, so the page can say what is missing instead of failing a request. Nothing here
    raises on a provider error during a batch: the error is reported and the backend reports
    itself unavailable for that call, because a half-embedded comparison is worse than none.
    """

    name = "embeddings"

    def __init__(self, cache_dir: Path, *, url: str = "", model: str = "",
                 key_env: str = "") -> None:
        # ENV WINS over the caller's value: the caller passes the instance DEFAULT (the otari
        # gateway, api_playground.OTARI_EMBED_*), and the point of the env vars is to try
        # another provider without a code change. The other way round they could never apply.
        self.url = (os.environ.get("RSCHED_PLAYGROUND_EMBED_URL") or url).strip()
        self.model = (os.environ.get("RSCHED_PLAYGROUND_EMBED_MODEL") or model).strip()
        self.key_env = (os.environ.get("RSCHED_PLAYGROUND_EMBED_KEY_ENV") or key_env).strip()
        self.cache_dir = Path(cache_dir)
        self.why = ""
        # The key is resolved env-FIRST, then from the instance secret store — the web process
        # does not carry that store in its environment, so an env-only lookup would report a
        # provisioned key as missing. Nothing here ever logs or returns the value.
        self._resolved_key = _resolve_key(self.key_env)
        if not self.url or not self.model:
            self.why = ("no embeddings endpoint configured — set an OpenAI-compatible "
                        "/v1/embeddings URL, a model and the env var holding its key")
        elif not self.url.startswith(("http://", "https://")):
            # The URL is operator-supplied config, and urllib would honour `file:` or a custom
            # scheme — which would turn this backend into a local-file reader. Only HTTP(S).
            self.why = f"embeddings URL must be http:// or https:// — got {self.url[:40]!r}"
        elif self.key_env and not self._resolved_key:
            self.why = (f"{self.key_env} is not in the environment or the instance secret "
                        f"store")
        self.available = not self.why

    # ---- cache ---------------------------------------------------------------------------

    def _cache_path(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self.model}\u0000{text}".encode()).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def _cached(self, text: str) -> list[float] | None:
        path = self._cache_path(text)
        try:
            vec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return vec if isinstance(vec, list) else None

    def _store(self, text: str, vec: list[float]) -> None:
        path = self._cache_path(text)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(vec), encoding="utf-8")
        except OSError:
            pass            # a cache miss costs a call, never the answer

    # ---- embedding -----------------------------------------------------------------------

    def _post(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model, "input": texts}).encode()
        # The S310 waivers below are earned: the scheme is validated in __init__ (http/https
        # only), which is what that rule asks for — a `file:` or custom scheme never reaches
        # here, because the backend reports itself unavailable instead.
        req = urllib.request.Request(self.url, data=body, method="POST")  # noqa: S310
        req.add_header("Content-Type", "application/json")
        if self._resolved_key:
            req.add_header("Authorization", f"Bearer {self._resolved_key}")
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        rows = payload.get("data") or []
        return [list(r.get("embedding") or []) for r in rows]

    def embed(self, texts: list[str]) -> dict[str, list[float]]:
        """Vectors for `texts`, cache first. Raises RuntimeError naming the provider failure."""
        out: dict[str, list[float]] = {}
        todo: list[str] = []
        for text in dict.fromkeys(texts):
            if (vec := self._cached(text)) is not None:
                out[text] = vec
            else:
                todo.append(text)
        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i:i + EMBED_BATCH]
            try:
                vecs = self._post(batch)
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
                raise RuntimeError(f"embeddings call failed: {exc}") from exc
            if len(vecs) != len(batch):
                raise RuntimeError(
                    f"embeddings endpoint returned {len(vecs)} vectors for {len(batch)} inputs")
            for text, vec in zip(batch, vecs, strict=True):
                normalized = _normalize(vec)
                self._store(text, normalized)
                out[text] = normalized
        return out

    def rank(self, query: str, items: list[Condition], limit: int) -> list[Hit]:
        if not query.strip() or not items:
            return []
        vectors = self.embed([query, *[i.condition for i in items]])
        qv = vectors.get(query) or []
        out: list[Hit] = []
        for item in items:
            iv = vectors.get(item.condition) or []
            if not qv or not iv:
                continue
            out.append(Hit(ident=item.ident, store=item.store, score=_dot(qv, iv),
                           condition=item.condition, owner=item.owner, detail=item.detail))
        out.sort(key=lambda h: (-h.score, h.ident))
        return out[:limit]


def _resolve_key(name: str) -> str:
    """The value of secret `name`: the process environment first, then the instance store.

    Env first so a test or a one-off export wins; the store second because the web process
    that serves the bench is not given the secrets as environment variables. Returns "" when
    the name is empty or unknown — the caller turns that into a stated reason, never a crash.
    """
    if not name:
        return ""
    if value := os.environ.get(name):
        return value
    try:
        from .. import secrets

        return str(secrets.load_secrets().get(name) or "")
    except Exception:
        return ""


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else list(vec)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
