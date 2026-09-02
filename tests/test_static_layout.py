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
MID_QUERY_RE = re.compile(r"@media \(min-width: \d+px\) and \(max-width: [\d.]+px\)")


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
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    assert ".run-rail {" in css
    assert ".run-rail.left" in css, "the left rail variant must be styled (fixed left margin)"
    for gone in (".conv-layout", ".pane-handle", ".pane-fold", ".pane-rail"):
        assert gone not in css, f"stale CSS for the removed grid: {gone}"


def test_rails_persist_at_mid_widths():
    """User order 2026-07-16: the rails must REMAIN beside the chat below the fixed-margin
    width too — a sticky three-column grid regime, with the view freed from the reading column.

    The escape is `main:has(.conv-view)`, never `main.conv-view`: app.js renders every view into
    its OWN container inside `main`, so the class lands on that container and the element
    selector matched nothing at all — the escape had never once fired.
    """
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    m = MID_QUERY_RE.search(css)
    assert m, "mid-width grid regime missing"
    block = css.split(m.group(0), 1)[1].split("@media", 1)[0]
    assert "main:has(.conv-view) { max-width: none; }" in block, \
        "the view must escape the reading column, through a selector that can match"
    # the comment above the rule NAMES the dead selector to explain it; strip comments first
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert "main.conv-view" not in rules, "the dead element selector must not come back"
    assert "display: grid" in block, "mid widths must lay the rails out as grid columns"
    assert "position: sticky" in block, "grid rails must stick (remain on scroll)"


def test_no_view_references_undefined_conv_classes():
    """Every conv-*/pane-* class literal the conversations view mounts is styled."""
    import re
    src = (STATIC / "views" / "conversations.js").read_text(encoding="utf-8")
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    used = set()
    for m in re.finditer(r'class: [`"]([^`"]+)[`"]', src):
        for token in re.split(r"[\s$]", m.group(1)):
            if token.startswith(("conv-", "pane-")):
                used.add(token.rstrip("{"))
    structural = {"conv-main"}   # a plain container, intentionally unstyled
    missing = {t for t in used - structural if f".{t}" not in css}
    assert not missing, f"classes mounted but unstyled in views.css: {sorted(missing)}"

