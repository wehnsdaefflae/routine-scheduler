"""Post-creation trait management — adding a library trait to an EXISTING routine or
conversation, and removing one.

Distinct from `workflows/scaffold.py`, which materializes the INITIAL selection at creation
time (where the generator ADAPTS each trait to the task). A later add copies the library
text VERBATIM: the curated set is written to be generally applicable, and an adaptation
call would put an LLM round-trip between the user flipping a switch and the trait taking
effect. The file in `<dir>/traits/` is the routine's own from that moment on, exactly as
if it had been selected at creation.

The traits/ DIRECTORY is the source of truth for what a routine practises; main.md's
`## Standing practices` tail is a derived index, rebuilt from the directory on every change
(`sync_practices_tail`) so add and remove need no special-casing and a hand-edited tail
converges back. This is the one place that convergence lives.
"""

from __future__ import annotations

from pathlib import Path

from . import library_docs
from .ids import is_slug
from .workflows.scaffold import PRACTICES_HEADING

_TAIL_LEAD = ("These practice modules are this routine's own standards — read each with "
              "read_file before the situation it governs (the routine-improver meta routine "
              "refines them over time):")


def current_traits(routine_dir: Path) -> list[str]:
    """The slugs this routine actually practises — the traits/ directory IS the state."""
    d = routine_dir / "traits"
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def trait_summary(body: str, slug: str) -> str:
    m = library_docs.DOC_RE.search(body)
    return m.group("summary").strip() if m else slug.replace("-", " ")


def sync_practices_tail(routine_dir: Path) -> None:
    """Rewrite main.md's Standing practices tail to match the traits/ directory. Everything
    from the heading to the end of the file is the derived index, so it is replaced wholesale;
    a routine with no traits loses the section entirely.
    """
    main = routine_dir / "main.md"
    if not main.is_file():
        return
    body = main.read_text(encoding="utf-8")
    head = body
    for line in body.splitlines():
        if line.strip().lower() == PRACTICES_HEADING.lower():
            head = body[:body.index(line)]
            break
    lines = []
    for slug in current_traits(routine_dir):
        raw = (routine_dir / "traits" / f"{slug}.md").read_text(encoding="utf-8")
        lines.append(f"- `traits/{slug}.md` — {trait_summary(raw, slug)}")
    if not lines:
        main.write_text(head.rstrip() + "\n", encoding="utf-8")
        return
    tail = [PRACTICES_HEADING, "", _TAIL_LEAD, *lines]
    main.write_text(head.rstrip() + "\n\n" + "\n".join(tail) + "\n", encoding="utf-8")


def add_trait(traits_home: Path, routine_dir: Path, slug: str) -> str:
    """Copy one library trait into <routine_dir>/traits/ verbatim and resync the tail.
    Returns the body written. Raises KeyError when the library has no such trait — the
    caller turns that into a 400 (web) or an observation error (a run consulting one).
    """
    if not is_slug(slug):
        raise KeyError(slug)
    raw = library_docs.read_doc(traits_home, slug)
    if raw is None:
        raise KeyError(slug)
    body = library_docs.doc_body(raw).strip()
    if not body:
        raise KeyError(slug)
    (routine_dir / "traits").mkdir(parents=True, exist_ok=True)
    (routine_dir / "traits" / f"{slug}.md").write_text(body + "\n", encoding="utf-8")
    sync_practices_tail(routine_dir)
    return body


def remove_trait(routine_dir: Path, slug: str) -> bool:
    """Delete one of the routine's trait files and resync the tail. False when absent.
    The library copy is untouched — this only ends THIS routine's practice of it.
    """
    if not is_slug(slug):
        return False
    p = routine_dir / "traits" / f"{slug}.md"
    if not p.is_file():
        return False
    p.unlink()
    sync_practices_tail(routine_dir)
    return True


def apply_changes(traits_home: Path, routine_dir: Path, add: list[str],
                  remove: list[str]) -> tuple[list[str], list[str]]:
    """One picker submission: add then remove, tail resynced once per mutation. Returns
    (added, removed) — the slugs that actually changed, so the caller can report honestly
    and skip the git commit when nothing did.
    """
    added = []
    for slug in add:
        if slug in current_traits(routine_dir):
            continue
        add_trait(traits_home, routine_dir, slug)      # KeyError → caller's 400
        added.append(slug)
    removed = [slug for slug in remove if remove_trait(routine_dir, slug)]
    return added, removed
