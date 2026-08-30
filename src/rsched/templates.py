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


def suggest(libraries_home: Path, permissions: list[str], rules: list[str] | None = None) -> str:
    """The template that best fits a requested permission set — deterministically.

    Creation already knows what the routine asked for (the clarify flow's preselection, the
    workflow's `includes`); the question is only which named starting point that set is closest
    to. So this is a fit, not a judgement, and it costs no model call: a wrong guess from an
    LLM here would be a wrong DEFAULT written into a config file, which is worse than a
    slightly-too-narrow one the user widens on the page.

    Scoring rewards coverage and penalises excess, so a template that supplies something the
    routine did not ask for has to earn it by covering more. Ties go to the NARROWER template —
    adding a capability later is a click, taking one back after a run has used it is a
    conversation. Nothing fitting means `basic`, the floor.
    """
    available = list_templates(libraries_home)
    if not available:
        return ""          # a library with no templates must not get a dangling reference
    want = set(permissions or [])
    want_rules = set(rules or [])
    best, best_score = "basic", 0
    for tpl in available:
        conf = tpl["config"]
        perms = set(conf.get("permissions") or [])
        covered = len(want & perms)
        excess = len(perms - want)
        score = covered * 2 - excess + len(want_rules & set(conf.get("rules") or []))
        if score > best_score or (score == best_score and score > 0
                                  and len(perms) < len(_perms_of(libraries_home, best))):
            best, best_score = tpl["slug"], score
    return best if any(t["slug"] == best for t in available) else available[0]["slug"]


def _perms_of(libraries_home: Path, slug: str) -> set[str]:
    rec = read_template(libraries_home, slug)
    return set((rec or {}).get("config", {}).get("permissions") or [])
