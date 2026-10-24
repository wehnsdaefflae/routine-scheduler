"""What `static/` MOUNTS and what its stylesheets NAME — the extraction half of the class
contract (R2885). The rules that judge the answer live in tests/test_static_class_contract.py;
this module only reads, and is a helper like tests/render_guard.py, not a test module.

WHAT IT SEES. Class LITERALS only — `class: "…"`, `className = "…"`, and the string arguments of
`classList.add/remove/toggle` — plus the values of a map whose NAME says it holds classes
(`VERDICT_CLASS`, `STATUS_TONE`, `OUTCOME_CHIP`, `GATE_CLS`): that bare-token class-map idiom IS
the 0.399.2 defect and it recurs (itemcard.js, jobs-panel.js, runjob.js, views/routine.js). A
piece of a template literal touching a `${…}` hole is dropped, not guessed at — `chip-${k}` is
not a class token. Token-not-substring throughout: `.conv-tag` is not styled because
`.conv-tagsel` is.

THREE SETS per module, because the rules need different ones:
  mounted    — what this module really puts on an element. What gets CHECKED.
  context    — what may be on that element: `mounted`, plus a class the module names as a string
               that some stylesheet styles (an array-built list `["card", live ? "live" : ""]`
               reaches the DOM exactly like a literal), plus `chip` where it calls `chip()`,
               plus the shell's own classes from index.html.
  ancestors  — `context` unioned over the module's IMPORT CLOSURE: a component may rely on a root
               class the view that mounts it emits (dev.css styles `.outcome-panel .summons`,
               and views/changes-routine.js:236 mounts `.outcome-panel` around
               components/outcome-panel.js).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"

HOLE = "\x00"
#: a map whose NAME says its values are class names — the 0.399.2 idiom
CLASS_MAP = re.compile(r"_(?:CLASS|CLS|TONE|CHIP)$")
CLASS_ATTR = re.compile(r"""class: ([`"'])(.*?)\1""", re.DOTALL)
CLASS_PROP = re.compile(r"""className\s*=\s*([`"'])(.*?)\1""", re.DOTALL)
CLASS_LIST = re.compile(r"""classList\.(add|remove|toggle)\(([^)]*)\)""")
MAP_HEAD = re.compile(r"""(?:const|let|var)\s+([A-Z][A-Z0-9_]*)\s*=\s*\{""")
MAP_VALUE = re.compile(r""":\s*(["'`])([^"'`\n]*)\1""")
ANY_STRING = re.compile(r"""([`"'])([^`"'\n]*)\1""")
ONE_STRING = re.compile(r"""^([`"'])([^`"'\n]*)\1$""")
IMPORT = re.compile(r"""from\s+["'](/static/[\w./-]+)["']""")
TOKEN = re.compile(r"[A-Za-z][\w-]*")


def css_rules(css: str) -> str:
    """The stylesheet without its comments. views.css NAMES selectors in its prose, so a check
    reading the raw text passes on a comment alone (the superseded check's own lesson)."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def js_code(js: str) -> str:
    """The module without its comments, for the same reason."""
    return re.sub(r"(?m)^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL))


def _punch(text: str) -> str:
    """Every `${…}` span replaced by one HOLE, brace-matched: the interpolation may nest."""
    out, i = [], 0
    while i < len(text):
        if text.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(text) and depth:
                depth += (text[j] == "{") - (text[j] == "}")
                j += 1
            out.append(HOLE)
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def class_tokens(literal: str) -> list[str]:
    """Complete class tokens in one class literal; a piece touching a hole is dynamic, not a
    token (`chip-${k}` yields nothing, never `chip-`)."""
    return [p for p in _punch(literal).split() if HOLE not in p and TOKEN.fullmatch(p)]


def _list_args(verb: str, arglist: str) -> list[str]:
    """The CLASS arguments of classList.add/remove/toggle. `toggle`'s second argument is a
    CONDITION, not a class — reading it as one mined `action.type === "leave"` for classes."""
    parts, depth, cur = [], 0, ""
    for ch in arglist:
        depth += (ch in "([{") - (ch in ")]}")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    if verb == "toggle":
        parts = parts[:1]
    out: list[str] = []
    for part in parts:
        if m := ONE_STRING.match(part.strip()):
            out += class_tokens(m.group(2))
    return out


def _brace_body(code: str, open_at: int) -> str:
    depth, j = 1, open_at + 1
    while j < len(code) and depth:
        depth += (code[j] == "{") - (code[j] == "}")
        j += 1
    return code[open_at + 1:j - 1]


def elements(code: str) -> list[list[str]]:
    """One token list per class-bearing element the module builds."""
    literal = [class_tokens(m.group(2))
               for pattern in (CLASS_ATTR, CLASS_PROP) for m in pattern.finditer(code)]
    listed = [_list_args(m.group(1), m.group(2)) for m in CLASS_LIST.finditer(code)]
    return [toks for toks in literal + listed if toks]


def map_classes(code: str) -> set[str]:
    """The values of every map whose name says it holds class names."""
    out: set[str] = set()
    for m in MAP_HEAD.finditer(code):
        if CLASS_MAP.search(m.group(1)):
            out |= {v for _, v in MAP_VALUE.findall(_brace_body(code, m.end() - 1))
                    if TOKEN.fullmatch(v)}
    return out


def selector_parts(css: str):
    """Each selector part as the list of its compounds' class sets, left to right.
    `.ladderstrip .bad` → [{ladderstrip}, {bad}]; `.chip.ok` → [{chip, ok}]."""
    for m in re.finditer(r"([^{}]+)\{[^{}]*\}", css):
        selector = " ".join(m.group(1).split())
        if not selector or selector.startswith("@"):
            continue
        for part in selector.split(","):
            part = re.sub(r"::?[a-z-]+(?:\([^)]*\))?", " ", part)      # pseudo-classes/elements
            part = re.sub(r"\[[^\]]*\]", " ", part)                     # attribute selectors
            compounds = [frozenset(re.findall(r"\.([A-Za-z_][\w-]*)", piece))
                         for piece in re.split(r"[>+~\s]+", part.strip()) if piece]
            if any(compounds):
                yield compounds


@lru_cache(maxsize=1)
def survey() -> dict:
    """Read every stylesheet and every static/**/*.js once; the tests share the answer."""
    sheets = sorted(STATIC.glob("*.css"))
    named: set[str] = set()
    parts: dict[str, list[list[frozenset[str]]]] = {}
    for sheet in sheets:
        for compounds in selector_parts(css_rules(sheet.read_text(encoding="utf-8"))):
            for compound in compounds:
                named |= compound
            for token in set().union(*compounds):
                parts.setdefault(token, []).append(compounds)

    shell = {t for group in re.findall(r'class="([^"]*)"',
                                       (STATIC / "index.html").read_text(encoding="utf-8"))
             for t in group.split()}

    modules = sorted(STATIC.rglob("*.js"))
    mounted: dict[str, set[str]] = {}
    context: dict[str, set[str]] = {}
    imported_by: dict[str, set[str]] = {}
    element_count = 0
    for path in modules:
        rel = str(path.relative_to(STATIC.parent))
        code = js_code(path.read_text(encoding="utf-8"))
        per_element = elements(code)
        element_count += len(per_element)
        mounted[rel] = {t for toks in per_element for t in toks} | map_classes(code)
        strings = {m.group(2) for m in ANY_STRING.finditer(code)}
        context[rel] = mounted[rel] | (strings & named) | shell
        if "chip(" in code:
            context[rel].add("chip")
        for m in IMPORT.finditer(code):
            imported_by.setdefault(m.group(1).lstrip("/"), set()).add(rel)

    ancestors: dict[str, set[str]] = {}
    for rel in context:
        seen, frontier = set(), {rel}
        while frontier:
            seen |= frontier
            frontier = set().union(*(imported_by.get(r, set()) for r in frontier)) - seen
        ancestors[rel] = set().union(*(context[r] for r in seen if r in context))

    return {"sheets": [p.name for p in sheets], "named": named, "parts": parts,
            "modules": len(modules), "elements": element_count, "mounted": mounted,
            "context": context, "ancestors": ancestors}


def reachable(token: str, rel: str, s: dict) -> str | None:
    """The first rule naming `token` that the module `rel` can actually match, as a selector:
    the token's OWN compound satisfied by what that module mounts, every ancestor compound by
    its import closure. None when no rule is reachable — the 0.399.2 shape."""
    for compounds in s["parts"].get(token, []):
        own = compounds[-1]
        if (own - {token}) <= s["context"][rel] \
                and all(c <= s["ancestors"][rel] for c in compounds[:-1]):
            return " ".join("." + ".".join(sorted(c)) for c in compounds)
    return None


def selectors_naming(token: str, s: dict) -> list[str]:
    """Every selector that names the token, for a failure message that explains itself."""
    return sorted(" ".join("." + ".".join(sorted(c)) for c in compounds)
                  for compounds in s["parts"].get(token, []))
