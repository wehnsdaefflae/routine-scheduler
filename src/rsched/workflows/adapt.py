"""Turn a single-file library workflow into a routine.

- `materialize`: the un-decomposed baseline — the whole workflow, rendered to markdown, becomes
  the routine's main.md. Used for sub-routines and as a fallback.
- `decompose`: the generator LLM applies the workflow to the initial instruction and splits it
  into the routine's entry (main.md) + one markdown STAGE per step/state of the workflow. Runs
  as a PIPELINE of scoped completions — outline → main → one call per stage — so no single
  completion carries the whole routine (the 2026-07-24 one-shot truncation shipped
  stageless/stub routines twice in one day). Falls back to `materialize` on failure.

  General RULES are NOT decomposed: a rule is general by construction, lives once in the
  library, and the run applies it to its own case. Creation only records which slugs the
  routine holds.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from .library import head_commit, read_workflow
from .pipeline import _pipeline


def dump_markdown(meta: dict, body: str) -> str:
    r"""Meta + markdown body → the main.md document: '---\n<yaml>\n---\n\n<body>' with key
    order preserved and exactly one trailing newline. The single writer-side counterpart of
    frontmatter.parse, so materialized files always round-trip.
    """
    post = frontmatter.Post(body.strip())
    post.metadata = dict(meta)
    return frontmatter.dumps(post, sort_keys=False) + "\n"


def _routine_frontmatter(meta: dict, slug: str, provenance: dict) -> dict:
    """The keys a materialized main.md carries. Only `materialized_from` and `tools` are READ
    back (runtime.load_workflow); `name`/`slug` stay as the human identity of a file the user
    edits in the recipe editor. Anything else here would be a second copy of a fact whose
    source of truth is elsewhere — the `stages/` directory, routine.yaml — and
    would silently drift from it.
    """
    fm = {"name": meta.get("name", slug), "slug": meta.get("slug", slug),
          "materialized_from": provenance}
    if meta.get("tools") is not None:
        fm["tools"] = meta["tools"]
    return fm


def materialize(home: Path, slug: str) -> tuple[str, dict]:
    """Single-file workflow → the routine's main.md content (whole workflow rendered to markdown).
    The Python pattern is rendered to markdown — the orchestrator acts it out.
    """
    from .pyworkflow import render_markdown

    meta, raw = read_workflow(home, slug)
    provenance = {"slug": slug, "commit": head_commit(home), "version": meta.get("version", 0)}
    return dump_markdown(_routine_frontmatter(meta, slug, provenance),
                         render_markdown(raw, meta)), provenance


log = logging.getLogger("rsched.adapt")


def decompose(server, slug: str, instruction: str, *,
              rules: list[str] | None = None) -> dict:
    """Generator LLM: apply a single-file workflow to `instruction` and split it into the
    routine's main.md body + stage/state modules. Returns {'main': <body>,
    'stages': {name: body}, 'degraded': bool}.

    Runs as a pipeline of SCOPED completions (outline → main → one per stage), each
    retried DECOMPOSE_ATTEMPTS times over transport errors and invalid payloads — the one-shot
    design shipped stub routines whenever its single huge completion truncated (D41,
    2026-07-24). Any hard failure degrades to the whole workflow rendered as main.md with
    `degraded` True so callers can SAY so.
    """
    from .. import rules as rules_mod

    meta, raw = read_workflow(server.libraries_home, slug)
    # A pattern may PIN deliverable paths (META["pin"]: str | list) that MUST survive
    # decomposition — the tailored files must still name them. The observed failure mode:
    # applied to an instruction that itself describes a routine, the generator sometimes
    # builds THAT routine and silently drops the pattern's real deliverable. A dropped pin
    # falls back to the verbatim pattern, which always keeps it. No shipped pattern pins a
    # path today; the mechanism stays because the failure it guards is generator behaviour,
    # not one pattern's quirk.
    pins = [meta["pin"]] if isinstance(meta.get("pin"), str) else list(meta.get("pin") or [])
    # rules reach the generator as an INDEX only (slug + summary): main.md must route to
    # them, and must not paraphrase prose that lives in the library.
    rule_lines = "\n".join(f"- {slug_}: {summary}" for slug_, summary
                           in rules_mod.summaries(server.rules_home, list(rules or [])).items())
    try:
        from ..endpoints import EndpointRegistry

        return _pipeline(EndpointRegistry(server).for_system, raw, instruction,
                         pins=pins, rule_lines=rule_lines, slug=slug)
    except Exception as exc:
        # a stageless recipe is a real quality drop — the fallback must never be silent
        log.warning("decompose(%s) pipeline failed — materializing the whole pattern as "
                    "main.md", slug, exc_info=exc)
        from .pyworkflow import render_markdown
        return {"main": render_markdown(raw, meta),
                "stages": {},
                "degraded": True, "reason": f"{type(exc).__name__}: {exc}"}
