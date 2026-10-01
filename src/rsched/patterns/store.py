"""The pattern STORE — `<library>/patterns/<slug>.yaml`, one immutable settings document each.

A pattern is created, read and deleted; it is never edited. Editing one in place would change
what "follows this pattern" means for every follower at once without any of them changing —
the silent drift a settings layer was taken apart to end (2026-08-30). A different pattern is
saved as a NEW one ("Save as new pattern" on a routine page) and routines move to it through the
same accept-the-changes step as everything else.

Deleting a pattern never touches a follower's settings: they are the routine's own. It removes
the `pattern:` reference from every follower's `routine.yaml` in the same operation, because a
reference to nothing is a routine claiming to follow something that does not exist.

A pattern never names an instance credential store among its folder grants
(`entities.guarded_roots`): creation copies a pattern's roots into the new routine past the
guard that refuses one on the routine page, so one such pattern would mount the store on every
routine made from it. `problems` names it (the lint, a refused save) and creation leaves it out
(`apply.routine_yaml`).

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

import yaml

from ..entities import guarded_roots
from ..ids import now_iso
from ..paths import atomic_write_yaml, read_yaml
from . import fields

SUBDIR = "patterns"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def home(libraries_home: Path) -> Path:
    return Path(libraries_home) / SUBDIR


def _path(libraries_home: Path, slug: str) -> Path:
    if not _SLUG_RE.fullmatch(slug or ""):
        raise ValueError(f"{slug!r} is not a pattern slug (kebab-case)")
    return home(libraries_home) / f"{slug}.yaml"


def _document(path: Path) -> tuple[object, str]:
    """The file's YAML document, or `(None, why)` when it does not parse — a library file a
    person can break by hand or a merge can leave conflicted, which is a finding to report
    and never an exception for every page that lists patterns.
    """
    try:
        return read_yaml(path, {}), ""
    except (OSError, yaml.YAMLError) as exc:
        return None, f"invalid YAML: {exc}"


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
    out.extend(f"settings.{key}: {root} is a credential store — a pattern never grants one"
               for key in ("fs_read_roots", "fs_write_roots")
               for root in guarded_roots(settings.get(key)))
    out.extend(f"asks: {ask!r} needs a known field and a question"
               for ask in doc.get("asks") or []
               if not (isinstance(ask, dict) and ask.get("field") in fields.BY_KEY
                       and str(ask.get("question") or "").strip()))
    return out


def read(libraries_home: Path, slug: str) -> dict | None:
    """The pattern `slug`, or None when there is none to use: no such file, or a file that is
    not a pattern document at all. `unusable` names those, for the library lint.
    """
    try:
        path = _path(libraries_home, slug)
    except ValueError:
        return None
    if not path.is_file():
        return None
    doc, _why = _document(path)
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


def _files(libraries_home: Path) -> list[Path]:
    d = home(libraries_home)
    return sorted(d.glob("*.yaml")) if d.is_dir() else []


def list_all(libraries_home: Path) -> list[dict]:
    """Every usable pattern. A file `read` cannot use is left out here and named by
    `unusable` instead.
    """
    return [p for p in (read(libraries_home, f.stem) for f in _files(libraries_home)) if p]


def unusable(libraries_home: Path) -> dict[str, str]:
    """File name → why, for each file in the pattern directory that is not a pattern `read`
    can use: what `list_all` leaves out, named so `rsched lint` reports it rather than nobody.
    """
    out: dict[str, str] = {}
    for f in _files(libraries_home):
        if not _SLUG_RE.fullmatch(f.stem):
            out[f.name] = "the file name is not a pattern slug (kebab-case)"
            continue
        doc, why = _document(f)
        if why or not isinstance(doc, dict):
            out[f.name] = why or "; ".join(problems(doc))
    return out


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


def exists(libraries_home: Path, slug: str) -> bool:
    """A pattern FILE by that slug, whether or not `read` can use it — a file broken by hand or
    by a merge is still the operator's to delete, and asking `read` hid it from that too.
    """
    try:
        return _path(libraries_home, slug).is_file()
    except ValueError:
        return False


def delete(libraries_home: Path, slug: str) -> bool:
    path = _path(libraries_home, slug)
    if not path.is_file():
        return False
    path.unlink()
    return True


def followers(routines_home: Path, slug: str) -> list[str]:
    """Routines whose `routine.yaml` names this pattern — read from the files, because the
    reference lives there and nowhere else. A file that does not parse names nothing anyone
    can read (its own loader reports it), so it is passed over rather than failing the list.
    """
    out = []
    for cfg in sorted(Path(routines_home).glob("*/routine.yaml")):
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError):
            continue
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
