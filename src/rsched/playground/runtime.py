"""The one function the ENGINE calls — `at_observation`; everything else here serves it.

`engine/loop.EngineLoop._tails` asks this once per observation. The contract is deliberately
narrow, because this is experimental code inside the live process:

* it returns a string, "" meaning "add nothing" (production behaviour, byte-identical);
* it NEVER raises — every failure path returns "" and is logged as a firing with an `error`,
  so a broken experiment degrades to today's behaviour instead of ending a conversation;
* it touches no shared store: the index is built per run and cached in memory, firings are
  written beside the run.

The cooldown and the floor score live here rather than in the feature declarations because they
are properties of the CHANNEL — the observation tail is scarce attention shared with budget
warnings and rule assists, and a layer that fires on every turn spends attention the other
tails need (the hazard this whole experiment exists to measure).
"""

from __future__ import annotations

import logging

from .features import (
    COOLDOWN,
    MANUAL_KINDS,
    enabled_features,
    log_firing,
    min_score,
    query_text,
)

log = logging.getLogger("rsched.playground")


def configure(loop) -> None:
    """Per-run state for this layer. Called from the loop's own configure seam."""
    loop._pg_after = {}        # feature slug → the turn before which it may not fire again
    loop._pg_index = None      # the conditions, loaded once per run
    loop._pg_standing = set()  # features whose standing instruction was already appended


def at_observation(loop, action: dict, obs: dict) -> str:
    """What the playground features add to THIS observation — "" for a production run.

    Wrapped whole in a guard: the engine must not learn new failure modes from an experiment.
    """
    try:
        return _at_observation(loop, action, obs)
    except Exception as exc:
        log.warning("playground layer suppressed: %s", exc, exc_info=exc)
        try:
            log_firing(loop.ctx.run_dir, {"error": str(exc), "turn": loop.ctx.turn})
        except Exception:
            pass
        return ""


def _at_observation(loop, action: dict, obs: dict) -> str:
    features = enabled_features(loop.ctx.routine)
    if not features:
        return ""
    if getattr(loop, "_pg_after", None) is None:
        configure(loop)
    text = ""
    for feature in features:
        if feature.slug == "intent-field":
            text += _standing(loop, feature)
            continue
        if feature.slug == "util-hint" and str(action.get("kind")) not in MANUAL_KINDS:
            continue
        if loop.ctx.turn < loop._pg_after.get(feature.slug, 0):
            continue
        text += _hint(loop, feature, action, obs)
    return text


def _standing(loop, feature) -> str:
    """A once-per-run instruction, appended the first time the feature is reached."""
    if feature.slug in loop._pg_standing:
        return ""
    loop._pg_standing.add(feature.slug)
    log_firing(loop.ctx.run_dir, {"feature": feature.slug, "turn": loop.ctx.turn,
                                  "kind": "standing"})
    return ("\n[PLAYGROUND · intent-field] From here on, end your `say` with one clause on its "
            "own line beginning `INTENT:` — what you are trying to accomplish NEXT, not what "
            "this action did. It is read as the query for the helper layer, so keep it free of "
            "reflection on the result.")


def _hint(loop, feature, action: dict, obs: dict) -> str:
    """One retrieval firing: query, floor, cooldown, log, render."""
    query = query_text(feature.components, action, obs)
    if not query.strip():
        return ""
    hits = _rank(loop, feature, query)
    if not hits:
        return ""
    top = hits[0]
    floor = min_score(loop.ctx.routine)
    if top.score < floor:
        log_firing(loop.ctx.run_dir, {"feature": feature.slug, "turn": loop.ctx.turn,
                                      "kind": "below_floor", "query": query[:400],
                                      "floor": floor, "best": top.as_dict()})
        return ""
    loop._pg_after[feature.slug] = loop.ctx.turn + COOLDOWN
    log_firing(loop.ctx.run_dir, {"feature": feature.slug, "turn": loop.ctx.turn,
                                 "kind": "fired", "query": query[:400],
                                 "components": feature.components, "hit": top.as_dict(),
                                 "runner_up": hits[1].as_dict() if len(hits) > 1 else None})
    return _render(feature, top)


def _render(feature, hit) -> str:
    if feature.slug == "util-hint":
        return (f"\n[PLAYGROUND · util-hint] You just did this by hand. `{hit.ident}` may "
                f"already cover it: {hit.detail or hit.condition[:200]}. "
                f'`util name=list args=["{hit.ident}"]` shows its exact usage. '
                f"Ignore this if it does not fit — it is a suggestion from an experiment.")
    if feature.slug == "memory-hint":
        owner = f" (in {hit.owner})" if hit.owner else ""
        return (f"\n[PLAYGROUND · memory-hint] A note of yours may bear on this: "
                f"`{hit.ident}`{owner} — {hit.detail or hit.condition[:200]}. "
                f'`memory_read name="{hit.ident}"` opens it.')
    return ""


def _rank(loop, feature, query: str):
    """Rank the feature's stores, loading the index once per run.

    Lexical only, deliberately: a live turn must not wait on a network call to a provider. The
    embeddings comparison ran on the console subpage, where latency was the operator's to spend
    rather than a conversation's — and it went when that subpage did (F959).
    """
    from .scoring import LexicalBackend
    from .stores import load_all

    if loop._pg_index is None:
        server = loop.ctx.server
        loop._pg_index = load_all(server.libraries_home, server.routines_home)
    items = [c for c in loop._pg_index if c.store in feature.stores]
    return LexicalBackend().rank(query, items, limit=3)
