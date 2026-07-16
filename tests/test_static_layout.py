"""The conversations view uses the run page's layout (user order, 2026-07-16).

Both conversations subpages (list + detail) mount the run view's .run-rail pattern:
the chat owns the full main column, the conversation list parks in the LEFT margin
rail and state/tasks/artifacts in the RIGHT one. The old three-pane grid
(conv-layout + drag handles + fold rails) must stay gone, and views.css must style
BOTH rail positions the views mount.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_conversations_mounts_run_rails():
    src = (STATIC / "views" / "conversations.js").read_text(encoding="utf-8")
    assert 'class: "run-rail left"' in src, "conversation list must ride a left run-rail"
    assert 'class: "run-rail"' in src, "state/tasks/artifacts must ride the right run-rail"
    for gone in ("conv-layout", "pane-handle", "pane-fold", "pane-rail",
                 "conv-pane-widths", "conv-pane-collapsed"):
        assert gone not in src, f"legacy three-pane grid resurfaced: {gone}"


def test_css_styles_both_rail_positions():
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    assert ".run-rail {" in css
    assert ".run-rail.left" in css, "the left rail variant must be styled (fixed left margin)"
    for gone in (".conv-layout", ".pane-handle", ".pane-fold", ".pane-rail"):
        assert gone not in css, f"stale CSS for the removed grid: {gone}"


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
