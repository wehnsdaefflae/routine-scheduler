"""The three type roles as the BROWSER resolves them, in the REAL console."""

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
    """Every type role, as resolved by the browser on a loaded page."""
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
