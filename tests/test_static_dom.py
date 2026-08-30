"""No console module hands a bare `null` to append/replaceChildren/prepend.

`util.el()` DROPS null children, so `el("div", {}, cond ? node : null)` is the house idiom and
it is safe. The DOM methods do not: `node.append(null)` stringifies it and renders the literal
text **null** on the page. The two look identical at the call site, which is exactly why this
kept happening — a settings panel showed a stray "null" beside its Public URL, a queued message
row showed one where its timestamp would be, and the settings-template panel shipped with a
"null" after "read it" and a "nullnull" between its two layer lists (reported from the live
console, 2026-08-30).

The console is no-build vanilla ES modules, so nothing but the browser would ever catch it, and
a stray word of text throws no error for the UI suite's `js_errors` collector to see. This is
that catch. The fix is always the same shape: spread a filtered array —
`host.append(...[a, cond ? b : null, c].filter(Boolean))`.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
CALL = re.compile(r"\.(?:append|replaceChildren|prepend)\(")


def _call_args(src: str, start: int) -> str:
    """The text between the call's parens, with every NESTED bracket group blanked out — so a
    `: null` inside an `el(...)` child list (which el filters, legitimately) is not read as a
    top-level argument."""
    depth, out = 1, []
    i = start
    while i < len(src) and depth:
        ch = src[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        out.append(" " if depth > 1 else ch)
        i += 1
    return "".join(out)


BARE_NULL = re.compile(r"(?:^|,)\s*null\s*(?:,|$)|:\s*null\s*(?:,|$)")


def test_no_bare_null_argument_to_a_dom_child_call():
    offenders = []
    for path in sorted(STATIC.rglob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in CALL.finditer(src):
            args = _call_args(src, m.end())
            if BARE_NULL.search(args):
                offenders.append(f"{path.relative_to(STATIC.parent)}:{src[:m.end()].count(chr(10)) + 1}")
    assert not offenders, (
        "append/replaceChildren/prepend stringify a null argument into the literal text "
        '"null" on the page — el() filters null children, these do not. Spread a filtered '
        "array instead: host.append(...[a, cond ? b : null, c].filter(Boolean)). Offenders:\n"
        + "\n".join(f"  - {o}" for o in offenders))
