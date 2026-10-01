"""One-shot playbooks — a fourth library doc category, alongside workflows/rules/permissions.

A **playbook** is a saved, generalized conversation BRIEF (NOT a control-flow workflow): the
proven spec of a kind of work, captured from a finished conversation and reused to seed a new
one. It mirrors the save-instruction / use-instruction pattern — an always-loaded `MAIN.md`
(front matter + Parameters/Instructions/Notes) plus optional on-demand detail files, each
playbook living in its own subfolder `<library>/playbooks/<slug>/`. The catalog is derived live
from each MAIN.md's front matter (there is no index file); the whole set is git-versioned and
synced with the rest of the library (`git add -A` at the repo root already covers it).

Distinct from a routine's "recipe" (its materialized main.md): a playbook is a library template,
never executed. Git lives at the library ROOT — use workflows.library.git_commit / head_commit /
git_log for commits (this module is pure storage).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from .ids import is_slug
from .paths import atomic_write

MAIN = "MAIN.md"
# The front-matter keys that define a playbook, in the order MAIN.md writes them. `when` is the
# one-line catalog entry (== the doc's description); `axis` is the generalization axis.
FRONT_KEYS = ("slug", "title", "when", "tags", "axis", "updated")


def playbooks_dir(home: Path) -> Path:
    """The playbooks/ subdir of the library repo root."""
    return home / "playbooks"


def _playbook_dir(home: Path, slug: str) -> Path | None:
    """`<library>/playbooks/<slug>`, or None when `slug` is not a slug.

    The ONE gate between a caller's slug and the filesystem. The web routes pass the URL
    segment straight through, and `%2E%2E` arrives as `..` — `playbooks/..` is the library
    ROOT, so a delete of it was an rmtree of the whole library repo, history included. Every
    playbook slug is minted by `ids.slugify`, so the slug alphabet refuses nothing real.
    """
    return playbooks_dir(home) / slug if is_slug(slug) else None


def _parse(text: str) -> tuple[dict, str]:
    from .library_docs import parse_lenient
    return parse_lenient(text)


def _safe_detail_name(name: str) -> str:
    """A traversal-proof `<kebab>.md` detail filename."""
    stem = re.sub(r"[^a-z0-9-]+", "-", str(name).lower().removesuffix(".md")).strip("-") or "detail"
    return f"{stem}.md"


def list_playbooks(home: Path) -> list[dict]:
    """Catalog: one entry per `<slug>/MAIN.md`, derived live from its front matter. `summary` and
    `when` are the same one-line 'when to reuse' string (kept under both keys so the Library tab's
    generic renderer and the picker both find it).
    """
    d = playbooks_dir(home)
    if not d.is_dir():
        return []
    out = []
    # a folder whose name is no slug is nothing the store can open, so the catalog omits it
    for sub in sorted(p for p in d.iterdir() if p.is_dir() and is_slug(p.name)):
        main = sub / MAIN
        if not main.is_file():
            continue
        meta, _ = _parse(main.read_text(encoding="utf-8"))
        when = str(meta.get("when") or "").strip()
        details = sorted(p.name for p in sub.glob("*.md") if p.name != MAIN)
        out.append({"slug": sub.name,
                    "title": str(meta.get("title") or sub.name),
                    "summary": when,
                    "when": when,
                    "axis": str(meta.get("axis") or "").strip(),
                    "tags": meta.get("tags") or [],
                    "updated": str(meta.get("updated") or ""),
                    "details": details})
    return out


def slugs(home: Path) -> list[str]:
    return [p["slug"] for p in list_playbooks(home)]


def read_playbook(home: Path, slug: str) -> dict | None:
    """{slug, content (full MAIN.md), body (MAIN.md minus front matter), meta, details:{name:body}}
    or None when the playbook does not exist.
    """
    sub = _playbook_dir(home, slug)
    if sub is None or not (sub / MAIN).is_file():
        return None
    text = (sub / MAIN).read_text(encoding="utf-8")
    meta, body = _parse(text)
    details = {p.name: p.read_text(encoding="utf-8")
               for p in sorted(sub.glob("*.md")) if p.name != MAIN}
    return {"slug": slug, "content": text, "body": body.strip(), "meta": meta, "details": details}


def read_detail(home: Path, slug: str, name: str) -> str | None:
    """One detail file's text (bare filename only — no path traversal)."""
    sub = _playbook_dir(home, slug)
    if sub is None or "/" in name or ".." in name:
        return None
    p = sub / name
    return p.read_text(encoding="utf-8") if p.is_file() and p.suffix == ".md" else None


def compose_main(meta: dict, body: str) -> str:
    """Assemble a MAIN.md: our fixed front-matter key order (dropping empties), then the body."""
    ordered = {k: meta[k] for k in FRONT_KEYS if str(meta.get(k) or "").strip() or meta.get(k) == 0}
    for k, v in meta.items():   # keep any extra keys after the canonical ones
        if k not in ordered and v not in (None, ""):
            ordered[k] = v
    fm = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def write_playbook(home: Path, slug: str, *,
                   main: str, details: dict[str, str] | None = None) -> None:
    """Write `<slug>/MAIN.md` (full text, front matter included), creating the subfolder. When
    `details` is a dict, it is reconciled — files not in it are removed (so a revision drops stale
    ones); when `details` is None the existing detail files are left untouched (a MAIN-only edit).
    Raises ValueError for a `slug` that is not one.
    """
    sub = _playbook_dir(home, slug)
    if sub is None:
        raise ValueError(f"not a playbook slug: {slug!r}")
    sub.mkdir(parents=True, exist_ok=True)
    atomic_write(sub / MAIN, main.rstrip() + "\n")
    if details is None:
        return
    keep = {MAIN}
    for name, body in details.items():
        fn = _safe_detail_name(name)
        atomic_write(sub / fn, body.rstrip() + "\n")
        keep.add(fn)
    for p in sub.glob("*.md"):
        if p.name not in keep:
            p.unlink()


def delete_playbook(home: Path, slug: str) -> bool:
    sub = _playbook_dir(home, slug)
    if sub is None or not sub.is_dir():
        return False
    shutil.rmtree(sub)
    return True


def unique_slug(home: Path, base: str) -> str:
    """`base`, suffixed `-2`, `-3`, … so a Save never clobbers an existing playbook."""
    existing = set(slugs(home))
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
