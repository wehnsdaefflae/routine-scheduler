"""The design system's stylesheet rules as a machine check (CLAUDE.md "Standards", base.css head).

"Colour is state" holds only while every colour is a TOKEN, every token is defined for BOTH
themes, every token a rule asks for exists, and type comes from the three type tokens. Those
rules lived in prose, and each has been broken in a way no eye caught for releases: a recipe bar
hard-coded as a dark-mode violet that stayed violet on a white sheet (F371), `var(--line)` asked
for by views.css when no stylesheet defined it, the search box's magnifier stroked with the dark
theme's --ink-3 as a literal on both themes, a hand-written monospace stack beside --f-mono. A
literal is invisible in the theme its author was using — which is the theme every author uses.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
BASE = (STATIC / "base.css").read_text(encoding="utf-8")
VIEWS = (STATIC / "views.css").read_text(encoding="utf-8")

#: A colour written out rather than read from a token — `%23` is a `#` inside a data URI, which is
#: where the magnifier's literal hid.
COLOUR_LITERAL = re.compile(
    r"(?:#|%23)[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(")

#: views.css rules whose colour is deliberately NOT the console's, each explained in its own
#: comment: a routine's artifact page assumes white, so its frame must not follow our theme.
OWN_GROUND = {".art-frame"}


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for every rule, comments stripped; a rule inside @media is its own."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def test_views_css_adds_no_colour_of_its_own():
    leaks = [sel for sel, body in _rules(VIEWS)
             if COLOUR_LITERAL.search(body) and sel not in OWN_GROUND]
    assert not leaks, f"views.css colours that are not tokens: {leaks}"


def test_base_css_colours_are_tokens_defined_for_both_themes():
    """Outside the token block nothing is a literal; inside it every colour is `light-dark()`."""
    for sel, body in _rules(BASE):
        if sel != ":root":
            assert not COLOUR_LITERAL.search(body), f"a literal colour outside the tokens: {sel}"
            continue
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
            if COLOUR_LITERAL.search(value):
                assert value.strip().startswith("light-dark("), (
                    f"{name} is defined for one theme only: {value.strip()}")


def test_every_token_a_rule_asks_for_is_defined():
    """Including the inline styles the views write — `color:var(--err)` is the same reference."""
    sources = [p.read_text(encoding="utf-8") for p in STATIC.rglob("*")
               if p.suffix in (".css", ".js", ".html")]
    defined = set()
    for text in sources:
        defined |= set(re.findall(r"(--[\w-]+)\s*:", text))
        defined |= set(re.findall(r"setProperty\(\s*[\"'`](--[\w-]+)", text))
    used = {m for text in sources for m in re.findall(r"var\(\s*(--[\w-]+)", text)}
    assert not used - defined, f"tokens asked for and defined nowhere: {sorted(used - defined)}"


def test_an_action_rows_outcome_never_wears_summons():
    """SUMMONS (coral) is what waits on a PERSON (base.css head). An action row's outcome is
    history, and the two that are not failures of the work — a malformed call (exit 2), a
    deadline that killed it (exit 124) — are the RUN's to repair: neither waits on anyone. They
    read in the warning colour, and keep the edge that tells them apart: solid for a call that
    was answered, dashed for one that never got a verdict."""
    rows = {sel: body for sel, body in _rules(VIEWS) if sel.startswith(".obs-")}
    usage, timeout = rows.get(".obs-usage > summary"), rows.get(".obs-timeout > summary")
    assert usage and timeout, "views.css lost the usage/timeout row rules"
    worn = [sel for sel, body in rows.items() if "--summons" in body]
    assert not worn, f"an action row's outcome wears SUMMONS, which waits on a person: {worn}"
    for sel, body in ((".obs-usage", usage), (".obs-timeout", timeout)):
        assert "var(--warn)" in body and "var(--warn-dim)" in body, (
            f"{sel} is not in the warning colour: {body.strip()}")
    assert "dashed" in timeout and "dashed" not in usage, "the solid/dashed edges swapped or merged"


#: A serif face, by family name or by generic. `sans-serif` and `ui-sans-serif` contain the
#: word and are the opposite of one, so the pattern requires the generic to stand alone.
SERIF_FACE = re.compile(
    r"(?<!sans-)(?<!sans)\bserif\b|\bui-serif\b|\bGeorgia\b|\bCharter\b|\bPalatino\b"
    r"|\bBaskerville\b|\bGaramond\b|\bCambria\b|\bConstantia\b|\bIowan Old Style\b"
    r"|\bTimes New Roman\b|\bTimes\b|\bMincho\b|\bSongti\b|\bBatang\b", re.IGNORECASE)


def test_no_type_token_names_a_serif_face():
    """The console uses no serif type (operator, 2026-10-12: "you need to make sure not to use
    serif fonts in the webui").

    Checked at the TOKENS because `test_type_comes_from_the_three_type_tokens` already proves
    no rule spells a face of its own — so the three token values are the whole type surface of
    `static/`, and a serif cannot reappear anywhere else without failing that test first.
    `docs_build.py` keeps its own copy of the token block for the standalone guide/docs pages
    the console serves, which is part of the same web UI: a fix in one copy and not the other
    is how the two drifted apart before.
    """
    from rsched import docs_build

    for name, text in (("static/base.css", BASE),
                       ("docs_build.THEME_TOKENS", docs_build.THEME_TOKENS)):
        for token, value in re.findall(r"(--f-[\w-]+)\s*:\s*([^;]+);", text):
            assert not SERIF_FACE.search(value), (
                f"{name} {token} names a serif face: {value.strip()}")


def test_type_comes_from_the_three_type_tokens():
    for name, css in (("base.css", BASE), ("views.css", VIEWS)):
        for sel, body in _rules(css):
            for prop, value in re.findall(r"(?<![\w-])(font-family|font)\s*:\s*([^;]+)", body):
                assert value.strip() == "inherit" or "var(--f-" in value, (
                    f"{name} {sel} spells its own {prop}: {value.strip()}")
