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
HELPERS = ("mdInline", "md", "clampedBody", "summaryLine")


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


def _without_comments(text: str) -> str:
    """The source with comment bodies blanked out, string literals left intact.

    A module that explains WHY it renders with one helper rather than the other names the
    other one, and `md() is a superset of mdInline()` is a sentence this repo wants written
    down — but a raw scan reads it as a call and demands an import for a function the file
    never invokes. Comments are stripped rather than the name being avoided in prose: the
    check exists to catch a missing import, so it must read what the browser executes.

    Blanked, not deleted, so offsets and line numbers are unchanged. The string states are
    tracked because `"https://…"` and a regex literal both carry `//`, and treating either
    as the start of a comment would blank real code — the one direction this check must
    never fail in, since a swallowed call is a missed import.
    """
    out, i, n = [], 0, len(text)
    quote = None                      # the string/template delimiter currently open
    while i < n:
        c, nxt = text[i], text[i + 1 : i + 2]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:          # an escape consumes the next char whole
                out.append(nxt)
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
            out.append(c)
        elif c == "/" and nxt == "/":
            while i < n and text[i] != "\n":      # blank to end of line, keep the newline
                out.append(" ")
                i += 1
            continue
        elif c == "/" and nxt == "*":
            while i < n and not (text[i] == "*" and text[i + 1 : i + 2] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")                     # the closing */
            i += 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _calls(text: str, name: str) -> bool:
    # `name(` not preceded by an identifier char or a dot (so `cmd(` / `foo.md(` don't match).
    return re.search(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"\s*\(", text) is not None


def test_md_helpers_are_imported_where_used():
    problems = []
    for path in _js_files():
        source = path.read_text(encoding="utf-8")
        text = _without_comments(source)
        imported = _imported_from_md(text)
        for name in HELPERS:
            if _calls(text, name) and name not in imported:
                rel = path.relative_to(STATIC.parent)
                problems.append(f"{rel}: calls {name}() but never imports it from {MD_MODULE}")
    assert not problems, "unimported md-helper usage (ReferenceError at runtime):\n" + "\n".join(problems)


API_MODULE = "/static/api.js"
API_HELPERS = ("openStreamCount",)   # F263 stream-count gauge — used by stream.js + trace.js


def _imported_from(text: str, module: str) -> set[str]:
    names: set[str] = set()
    pattern = r'import\s*\{([^}]*)\}\s*from\s*"' + re.escape(module) + r'"'
    for m in re.finditer(pattern, text):
        for part in m.group(1).split(","):
            part = part.strip()
            if part:
                names.add(part)
    return names


def test_api_stream_gauge_is_imported_where_used():
    """F263: openStreamCount (the concurrent-EventSource gauge stamped into reconnect/freeze
    traces) is defined in api.js; any module calling it must import it — same ReferenceError
    class the md-helper guard catches. api.js itself DEFINES it, so it is excluded."""
    problems = []
    for path in _js_files():
        if path.name == "api.js":
            continue
        text = path.read_text(encoding="utf-8")
        imported = _imported_from(text, API_MODULE)
        for name in API_HELPERS:
            if _calls(text, name) and name not in imported:
                rel = path.relative_to(STATIC.parent)
                problems.append(f"{rel}: calls {name}() but never imports it from {API_MODULE}")
    assert not problems, "unimported api-helper usage (ReferenceError at runtime):\n" + "\n".join(problems)


PANEL_MODULE = "/static/views/settings-common.js"


def _panel_section_callbacks(text: str) -> list[tuple[int, str]]:
    """Every `panelSection(...)` render callback, as (offset, parameter-list).

    The call is `panelSection(view, url, skel, (box, data, reload) => {`, so the callback's
    parameter list is the last parenthesised group before the arrow. Only the arrow form is
    matched because that is the only form every section uses; a future `function (…)` callback
    would go unchecked rather than falsely flagged, which is the safe direction for a guard
    whose failure mode is a missed ReferenceError, not a blocked commit.
    """
    out = []
    for m in re.finditer(r"panelSection\s*\(", text):
        depth, i, n = 1, m.end(), len(text)
        start = i
        while i < n and depth:                      # walk to this call's closing paren
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        call = text[start : i - 1]
        arrow = re.search(r"\(([^()]*)\)\s*=>", call)
        if arrow:
            out.append((m.start(), arrow.group(1)))
    return out


def test_a_settings_section_that_reloads_declares_the_reload_it_was_handed():
    """F536: `panelSection` hands `reload` to its render callback as the THIRD argument, and a
    section that omits the parameter but calls `reload()` throws ReferenceError at runtime.

    `static/views/settings-server.js` shipped `(srvBox, s) => {` while calling `reload()` on the
    success branch of its restart watcher — so every SUCCESSFUL daemon restart from the console
    ended in `ReferenceError: reload is not defined` (.ui-traces 2026-09-23, twice in one day),
    after the toast had already told the operator it worked. Same class as the md-helper guard
    above, from the other direction: there, a name was used without being imported; here, a name
    is used without being RECEIVED.
    """
    problems = []
    for path in _js_files():
        text = _without_comments(path.read_text(encoding="utf-8"))
        if "panelSection" not in text:
            continue
        for offset, params in _panel_section_callbacks(text):
            names = [p.strip() for p in params.split(",") if p.strip()]
            if "reload" in names:
                continue
            body = text[offset:]
            end = body.find("panelSection", 1)      # this section's slice of the module
            if _calls(body[: end if end > 0 else len(body)], "reload"):
                rel = path.relative_to(STATIC.parent)
                line = text[:offset].count("\n") + 1
                problems.append(f"{rel}:{line}: the panelSection callback ({params}) calls "
                                f"reload() but never declares it — panelSection passes it as "
                                f"the third argument ({PANEL_MODULE})")
    assert not problems, ("a settings section calls a reload it was never handed "
                          "(ReferenceError at runtime):\n" + "\n".join(problems))


def test_a_helper_named_only_in_prose_is_not_a_call():
    """The check reads what the browser executes. A module that explains why it renders with
    one helper rather than the other names the other one, and that sentence is worth writing.
    """
    src = ('// md() is a superset of mdInline(), so nothing reads worse for it.\n'
           '/* mdInline() lives here too */\n'
           'import { md } from "/static/md.js";\n'
           'const u = "https://example.test/a//b";\n'
           'export const go = () => md(u);\n')
    stripped = _without_comments(src)
    assert not _calls(stripped, "mdInline")          # prose is not a call
    assert _calls(stripped, "md")                    # the real call survives
    assert "https://example.test/a//b" in stripped   # a `//` inside a string is not a comment
