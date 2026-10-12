"""The three type roles as the BROWSER resolves them, in the REAL console.

`tests/test_stylesheet_tokens.py` reads the stylesheet files: it proves no token names a serif
face and no rule spells a face of its own. That is the static half. This is the half it cannot
reach — that the stylesheet the daemon SERVES is the one with the fix in it, and that a prose
element on a real page therefore computes a sans stack. A token corrected in the repo while the
console serves a cached or frozen copy would pass the file check and still show the operator a
serif (0.418.1 shipped a NEW stylesheet for exactly that reason: "a frozen stylesheet means a
NEW stylesheet, not an inline style").

Operator, 2026-10-12: "you need to make sure not to use serif fonts in the webui."
"""

from __future__ import annotations

#: Generic and family names that are serif. `sans-serif`/`ui-sans-serif` contain the word and are
#: its opposite, so the generic has to stand alone — the probe strips those two before matching.
SERIF_NAMES = ("serif", "georgia", "charter", "palatino", "baskerville", "garamond", "cambria",
               "constantia", "iowan old style", "times new roman", "times", "mincho", "songti",
               "batang")


def _serif_in(stack: str) -> list[str]:
    """The serif names a resolved font stack carries, sans-serif generics removed first."""
    cleaned = stack.lower().replace("ui-sans-serif", "").replace("sans-serif", "")
    return [n for n in SERIF_NAMES if n in cleaned]


def test_no_console_surface_computes_a_serif_stack(ui, ui_page):
    """Every type role, as resolved by the browser on a loaded page.

    The probe asks the live document for each token's value AND for the computed `font-family`
    of a real `.prose` element, so a serif reaching the user through the cascade — a token, a
    late rule, a stale served sheet — fails here whatever its source.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector(".topbar")
    resolved = ui_page.evaluate(
        "() => {"
        "  const root = getComputedStyle(document.documentElement);"
        "  const probe = document.createElement('div');"
        "  probe.className = 'prose'; probe.textContent = 'x';"
        "  document.body.append(probe);"
        "  const prose = getComputedStyle(probe).fontFamily;"
        "  probe.remove();"
        "  return {"
        "    display: root.getPropertyValue('--f-display').trim(),"
        "    mono: root.getPropertyValue('--f-mono').trim(),"
        "    prose: root.getPropertyValue('--f-prose').trim(),"
        "    proseComputed: prose,"
        "    bodyComputed: getComputedStyle(document.body).fontFamily,"
        "  };"
        "}")
    assert resolved["prose"], f"the console serves no --f-prose token at all: {resolved}"
    for role, stack in resolved.items():
        found = _serif_in(stack)
        assert not found, f"the console's {role} type is serif ({found}): {stack}"
