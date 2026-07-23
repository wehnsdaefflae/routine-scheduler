"""Library markdown docs — shared access to the two per-document library sets:

- **traits** (`<library>/traits/`): reusable practice prose. Selected at routine creation,
  ADAPTED to the routine and copied into `<routine>/traits/`; from then on they are the
  routine's own files (the routine may refine them; the user edits them like any routine
  file). The library copy is only the template.
- **permissions** (`<library>/permissions/`): conduct docs of the two-layer permission set.
  Activation lives in routine.yaml `permissions:` (user-only); the frontmatter `requires:`
  of the LIBRARY copy declares which capabilities the doc's instructions presume (see
  grants.py — enforcement reads the routine's own `capabilities:`). Bodies are short
  capability notes shown in the UI and appended to the prompt's CAPABILITIES section when
  active.

Both are one markdown file per doc with a `# trait: <name> — <summary>` /
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
DOC_RE = re.compile(r"^#\s*(?:trait|permission):\s*(?P<slug>.+?)\s*—\s*(?P<summary>.+)$",
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
    """traits/ and permissions/ live in the library repo — the repo itself is managed by
    utils_lib.ensure_library; here we only make sure the directory exists.
    """
    home.mkdir(parents=True, exist_ok=True)


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
