"""PENDING CHANGES — settings proposed for a routine that wait for the person's accept.

One document per routine: the changes, why each one is proposed, the pattern the routine would
follow once they are accepted, and the one-line message the page leads with ("check the changes
I recommend."). Creation writes one, the page's "Recommend for this routine" writes one, and
switching pattern writes one; the page overlays it on the controls, highlighted. A single
"accept changes" applies whatever the person kept.

It lives under the routines home's `.control/`, NOT in the routine's own directory: a run can
write anywhere in its own directory; a proposal the person accepts with one click is an
authority surface — a run able to write its own pending changes could grant itself a capability
by waiting for a routine click. `.control/` is written by the web layer and the daemon only.
"""

from __future__ import annotations

from pathlib import Path

from ..ids import is_slug, now_iso
from ..paths import atomic_write_json, read_json
from . import fields

DIR = Path(".control") / "settings-drafts"


def _path(routines_home: Path, slug: str) -> Path:
    if not is_slug(slug):
        raise ValueError(f"{slug!r} is not a routine slug")
    return Path(routines_home) / DIR / f"{slug}.json"


def read(routines_home: Path, slug: str) -> dict | None:
    doc = read_json(_path(routines_home, slug))
    if not isinstance(doc, dict) or not isinstance(doc.get("changes"), dict):
        return None
    changes = {k: {"value": fields.canonical(k, v.get("value")),
                   "reason": str(v.get("reason") or "")}
               for k, v in doc["changes"].items()
               if k in fields.BY_KEY and isinstance(v, dict) and "value" in v}
    if not changes and not doc.get("pattern"):
        return None
    return {"changes": changes, "pattern": doc.get("pattern"),
            "message": str(doc.get("message") or ""), "source": str(doc.get("source") or ""),
            "reason": str(doc.get("reason") or ""), "created": str(doc.get("created") or "")}


def write(routines_home: Path, slug: str, *, changes: dict[str, dict], pattern: str | None,
          message: str, source: str, reason: str = "") -> dict:
    """Replace the routine's pending changes. `changes` is `{field: {value, reason}}`; a field
    outside the vocabulary is dropped rather than stored, because nothing could apply it.
    """
    doc = {"created": now_iso(), "source": source, "message": message, "reason": reason,
           "pattern": pattern,
           "changes": {k: {"value": fields.canonical(k, v.get("value")),
                           "reason": str(v.get("reason") or "")}
                       for k, v in changes.items() if k in fields.BY_KEY}}
    path = _path(routines_home, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, doc)
    return doc


def clear(routines_home: Path, slug: str) -> None:
    _path(routines_home, slug).unlink(missing_ok=True)


def prune(routines_home: Path, slug: str, saved: dict, assigned: str) -> dict | None:
    """Drop every pending change the saved settings already hold — a change is pending only
    while it would change something. Clears the document when nothing is left to accept: no
    field differs and the proposed pattern is the one the routine already follows.
    """
    doc = read(routines_home, slug)
    if doc is None:
        return None
    left = {k: v for k, v in doc["changes"].items() if not fields.equal(k, saved.get(k),
                                                                         v["value"])}
    if not left and doc["pattern"] in (None, assigned):
        clear(routines_home, slug)
        return None
    if left != doc["changes"]:
        return write(routines_home, slug, changes=left, pattern=doc["pattern"],
                     message=doc["message"], source=doc["source"], reason=doc["reason"])
    return doc
