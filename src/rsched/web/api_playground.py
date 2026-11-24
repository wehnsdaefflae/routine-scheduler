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


def _embedding_backend(server) -> EmbeddingBackend:
    return EmbeddingBackend(_cache_dir(server))


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
        try:
            out["results"]["embeddings"] = [h.as_dict() for h in
                                            embed.rank(query, items, BENCH_LIMIT)]
        except RuntimeError as exc:
            out["results"]["embeddings"] = None
            out["embeddings_why"] = str(exc)
    return out
