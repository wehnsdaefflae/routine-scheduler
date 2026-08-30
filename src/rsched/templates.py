"""SETTINGS TEMPLATES — a named starting point for a routine's whole conduct surface.

A routine's setup is five separate decisions (held conduct docs, the capability mapping, secret
exposure, filesystem roots, bound rules) that are almost never made independently. Reading the
28 live routines, they cluster hard: eight rules are held by two thirds of them, `memory` +
`util-authoring` + `util-revision` by nearly all, and the differences fall into a handful of
recognisable JOBS — something that watches, something that writes to people, something that
tends a project, something that acts on files and machines, something that maintains the
instance itself. Configuring each of those five layers per routine was busywork that produced
the same answer most of the time and hid the interesting differences in the noise.

A template carries the same keys a GROUP's shared config carries (`groups.CONFIG_KEYS`), and
layers under it:

    the routine's own routine.yaml   >   its group's config   >   its template

Each layer only fills what the one above left unset, with the identical union/merge rules the
group merge already uses — so adopting a template SUBTRACTS nothing. Every field stays editable
per routine, and a routine that overrides half of one is a perfectly ordinary routine.

Templates live in the library beside rules and permissions, so they are versioned, shared and
editable in one place, and a revision reaches every adopter at its next run — the same leverage,
and the same hazard, which is why `library_impact` treats them like any other library document.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import library_docs

log = logging.getLogger("rsched.templates")

TEMPLATES_SUBDIR = "templates"


def templates_home(libraries_home: Path) -> Path:
    return Path(libraries_home) / TEMPLATES_SUBDIR


def list_templates(libraries_home: Path) -> list[dict]:
    """Every template: `{slug, title, summary, tags, config, body}`, slug-sorted."""
    home = templates_home(libraries_home)
    if not home.is_dir():
        return []
    out = []
    for path in sorted(home.glob("*.md")):
        rec = read_template(libraries_home, path.stem)
        if rec is not None:
            out.append(rec)
    return out


def read_template(libraries_home: Path, slug: str) -> dict | None:
    raw = library_docs.read_doc(templates_home(libraries_home), slug)
    if raw is None:
        return None
    meta, body = library_docs.parse_lenient(raw)
    m = library_docs.DOC_RE.search(raw)
    return {"slug": slug,
            "title": slug.replace("-", " ").capitalize(),
            "summary": (m.group("summary").strip() if m else ""),
            "tags": list(meta.get("tags") or []),
            "config": normalize_config(meta.get("config")), "body": body}


def normalize_config(raw: object) -> dict:
    """The shared-config half of a template, restricted to the keys a GROUP may share.

    One vocabulary for both layers on purpose: a template that could carry a key a group
    cannot would make "where do I set this?" a question with two answers.
    """
    from .groups import CONFIG_KEYS

    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in CONFIG_KEYS and v not in (None, "", [], {})}


def config_for(libraries_home: Path, slug: str) -> dict:
    """The config a routine adopting `slug` inherits — empty for an unknown template.

    Unknown is deliberately not an error: a routine naming a template the library has lost
    should keep running on its own config, not fail to load. `rsched validate` reports it.
    """
    if not slug:
        return {}
    rec = read_template(libraries_home, slug)
    if rec is None:
        log.warning("routine references unknown template %r", slug)
        return {}
    return rec["config"]
