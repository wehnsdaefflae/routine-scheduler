"""The conversations view uses the run page's layout (user orders, 2026-07-16).

Both conversations subpages (list + detail) mount the run view's .run-rail pattern:
the chat owns the main column, the conversation list parks in the LEFT rail and
state/tasks/artifacts in the RIGHT one — and the rails PERSIST at every desktop
width: fixed viewport margins at the top end, sticky grid columns beside the chat
below that (the view escapes the reading column), stacked only on a narrow screen.
The old three-pane grid (conv-layout + drag handles + fold rails) must stay gone,
and views.css must style BOTH rail positions the views mount.

The WIDTHS moved with the 0.277.0 console rework and are read from the stylesheet
rather than pinned here: the navigation rail now takes 212px of the viewport, so the
free margin a fixed rail parks in starts much later, and pinning the old numbers
would assert the layout of a shell that no longer exists. What is asserted is the
REGIME — that a mid-width grid exists, escapes the reading column, and sticks.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"

#: the mid-width regime's own query, found by its shape so a breakpoint move is not a failure
MID_QUERY_RE = re.compile(r"@media \(min-width: \d+px\)")


def _rules(css: str) -> str:
    """The stylesheet without its comments. views.css NAMES selectors in its prose
    ("(.run-rail = the right column, .run-rail.left = the conversation index …)"), so a
    check that read the raw text passed on a comment alone."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _selects(rules: str, selector: str) -> bool:
    """`selector` as a whole token: `.pane-rail` is not present because `.pane-railing` is."""
    return re.search(re.escape(selector) + r"(?![\w-])", rules) is not None


def test_conversations_mounts_run_rails():
    src = (STATIC / "views" / "conversations.js").read_text(encoding="utf-8")
    assert 'class: "run-rail left"' in src, "conversation list must ride a left run-rail"
    assert 'class: "run-rail"' in src, "state/tasks/artifacts must ride the right run-rail"
    for gone in ("conv-layout", "pane-handle", "pane-fold", "pane-rail",
                 "conv-pane-widths", "conv-pane-collapsed"):
        assert gone not in src, f"legacy three-pane grid resurfaced: {gone}"


def test_dom_order_list_chat_artifacts():
    """List left of the chat, artifacts right of it — in stacked mode the list lands
    above the chat and the artifacts below, in grid mode the columns fall out naturally."""
    src = (STATIC / "views" / "conversations.js").read_text(encoding="utf-8")
    assert "view.append(sideRail, main, artRail)" in src, \
        "rail DOM order must be list, chat, artifacts"


def test_css_styles_both_rail_positions():
    rules = _rules((STATIC / "views.css").read_text(encoding="utf-8"))
    assert ".run-rail {" in rules
    assert _selects(rules, ".run-rail.left"), \
        "the left rail variant must be styled (fixed left margin)"
    for gone in (".conv-layout", ".pane-handle", ".pane-fold", ".pane-rail"):
        assert not _selects(rules, gone), f"stale CSS for the removed grid: {gone}"


def test_rails_persist_at_mid_widths():
    """User order 2026-07-16: the rails must REMAIN beside the chat below the fixed-margin
    width too — a sticky three-column grid regime, with the view freed from the reading column.

    The escape is `main:has(.conv-view)`, never `main.conv-view`: app.js renders every view into
    its OWN container inside `main`, so the class lands on that container and the element
    selector matched nothing at all — the escape had never once fired.
    """
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    blocks = [css[m.end():].split("@media", 1)[0] for m in MID_QUERY_RE.finditer(css)]
    block = next((b for b in blocks if "main:has(.conv-view)" in b), "")
    assert block, "shared rail grid regime missing"
    assert "main:has(.conv-view), main:has(.run-view) { max-width: none; }" in block, \
        "both views must escape the reading column through selectors that can match"
    # the comment above the rule NAMES the dead selector to explain it; strip comments first
    assert "main.conv-view" not in _rules(css), "the dead element selector must not come back"
    assert "display: grid" in block, "mid widths must lay the rails out as grid columns"
    assert "position: sticky" in block, "grid rails must stick (remain on scroll)"


#: The conv-*/pane-* class contract that lived here is now `tests/test_static_class_contract.py`
#: (R2885): the same token-not-substring rule, widened from this view's two prefixes to every
#: class every static/**/*.js module mounts, against all five stylesheets — plus the second rule
#: the prefix scope could not express, that a STATE class must be REACHABLE where it is mounted.
#: `conv-head` is waived there, as it was here, as a plain container; `conv-main`, the other of
#: this check's two waivers, has since been styled (views.css:1013) and needs none. One copy of
#: a rule, not two: a narrower duplicate would go green on exactly what the wider one reds on.

