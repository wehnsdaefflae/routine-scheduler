"""The pattern STORE — `<library>/patterns/<slug>.yaml`, one immutable settings document each.

A pattern is created, read and deleted; it is never edited. Editing one in place would change
what "follows this pattern" means for every follower at once without any of them changing —
the silent drift a settings layer was taken apart to end (2026-08-30). A different pattern is
saved as a NEW one ("Save as new pattern" on a routine page) and routines move to it through the
same accept-the-changes step as everything else.

Deleting a pattern never touches a follower's settings: they are the routine's own. It removes
the `pattern:` reference from every follower's `routine.yaml` in the same operation, because a
reference to nothing is a routine claiming to follow something that does not exist.

File shape:

    title: Watch and digest
    summary: <one line — what a routine on this pattern does>
    workflow: <the abstract workflow slug it is designed around>
    when: <when to pick it — what the recommender and the creation flow match on>
    asks:            # what creation must settle with the person for a routine on it
      - {field: fs_read_roots, question: Which folders does it read?}
    settings: {<field>: <value>, ...}   # only governed fields; see fields.GOVERNABLE
    created: <iso>
    from_routine: <slug>                # when saved from a routine page
"""

from __future__ import annotations

import re
from pathlib import Path

from ..ids import is_slug, now_iso
from ..paths import atomic_write_yaml, read_yaml
from . import fields

SUBDIR = "patterns"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def home(libraries_home: Path) -> Path:
    return Path(libraries_home) / SUBDIR


def _path(libraries_home: Path, slug: str) -> Path:
    if not _SLUG_RE.fullmatch(slug or "") or not is_slug(slug):
        raise ValueError(f"{slug!r} is not a pattern slug (kebab-case)")
    return home(libraries_home) / f"{slug}.yaml"


def problems(doc: object) -> list[str]:
    """What is wrong with a pattern document, as sentences. Empty when it is sound."""
    if not isinstance(doc, dict):
        return ["a pattern is a mapping"]
    out = [f"{key} is required" for key in ("title", "summary", "workflow")
           if not str(doc.get(key) or "").strip()]
    settings = doc.get("settings")
    if not isinstance(settings, dict) or not settings:
        out.append("settings must name at least one field")
        return out
    for key in settings:
        if key not in fields.BY_KEY:
            out.append(f"settings.{key}: not a setting")
        elif not fields.BY_KEY[key].governable:
            out.append(f"settings.{key}: a pattern never carries a routine's own {key}")
    out.extend(f"asks: {ask!r} needs a known field and a question"
               for ask in doc.get("asks") or []
               if not (isinstance(ask, dict) and ask.get("field") in fields.BY_KEY
                       and str(ask.get("question") or "").strip()))
    return out


def read(libraries_home: Path, slug: str) -> dict | None:
    try:
        path = _path(libraries_home, slug)
    except ValueError:
        return None
    if not path.is_file():
        return None
    doc = read_yaml(path, {})
    if not isinstance(doc, dict):
        return None
    settings = {k: fields.canonical(k, v) for k, v in (doc.get("settings") or {}).items()
                if k in fields.BY_KEY and fields.BY_KEY[k].governable}
    return {"slug": slug, "title": str(doc.get("title") or slug),
            "summary": str(doc.get("summary") or ""), "workflow": str(doc.get("workflow") or ""),
            "when": str(doc.get("when") or ""), "asks": list(doc.get("asks") or []),
            "settings": settings, "created": str(doc.get("created") or ""),
            "from_routine": str(doc.get("from_routine") or ""),
            "problems": problems(doc)}


def list_all(libraries_home: Path) -> list[dict]:
    d = home(libraries_home)
    if not d.is_dir():
        return []
    return [p for p in (read(libraries_home, f.stem) for f in sorted(d.glob("*.yaml"))) if p]


def create(libraries_home: Path, slug: str, doc: dict) -> dict:
    """Write a NEW pattern. Refuses an existing slug — patterns are immutable — and a document
    with problems. Returns the stored pattern. The caller commits the library.
    """
    path = _path(libraries_home, slug)
    if path.exists():
        raise FileExistsError(f"a pattern named {slug!r} already exists")
    body = {"title": str(doc.get("title") or "").strip(),
            "summary": str(doc.get("summary") or "").strip(),
            "workflow": str(doc.get("workflow") or "").strip(),
            "when": str(doc.get("when") or "").strip(),
            **({"asks": list(doc["asks"])} if doc.get("asks") else {}),
            "settings": {k: fields.canonical(k, v) for k, v in (doc.get("settings") or {})
                         .items() if k in fields.BY_KEY},
            "created": now_iso(),
            **({"from_routine": doc["from_routine"]} if doc.get("from_routine") else {})}
    if found := problems(body):
        raise ValueError("; ".join(found))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_yaml(path, body)
    stored = read(libraries_home, slug)
    if stored is None:
        raise OSError(f"the pattern {slug!r} was written but does not read back")
    return stored


def delete(libraries_home: Path, slug: str) -> bool:
    path = _path(libraries_home, slug)
    if not path.is_file():
        return False
    path.unlink()
    return True


def followers(routines_home: Path, slug: str) -> list[str]:
    """Routines whose `routine.yaml` names this pattern — read from the files, because the
    reference lives there and nowhere else.
    """
    out = []
    for cfg in sorted(Path(routines_home).glob("*/routine.yaml")):
        raw = read_yaml(cfg, {})
        if isinstance(raw, dict) and raw.get("pattern") == slug:
            out.append(cfg.parent.name)
    return out


def unique_slug(libraries_home: Path, title: str) -> str:
    """A free slug for a new pattern titled `title`."""
    from ..ids import slugify

    base = slugify(title, default="pattern")[:50].strip("-") or "pattern"
    slug, n = base, 2
    while (home(libraries_home) / f"{slug}.yaml").exists():
        slug, n = f"{base}-{n}", n + 1
    return slug
