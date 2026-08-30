"""Library markdown docs — shared access to the two per-document library sets:

- **rules** (`<library>/rules/`): general rules — principle prose a run applies to its own
  case. There is exactly ONE copy: a routine holds SLUGS (routine.yaml `rules:`) and reads
  the prose on demand (`read_rule`), so a revision here reaches every holder at once. Nothing
  copies them anywhere; every run is read-only to them.
- **permissions** (`<library>/permissions/`): conduct docs of the two-layer permission set.
  Activation lives in routine.yaml `permissions:` (user-only); the frontmatter `requires:`
  of the LIBRARY copy declares which capabilities the doc's instructions presume (see
  grants.py — enforcement reads the routine's own `capabilities:`). Bodies are short
  capability notes shown in the UI and appended to the prompt's CAPABILITIES section when
  active.

Both are one markdown file per doc with a `# rule: <name> — <summary>` /
`# permission: <name> — <summary>` heading line.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import frontmatter
import yaml

from . import libgit
from .paths import atomic_write

# Title before the em-dash may be a kebab slug OR a readable phrase ("ask policy"); the summary is
# whatever follows the em-dash. (Splitting on a bare hyphen would swallow hyphens inside a slug.)
DOC_RE = re.compile(r"^#\s*(?:rule|permission|template):\s*(?P<slug>.+?)\s*—\s*(?P<summary>.+)$",
                    re.MULTILINE)


def parse_lenient(text: str) -> tuple[dict, str]:
    """frontmatter.parse for user-editable files: broken YAML reads as no frontmatter, so a
    bad edit never crashes a run or the listing. The ONE lenient parser — playbooks and
    grants import it rather than growing their own copies.
    """
    try:
        return frontmatter.parse(text)
    except yaml.YAMLError:
        return {}, text


_parse = parse_lenient   # module-internal alias (call sites below predate the export)


def doc_body(raw: str) -> str:
    """The document text without frontmatter — what the UI shows and prompts inline."""
    return _parse(raw)[1]


def ensure_dir(home: Path) -> None:
    """rules/ and permissions/ live in the library repo — the repo itself is managed by
    utils_lib.ensure_library; here we only make sure the directory exists.
    """
    home.mkdir(parents=True, exist_ok=True)


# `with`/`without` rather than `on`/`off`: YAML 1.1 reads a bare `on:` key as the BOOLEAN true,
# so an author hand-editing a doc on the Library tab would silently produce a key nothing reads.
# The UI still labels the two sides "on" and "off" — that is the toggle's language, not YAML's.
EFFECT_FIELDS = ("with", "without", "when")


def _effect(raw: object) -> dict[str, str]:
    """`{on, off, when}` as strings — missing keys read as empty rather than absent, so the
    page renders the gap instead of a hole and the linter is the thing that fails on it.
    """
    src = raw if isinstance(raw, dict) else {}
    return {k: str(src.get(k) or "").strip() for k in EFFECT_FIELDS}


def list_docs(home: Path) -> list[dict]:
    from .grants import normalize_capabilities

    if not home.is_dir():
        return []
    out = []
    for path in sorted(home.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, _ = _parse(text)
        m = DOC_RE.search(text)
        out.append({"slug": path.stem,
                    "summary": (m.group("summary").strip() if m else ""),
                    # The three things a person deciding this toggle needs (operator,
                    # 2026-08-30): what the routine does WITH it, what it does WITHOUT it, and
                    # when to hold it. The title cannot be any of them — it names a topic
                    # ("ask policy — when and how to involve the user"), and the doc BODY is
                    # written to the run in the imperative, which is not a description for the
                    # person choosing. `on`/`off` make the toggle a comparison instead of a
                    # label; `when` answers the actual question, which is whether it is for
                    # THIS routine.
                    "effect": _effect(meta.get("effect")),
                    "title": _title(path.stem),
                    "tags": meta.get("tags") or [],
                    # the capabilities this doc's instructions presume (permissions dir only)
                    "requires": normalize_capabilities(meta.get("requires"), label="requires",
                                                       requires=True)[0]})
    return out


def _title(slug: str) -> str:
    # a short human label, e.g. "run-history" → "Run history"
    return slug.replace("-", " ").replace("_", " ").capitalize()


def slugs(home: Path) -> list[str]:
    return [d["slug"] for d in list_docs(home)]


def read_doc(home: Path, slug: str) -> str | None:
    p = home / f"{slug}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_doc(home: Path, slug: str, content: str) -> None:
    atomic_write(home / f"{slug}.md", content)


def git_commit(home: Path, message: str, *, paths: Sequence[str] | None = None) -> bool:
    """Commit a doc change under the shared library-repo lock (see libgit.commit); `paths`
    (relative to `home`, e.g. `<slug>.md`) scopes the stage so a concurrent writer's commit
    can't sweep it.
    """
    return libgit.commit(home, message, paths=paths)


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    return libgit.git_log(home, rel_path, limit)
