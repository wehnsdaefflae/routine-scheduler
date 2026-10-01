"""The colour-theme toggle (auto / light / dark) in the REAL console.

The whole palette turns on one CSS mechanism: every token is `light-dark(light, dark)` and the
theme is chosen by the root's `color-scheme` alone (base.css) — `data-theme="light"` sets
`color-scheme: light`, which makes both `light-dark()` AND the browser's own page canvas go light.
So the ONE thing that must hold is: picking light actually computes a light `color-scheme` and a
light surface. This surface had no test at all, which is how "light does nothing" reached the user.
"""

from __future__ import annotations

import pytest


def _load_with_theme(ui, ui_page, theme: str):
    # the same pre-load seed the token uses (conftest), so the inline <head> script in index.html
    # applies the theme BEFORE first paint exactly as it does for a real visit
    ui_page.add_init_script(f"localStorage.setItem('rsched_theme', {theme!r})")
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector(".topbar")


def _luminance(rgb: str) -> float:
    """Rough perceived luminance (0-255) of an `rgb(...)`/`rgba(...)` string."""
    nums = [float(x) for x in rgb.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")[:3]]
    r, g, b = nums
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_light_theme_actually_makes_the_surface_light(ui, ui_page):
    """Picking light must compute a light `color-scheme` on the root and a light rail surface —
    the operator's report is that it stays dark (screenshot 2026-09-04)."""
    _load_with_theme(ui, ui_page, "light")
    state = ui_page.evaluate(
        "() => ({"
        "  theme: document.documentElement.dataset.theme,"
        "  scheme: getComputedStyle(document.documentElement).colorScheme,"
        "  deck: getComputedStyle(document.documentElement).getPropertyValue('--deck').trim(),"
        "  railBg: getComputedStyle(document.querySelector('.topbar')).backgroundColor"
        "})")
    assert state["theme"] == "light", state
    assert state["scheme"] == "light", state
    # the rail surface (--deck-2) must resolve to a LIGHT colour, not the dark arm of light-dark()
    lum = _luminance(state["railBg"])
    assert lum > 160, f"light theme left the rail dark: {state} (luminance {lum:.0f})"


def test_dark_theme_keeps_the_surface_dark(ui, ui_page):
    """The shipped default: dark computes a dark `color-scheme` and a dark surface."""
    _load_with_theme(ui, ui_page, "dark")
    state = ui_page.evaluate(
        "() => ({"
        "  theme: document.documentElement.dataset.theme,"
        "  scheme: getComputedStyle(document.documentElement).colorScheme,"
        "  railBg: getComputedStyle(document.querySelector('.topbar')).backgroundColor"
        "})")
    assert state["theme"] == "dark", state
    assert state["scheme"] == "dark", state
    lum = _luminance(state["railBg"])
    assert lum < 96, f"dark theme surface is not dark: {state} (luminance {lum:.0f})"


def test_document_declares_dual_scheme_support_to_defeat_force_dark(ui, ui_page):
    """A browser that force-darkens pages (Android "Auto Dark Theme", some desktop dark-mode
    extensions) keys its opt-out off the DOCUMENT-level `<meta name="color-scheme">`, not only
    base.css's `color-scheme` property — so without the meta a user's `light` choice can be
    re-darkened over the top. Pin that the meta is present and declares both schemes."""
    _load_with_theme(ui, ui_page, "light")
    content = ui_page.evaluate(
        "() => document.querySelector('meta[name=\"color-scheme\"]')?.content || ''")
    assert "light" in content and "dark" in content, f"color-scheme meta missing/partial: {content!r}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_search_icon_is_drawn_in_the_themes_own_ink(ui, ui_page, theme):
    """Every colour is a token defined for both themes — and the search box's magnifier was not.
    It was a data-URI background, whose SVG cannot read a custom property, so its stroke was the
    dark theme's --ink-3 written out as a literal: on the light theme the icon stayed a grey the
    palette does not have, beside a placeholder drawn in the real --ink-3. It is a mask filled
    with --ink-3 now, so it follows the theme like everything else."""
    _load_with_theme(ui, ui_page, theme)
    ui_page.wait_for_selector(".gsearch .gs-input")
    state = ui_page.evaluate("""() => {
      const p = document.createElement("span");
      p.style.color = "var(--ink-3)"; document.body.append(p);
      const ink3 = getComputedStyle(p).color; p.remove();
      return { ink3,
               icon: getComputedStyle(document.querySelector(".gsearch"), "::before").backgroundColor,
               image: getComputedStyle(document.querySelector(".gsearch .gs-input")).backgroundImage };
    }""")
    assert state["icon"] == state["ink3"], f"the search icon is not --ink-3 on {theme}: {state}"
    assert state["image"] == "none", f"a literal-coloured icon still rides the input: {state}"

