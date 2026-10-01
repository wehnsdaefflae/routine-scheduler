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


def test_no_view_references_undefined_conv_classes():
    """Every conv-*/pane-* class literal the conversations view mounts is styled — by a RULE:
    a class a comment merely mentions is not styled."""
    src = (STATIC / "views" / "conversations.js").read_text(encoding="utf-8")
    css = _rules((STATIC / "views.css").read_text(encoding="utf-8"))
    used = set()
    for m in re.finditer(r'class: [`"]([^`"]+)[`"]', src):
        for token in re.split(r"[\s$]", m.group(1)):
            if token.startswith(("conv-", "pane-")):
                used.add(token.rstrip("{"))
    # plain containers, intentionally unstyled (conv-head holds the styled .conv-head-row pair;
    # the substring match had been reading it as styled through them)
    structural = {"conv-main", "conv-head"}
    # a CLASS TOKEN, not a substring: `.conv-tag` must not count as styled because
    # `.conv-tagsel` is
    styled = set(re.findall(r"\.((?:conv|pane)-[\w-]+)", css))
    missing = (used - structural) - styled
    assert not missing, f"classes mounted but unstyled in views.css: {sorted(missing)}"

