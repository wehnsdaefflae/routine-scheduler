"""SETTINGS TEMPLATES — a named starting point for a routine's whole conduct surface.

A routine's setup is five separate decisions (held conduct docs, the capability mapping, secret
exposure, filesystem roots, bound rules) that are almost never made independently. Reading the
28 live routines, they cluster hard: eight rules are held by two thirds of them, `memory` +
`util-authoring` + `util-revision` by nearly all, and the differences fall into a handful of
recognisable JOBS — something that watches, something that writes to people, something that
tends a project, something that acts on files and machines, something that maintains the
instance itself. Configuring each of those five layers per routine was busywork that produced
the same answer most of the time and hid the interesting differences in the noise.

A template is a PRESELECTION, not a layer (operator decision 2026-08-30, reversing 0.262.0's
layering). Adopting one COPIES its values into the routine's own `routine.yaml`, once, and the
routine owns them from that moment: lists union, maps fill only what the routine left unset —
the same rules the group merge uses, applied as a WRITE instead of as an inheritance.

Layering was tried first and read badly. A routine's own file recorded only its DIFFERENCES from
its template, so opening `routine.yaml` told you almost nothing about what the routine could do;
the page had to explain a second inheritance chain stacked on the group's; and `template_except:`
existed purely to subtract from a layer nobody could see. The cost of copying is the leverage —
editing a template no longer reaches its adopters — which is the correct trade for a
STARTING POINT. A live shared config is what a GROUP is, and that layer stays.

So a template has no runtime existence: nothing resolves one at config load, and no field on
`RoutineConfig` names one. It is read by creation (`workflows.scaffold`) and by the routine
page's adopt action, and nowhere else.
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
    """The config a routine adopting `slug` would copy in — empty for an unknown template.

    Unknown is deliberately not an error: a caller naming a template the library has lost gets
    nothing copied rather than a failure, and the adopt route says so.
    """
    if not slug:
        return {}
    rec = read_template(libraries_home, slug)
    if rec is None:
        log.warning("routine references unknown template %r", slug)
        return {}
    return rec["config"]


def adopt_into(raw: dict, config: dict) -> tuple[dict, list[str]]:
    """Copy a template's `config` into a routine's raw `routine.yaml` mapping, once.

    Returns `(merged, added)` where `added` names, in plain words, what the write CONTRIBUTED —
    the routine page shows it back, because an adoption that silently changed nine things is
    the layer's illegibility in a different costume.

    The merge rules are the group merge's, which is deliberate: union for the list keys, fill
    per key for the maps, the routine's own value always winning. What differs is only that the
    result is WRITTEN. `grants` is never copied — a grant is a settled decision a person made
    about one routine, and a template pre-answering one would be a template exposing a secret.
    """
    from .config.groupconfig import apply_group_config

    shareable = {k: v for k, v in (config or {}).items() if k != "grants"}
    merged, provenance = apply_group_config(raw, shareable, source="the template")
    return merged, [f"{note.split(' from ')[0]} {key}"
                    for key, note in sorted(provenance.items())]


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
