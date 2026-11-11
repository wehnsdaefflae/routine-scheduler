"""A path a SOURCE file's prose points at must exist.

`test_doc_claims.py` already proves this for every guide under `docs/`. Nothing proved it for
the prose inside the code, and that is where it broke: `web/assetskew.py` spent its existence
telling the reader that the skew verdict is rendered by `static/components/notices.js` — a file
this repo has never had. The renderer is `skewbanner.js`. Found by a read-only cruft sweep
(2026-11-11), not by a test, which is the gap this module closes.

Why it matters more than a typo: a docstring's job here is to hand the next reader the other
half of a mechanism that spans the server and the console. A pointer to a file that does not
exist costs that reader the search, and then costs them their confidence in the paragraph
around it — which is the paragraph explaining why the design is the way it is.

The check is deliberately narrow, because a wide one would be noise:
  * only a path in BACKTICKS — prose names modules bare ("see assetskew") and that is fine;
  * only a path with a DIRECTORY, so a bare `foo.py` or a filename in running text is ignored;
  * resolved against the five rootings this tree's prose actually uses, the same set
    `test_doc_claims.py` resolves against (repo, package, package namespace, static, tests);
  * `docs/designs.md` is the one allowed exception, by the same reasoning that excuses it
    there: it is the decided-work queue, so its entries name code that does not exist yet.

At the time of writing: 385 citations across 556 source files, zero missing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The rootings this tree's prose uses for a relative path — repo root, the package, the
#: package's own namespace (`engine/actions.py`), `static` (`views/run.js`) and `tests`.
ROOTS = ("", "src/rsched/", "src/", "static/", "tests/")

#: A backticked path carrying a directory, in one of the namespaces this repo owns. Anchoring on
#: the leading segment is what keeps a foreign path (`/etc/hosts`, a URL fragment) out.
PATH_RE = re.compile(
    r"`((?:src|static|tests|deploy|docs|config|engine|web|daemon|hooks|readmodels|endpoints"
    r"|views|components|cli|gatekit)/[\w./-]+\.(?:py|js|css|md|html|json|jsonl|toml|yaml|yml))`"
)

#: Allowed to name what does not exist, with the reason — never a silent exemption.
MAY_NAME_THE_UNBUILT = {
    "docs/designs.md": "the decided-work queue: its entries name code that does not exist yet",
}


def _resolves(rel: str) -> bool:
    return any((REPO / (root + rel)).exists() for root in ROOTS)


def _citations(text: str) -> list[str]:
    return [m.group(1) for m in PATH_RE.finditer(text)]


def test_every_path_a_source_docstring_names_exists() -> None:
    missing: list[str] = []
    seen = 0
    for path in sorted(REPO.glob("src/**/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not this test's subject
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Module | ast.ClassDef
                              | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            for cited in _citations(doc):
                seen += 1
                if cited in MAY_NAME_THE_UNBUILT or _resolves(cited):
                    continue
                missing.append(f"{path.relative_to(REPO)} names `{cited}`, which does not exist")
    assert not missing, (
        f"{len(missing)} of {seen} path(s) cited in a source docstring do not exist:\n  "
        + "\n  ".join(missing))
    assert seen > 100, (
        f"only {seen} citations found — the pattern has stopped matching this tree's prose, "
        "which would make this test pass by seeing nothing")


def test_every_path_a_console_comment_names_exists() -> None:
    """The same for `static/`, where the defect's sibling would live: the console's modules
    cross-reference each other and the server by path, and nothing checked those either.
    """
    missing: list[str] = []
    seen = 0
    for path in sorted(REPO.glob("static/**/*.js")):
        for cited in _citations(path.read_text(encoding="utf-8")):
            seen += 1
            if cited in MAY_NAME_THE_UNBUILT or _resolves(cited):
                continue
            missing.append(f"{path.relative_to(REPO)} names `{cited}`, which does not exist")
    assert not missing, (
        f"{len(missing)} of {seen} path(s) cited in a console comment do not exist:\n  "
        + "\n  ".join(missing))


def test_the_checker_catches_a_path_that_does_not_exist() -> None:
    """The check above passes on a clean tree, so by itself it cannot tell a working detector
    from a dead pattern. This pins the detector on the real defect's own text.
    """
    broken = "the console renders it (`static/components/notices.js`)."
    assert _citations(broken) == ["static/components/notices.js"]
    assert not _resolves("static/components/notices.js")
    assert _resolves("static/components/skewbanner.js")      # what it should have said
    assert _resolves("components/skewbanner.js")             # and via the `static/` rooting
    assert _citations("see assetskew for the comparison") == []   # a bare module name is fine
    assert _citations("`foo.py`") == []                           # no directory: ignored
