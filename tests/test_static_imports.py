"""Static JS md-helper imports are consistent — a call to md()/mdInline() must import it.

Guards the ReferenceError class where a view uses a shared markdown helper from
/static/md.js without importing it. conversations.js shipped `mdInline()` unimported,
raising "ReferenceError: mdInline is not defined" in the console whenever a deferred
question was rendered (surfaced by .ui-traces, 2026-07-16 self-audit). The console is
no-build vanilla ES modules, so nothing but the browser catches a missing import —
this test is that catch.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
MD_MODULE = "/static/md.js"
HELPERS = ("mdInline", "md")


def _js_files():
    # md.js itself DEFINES the helpers; every other module must import to use them.
    return sorted(p for p in STATIC.rglob("*.js") if p.name != "md.js")


def _imported_from_md(text: str) -> set[str]:
    names: set[str] = set()
    pattern = r'import\s*\{([^}]*)\}\s*from\s*"' + re.escape(MD_MODULE) + r'"'
    for m in re.finditer(pattern, text):
        for part in m.group(1).split(","):
            part = part.strip()
            if part:
                names.add(part)
    return names


def _calls(text: str, name: str) -> bool:
    # `name(` not preceded by an identifier char or a dot (so `cmd(` / `foo.md(` don't match).
    return re.search(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"\s*\(", text) is not None


def test_md_helpers_are_imported_where_used():
    problems = []
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        imported = _imported_from_md(text)
        for name in HELPERS:
            if _calls(text, name) and name not in imported:
                rel = path.relative_to(STATIC.parent)
                problems.append(f"{rel}: calls {name}() but never imports it from {MD_MODULE}")
    assert not problems, "unimported md-helper usage (ReferenceError at runtime):\n" + "\n".join(problems)
