"""The Playground API — experimental conversations and the retrieval bench (the operator's order).

The Playground is a Conversations-shaped surface for features that are not ready for production:
a playground conversation is an ordinary conversation (same engine, same workflow) with
`playground: true` and a list of experiment slugs in its `routine.yaml`. It is listed here
instead of on Conversations, and it is the ONLY kind of run the engine's playground seam does
anything for — every production run gets "" from it.

Four surfaces, which is the whole page:

* `GET /playground/features` — the experiments on offer, each with what it claims and which
  query recipe it uses, plus whether an embeddings backend is configured.
* `GET|POST /playground/conversations` — list the playground ones / create one with features.
* `GET /playground/firings/{slug}` — what the layer actually surfaced in that conversation's
  runs, read from the per-run `playground-firings.jsonl`. This is the precision evidence: real
  turns, not a bench.
* `POST /playground/bench` — the interactive comparison: one situation, every store, lexical
  and embeddings side by side. Embeddings run HERE and never in a conversation's turn, because
  a live turn must not wait on a provider.

Promotion is deliberately not an endpoint. The page carries a verdict note the operator writes;
moving a feature into production is a code change someone makes on purpose.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:       # the store is imported lazily in `_store` (web boot stays light)
    from ..playground.vectorstore import VectorStore

from fastapi import APIRouter, HTTPException, Request

from ..config import load_routine
from ..paths import atomic_write_yaml, read_yaml
from ..playground import COMPONENT_LABELS, COMPONENTS, STORES
from ..playground.features import FEATURES, MIN_SCORE
from ..playground.scoring import EmbeddingBackend, LexicalBackend
from ..playground.stores import load_all

log = logging.getLogger("rsched.playground.api")

router = APIRouter(tags=["playground"])

#: Where the bench caches embedding vectors — under the routines control dir, beside the other
#: daemon-owned caches, never inside a routine's own directory.
CACHE_SUBDIR = ".control/playground-embed-cache"

#: How many hits a bench query returns per store.
BENCH_LIMIT = 8


def _server(request: Request):
    return request.app.state.server


def _cache_dir(server) -> Path:
    return Path(server.routines_home) / CACHE_SUBDIR


#: The instance's own OpenAI-compatible gateway (docs/model-gateway.md): the `otari-openai`
#: endpoint's base plus `/embeddings`, keyed by the same `OTARI_API_KEY` every otari endpoint
#: uses. Used when the three RSCHED_PLAYGROUND_EMBED_* env vars name nothing, so the bench has
#: a working backend on this instance without anything to export; the env vars still override
#: it, which is how another provider is tried without a code change.
OTARI_EMBED_URL = "http://172.17.0.1:4000/api/v1/embeddings"
#: The embedding model, named as the gateway names it. The instance serves TWO (measured
#: 2026-11: `qwen3-embedding-8b` and `qwen3-embedding-8b-local`, the only embedders among its
#: 809 models), and they share an index — tag-fs measured cosine min 0.9921 / median 0.9973
#: between them, which is why it treats `-local` as the private-file substitute for the same
#: vectors.
#:
#: The LOCAL one is the default (operator's choice): omen's local-model-server serves it
#: through decision-balancer's EMBED_BACKENDS pool, so no condition text leaves the network to
#: be indexed. Measured against its cloud sibling on the same pair — cosine 0.7256 local vs
#: 0.7221 cloud, both 4096 dims — which matches tag-fs's own finding that the two agree to a
#: median cosine of 0.9973 and may share an index.
#:
#: A model switch needs no code change (RSCHED_PLAYGROUND_EMBED_MODEL) and cannot corrupt the
#: index: the store's keys carry the model, so switching re-embeds rather than comparing
#: vectors from two spaces.
OTARI_EMBED_MODEL = "qwen3-embedding-8b-local"
#: The cloud sibling — the fallback when omen's embedder is down (the gateway answers 502 for
#: the local id then), and the model tag-fs uses by default.
OTARI_EMBED_MODEL_CLOUD = "qwen3-embedding-8b"
OTARI_KEY_ENV = "OTARI_API_KEY"


def _embedding_backend(server) -> EmbeddingBackend:
    """The embeddings backend, defaulting to the instance's otari gateway."""
    return EmbeddingBackend(_cache_dir(server), url=OTARI_EMBED_URL,
                            model=OTARI_EMBED_MODEL, key_env=OTARI_KEY_ENV)


@router.get("/playground/features")
def features(request: Request) -> dict:
    """The experiments on offer, the component vocabulary, and backend readiness."""
    server = _server(request)
    embed = _embedding_backend(server)
    return {
        "features": [{"slug": f.slug, "title": f.title, "about": f.about,
                      "components": f.components, "stores": list(f.stores)}
                     for f in FEATURES.values()],
        "components": [{"id": c, "label": COMPONENT_LABELS[c]} for c in COMPONENTS],
        "stores": list(STORES),
        "min_score": MIN_SCORE,
        "backends": [{"name": "lexical", "available": True, "why": ""},
                     {"name": "embeddings", "available": embed.available, "why": embed.why}],
    }


def _conversation_dirs(server) -> list[Path]:
    home = Path(server.conversations_home)
    return sorted(p for p in home.glob("*/routine.yaml")) if home.is_dir() else []


@router.get("/playground/conversations")
def list_playground(request: Request) -> dict:
    """Every playground conversation, newest first, with its enabled experiments."""
    server = _server(request)
    rows = []
    for cfg_path in _conversation_dirs(server):
        raw = read_yaml(cfg_path, {})
        block = raw.get("playground")
        if not isinstance(block, dict) or not block.get("on"):
            continue
        slug = cfg_path.parent.name
        runs = sorted((cfg_path.parent / "runs").glob("*/")) \
            if (cfg_path.parent / "runs").is_dir() else []
        rows.append({"slug": slug, "name": raw.get("name") or slug,
                     "features": list(block.get("features") or []),
                     "runs": len(runs),
                     "last_run": runs[-1].name if runs else "",
                     "firings": _count_firings(cfg_path.parent)})
    rows.sort(key=lambda r: r["last_run"], reverse=True)
    return {"conversations": rows}


def _count_firings(conv_dir: Path) -> int:
    total = 0
    runs = conv_dir / "runs"
    if not runs.is_dir():
        return 0
    for path in runs.glob("*/playground-firings.jsonl"):
        try:
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
        except OSError:
            continue
    return total


@router.post("/playground/conversations")
async def create_playground(request: Request) -> dict:
    """Create a playground conversation: an ordinary one, then stamped with its experiments.

    Reuses `conversations.create_conversation` wholesale rather than forking it — an experiment
    must run on the SAME engine path as production or it tests something else.
    """
    from .. import conversations

    body = await request.json()
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "a first message is required")
    wanted = [s for s in (body.get("features") or []) if s in FEATURES]
    server = _server(request)
    slug = conversations.new_slug(server.conversations_home)
    conv_dir = conversations.create_conversation(server, slug=slug, first_message=message)
    # STAMP BEFORE FIRING: the engine reads routine.yaml at boot, so a flag written after the
    # fire would leave reply #1 running as an ordinary conversation — the one reply the
    # operator is most likely to judge the experiment by.
    cfg_path = conv_dir / "routine.yaml"
    raw = read_yaml(cfg_path, {})
    block: dict = {"on": True, "features": wanted}
    try:            # an out-of-range or unparsable floor falls back to the module default
        floor = float(body.get("min_score"))
        if 0 < floor <= 100:
            block["min_score"] = floor
    except (TypeError, ValueError):
        pass
    raw["playground"] = block
    atomic_write_yaml(cfg_path, raw)
    cfg, _ = load_routine(conv_dir)
    rid = await request.app.state.runner.fire(cfg, reason="playground")
    if rid is None:
        shutil.rmtree(conv_dir, ignore_errors=True)
        raise HTTPException(503, "the daemon is restarting — try again in a moment")
    log.info("playground conversation %s created with features %s", slug, wanted)
    return {"slug": slug, "features": wanted, "run_id": rid}


@router.get("/playground/firings/{slug}")
def firings(request: Request, slug: str, limit: int = 200) -> dict:
    """Every firing this conversation's runs recorded — the precision evidence.

    A `fired` row is a line the run was actually shown. A `below_floor` row is a query whose
    best hit did NOT clear the floor: kept deliberately, because the near-misses are how the
    floor gets calibrated, and a layer that only logs its successes cannot be judged.
    """
    server = _server(request)
    conv_dir = Path(server.conversations_home) / slug
    if not (conv_dir / "routine.yaml").is_file():
        raise HTTPException(404, f"no conversation {slug!r}")
    rows: list[dict] = []
    runs = conv_dir / "runs"
    if runs.is_dir():
        for path in sorted(runs.glob("*/playground-firings.jsonl")):
            run_ts = path.parent.name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    rows.append({"run": run_ts, **json.loads(line)})
                except ValueError:
                    continue
    rows.sort(key=lambda r: (str(r.get("run")), int(r.get("turn") or 0)))
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[str(row.get("kind") or "?")] = kinds.get(str(row.get("kind") or "?"), 0) + 1
    return {"slug": slug, "count": len(rows), "by_kind": kinds, "firings": rows[-limit:]}


def _store(server) -> VectorStore:
    """The persisted vector index for the configured model."""
    from ..playground.vectorstore import VectorStore

    return VectorStore(_cache_dir(server) / "index", _embedding_backend(server).model)


@router.get("/playground/vectors")
def vectors(request: Request) -> dict:
    """The vector index's state: how many conditions are embedded, and how many are missing.

    The honest read for a page whose whole point is being checkable — `missing` is what a sync
    would cost in provider calls, and `held` is what a query can actually reach.
    """
    server = _server(request)
    backend = _embedding_backend(server)
    conditions = load_all(server.libraries_home, server.routines_home)
    store = _store(server)
    held = store.load()
    # The LOCAL substitute is named here, with its own index's state, because switching to it
    # is the operator's choice and the honest answer to "is it usable" is how many vectors it
    # already holds — a model switch re-embeds (the keys carry the model), so the cost of
    # switching is a fact about this store, not a guess.
    from ..playground.vectorstore import VectorStore

    other = VectorStore(_cache_dir(server) / "index", OTARI_EMBED_MODEL_CLOUD)
    return {"model": backend.model, "backend_available": backend.available,
            "why": backend.why, "conditions": len(conditions), "held": held,
            "missing": len(store.missing(conditions)), "dim": store.dim(),
            "path": str(store.path),
            "alt_model": OTARI_EMBED_MODEL_CLOUD, "alt_held": other.load(),
            "alt_hint": ("the cloud sibling, already indexed — RSCHED_PLAYGROUND_EMBED_MODEL "
                         "switches to it if omen's embedder goes down")}


@router.post("/playground/vectors/sync")
def sync_vectors(request: Request) -> dict:
    """Embed every condition the index is missing, and prune the ones that no longer exist.

    Idempotent: a second call with nothing changed makes no provider call at all, because the
    index is keyed by (model, condition text) — see `playground/vectorstore.py`.
    """
    server = _server(request)
    backend = _embedding_backend(server)
    if not backend.available:
        raise HTTPException(503, backend.why)
    conditions = load_all(server.libraries_home, server.routines_home)
    store = _store(server)
    try:
        report = store.sync(conditions, backend.embed)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    report["pruned"] = store.prune(conditions)
    return report


@router.post("/playground/bench")
async def bench(request: Request) -> dict:
    """One situation, every store, both backends — the interactive comparison.

    The query is assembled from whichever components the caller filled in, so the page can ask
    the thread's actual question: does `A+O` beat `I` for catching work done by hand, and does
    an embedding index see anything token overlap misses on the SAME input?
    """
    body = await request.json()
    parts = {c: str(body.get(c) or "").strip() for c in COMPONENTS}
    chosen = [c for c in COMPONENTS if parts.get(c)]
    if not chosen:
        raise HTTPException(422, "fill in at least one component")
    query = " ".join(parts[c] for c in chosen)
    wanted_stores = [s for s in (body.get("stores") or STORES) if s in STORES]
    server = _server(request)
    items = [c for c in load_all(server.libraries_home, server.routines_home)
             if c.store in wanted_stores]
    out: dict = {"query": query, "components": chosen, "indexed": len(items), "results": {}}
    out["results"]["lexical"] = [h.as_dict() for h in
                                 LexicalBackend().rank(query, items, BENCH_LIMIT)]
    embed = _embedding_backend(server)
    if not embed.available:
        out["results"]["embeddings"] = None
        out["embeddings_why"] = embed.why
    else:
        # Search the PERSISTED index: one embedding call for the query, then an exact scan of
        # stored vectors. Re-embedding all 774 conditions per bench query would be the thing
        # the store exists to avoid.
        try:
            store = _store(server)
            store.load()
            qvec = embed.embed([query]).get(query) or []
            hits = store.search(qvec, stores=wanted_stores, limit=BENCH_LIMIT)
            out["results"]["embeddings"] = [
                {"ident": r.ident, "store": r.store, "score": round(score, 4),
                 "condition": r.condition, "owner": r.owner, "detail": r.detail}
                for score, r in hits]
            out["indexed_vectors"] = store.count(wanted_stores)
            if not hits and not store.count(wanted_stores):
                out["embeddings_why"] = ("the vector index is empty for these stores — POST "
                                         "/api/playground/vectors/sync to build it")
        except RuntimeError as exc:
            out["results"]["embeddings"] = None
            out["embeddings_why"] = str(exc)
    return out
