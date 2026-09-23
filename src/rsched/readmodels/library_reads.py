"""The library's parsed documents, read once per CHANGE instead of once per request.

Every permission doc, rule doc and util header is a file a git commit rewrites, and the
whole-library lint is an AST pass over the same tree — so the package's stat-fingerprint
memo is the whole of what this module adds, and the functions below are the ONE way the
web layer reaches those parsers.

What it costs to read them per request was measured on the live instance (2026-09-22
slow-request ring): `GET /api/domains` parsed the permission library twice PER DOMAIN —
12 walks and ~300 frontmatter parses for six domains, 3.7-4.3 s a call — and
`GET /api/library` re-linted every workflow, rule, permission, template, reminder and
playbook and re-parsed 110 util headers on every call, 3.7-4.3 s. Both are fetched when
any routine page opens, where a dozen CPU-bound handlers then serialize under the GIL and
a one-file read beside them takes 3.2 s. CLAUDE.md's 2026-09-12 rule removed those two
from the bus-event refetch path; this makes them cheap wherever they are still called.

Results are `memoized_shared` — the record lists are large and a deep copy per request
would cost what the memo saves — so every caller treats what it gets back as IMMUTABLE
and builds new dicts rather than editing these.
"""

from __future__ import annotations

from pathlib import Path

from . import memo


def docs(home: Path) -> list[dict]:
    """`library_docs.list_docs(home)` — every conduct/rule doc in a docs home as a record
    (summary, effect, title, requires, assists), reparsed only when the directory changes.
    """
    from ..library_docs import list_docs

    return memo.memoized_shared(f"library-docs:{home}", memo.tree_paths(home, "*.md"),
                                lambda: list_docs(home))


def doc_slugs(home: Path) -> list[str]:
    """The slugs alone — `library_docs.slugs`, off the same memo."""
    return [d["slug"] for d in docs(home)]


def requires(permissions_home: Path) -> dict[str, dict]:
    """`grants.read_library_requires` — slug → the capabilities each permission doc's
    instructions presume, the docs↔capabilities dependency map the floor reads.
    """
    from ..grants import read_library_requires

    return memo.memoized_shared(f"library-requires:{permissions_home}",
                                memo.tree_paths(permissions_home, "*.md"),
                                lambda: read_library_requires(permissions_home))


def assists(rules_home: Path):
    """`assists.read_library_assists` — every assist every library rule declares."""
    from ..assists import read_library_assists

    return memo.memoized_shared(f"library-assists:{rules_home}",
                                memo.tree_paths(rules_home, "*.md"),
                                lambda: read_library_assists(rules_home))


def utils(libraries_home: Path) -> list[dict]:
    """`utils_lib.list_utils` — every global util's parsed header (110 of them live)."""
    from ..utils_lib import list_utils

    return memo.memoized_shared(f"library-utils:{libraries_home}",
                                memo.tree_paths(libraries_home / "utils", "*/main.py"),
                                lambda: list_utils(libraries_home))


def lint(libraries_home: Path) -> dict[str, list[str]]:
    """`workflows.lint.lint_all` — path-relative-name → problems for the whole library."""
    from ..workflows.lint import lint_all

    return memo.memoized_shared(f"library-lint:{libraries_home}",
                                _lint_sources(libraries_home),
                                lambda: lint_all(libraries_home))


def _lint_sources(home: Path) -> list[Path]:
    """Every file `lint_all` reads — the six kinds it walks, each with its own dir, so a
    doc added to any of them invalidates the verdict for all of them.
    """
    from .. import playbooks, reminders, templates
    from ..workflows import library

    return [*memo.tree_paths(library.workflows_dir(home), "*.py"),
            *memo.tree_paths(library.rules_dir(home), "*.md"),
            *memo.tree_paths(library.permissions_dir(home), "*.md"),
            *memo.tree_paths(templates.templates_home(home), "*.md"),
            *memo.tree_paths(reminders.reminders_home(home), "*.json"),
            *memo.tree_paths(playbooks.playbooks_dir(home), "*/MAIN.md")]
