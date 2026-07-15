"""One-shot playbooks — a fourth library doc category, alongside workflows/traits/permissions.

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

import frontmatter
import yaml

MAIN = "MAIN.md"
# The front-matter keys that define a playbook, in the order MAIN.md writes them. `when` is the
# one-line catalog entry (== the doc's description); `axis` is the generalization axis.
FRONT_KEYS = ("slug", "title", "when", "tags", "axis", "updated")


def playbooks_dir(home: Path) -> Path:
    """The playbooks/ subdir of the library repo root."""
    return home / "playbooks"


def _parse(text: str) -> tuple[dict, str]:
    """frontmatter.parse for user-editable files: broken YAML reads as no frontmatter, so a bad
    edit never crashes listing or a run.
    """
    try:
        return frontmatter.parse(text)
    except yaml.YAMLError:
        return {}, text


def doc_body(text: str) -> str:
    """MAIN.md without its front matter — the reusable brief that seeds a conversation."""
    return _parse(text)[1]


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
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
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
    main = playbooks_dir(home) / slug / MAIN
    if not main.is_file():
        return None
    text = main.read_text(encoding="utf-8")
    meta, body = _parse(text)
    sub = playbooks_dir(home) / slug
    details = {p.name: p.read_text(encoding="utf-8")
               for p in sorted(sub.glob("*.md")) if p.name != MAIN}
    return {"slug": slug, "content": text, "body": body.strip(), "meta": meta, "details": details}


def read_detail(home: Path, slug: str, name: str) -> str | None:
    """One detail file's text (bare filename only — no path traversal)."""
    if "/" in name or ".." in name:
        return None
    p = playbooks_dir(home) / slug / name
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
    """
    sub = playbooks_dir(home) / slug
    sub.mkdir(parents=True, exist_ok=True)
    (sub / MAIN).write_text(main.rstrip() + "\n", encoding="utf-8")
    if details is None:
        return
    keep = {MAIN}
    for name, body in details.items():
        fn = _safe_detail_name(name)
        (sub / fn).write_text(body.rstrip() + "\n", encoding="utf-8")
        keep.add(fn)
    for p in sub.glob("*.md"):
        if p.name not in keep:
            p.unlink()


def delete_playbook(home: Path, slug: str) -> bool:
    sub = playbooks_dir(home) / slug
    if not sub.is_dir():
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
