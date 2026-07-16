"""The conversations view uses the run page's layout (user orders, 2026-07-16).

Both conversations subpages (list + detail) mount the run view's .run-rail pattern:
the chat owns the main column, the conversation list parks in the LEFT rail and
state/tasks/artifacts in the RIGHT one — and the rails PERSIST at every desktop
width: fixed viewport margins >=1560px, sticky grid columns beside the chat at
1200-1559px (the view escapes the 1180px column), stacked only below 1200px.
The old three-pane grid (conv-layout + drag handles + fold rails) must stay gone,
and views.css must style BOTH rail positions the views mount.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"

MID_QUERY = "@media (min-width: 1200px) and (max-width: 1559.9px)"


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
    """User order 2026-07-16: the rails must REMAIN beside the chat below 1560px too —
    a sticky three-column grid regime, with the view freed from the 1180px column."""
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    assert MID_QUERY in css, "mid-width grid regime missing"
    block = css.split(MID_QUERY, 1)[1].split("@media", 1)[0]
    assert "main.conv-view { max-width: none; }" in block, "view must escape the 1180px column"
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


def test_wizard_recovery_affordances_present():
    """Pins the affordances shipped for the 2026-07-16 wizard incidents: the setup banner
    names the session by its draft preview (an abandoned session must not read as if it were
    the routine just created), and the clarify error panel offers a draft-preserving retry."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "cur.draft" in app, "setup banner must quote the session's draft preview"
    panel = (STATIC / "components" / "setuppanel.js").read_text(encoding="utf-8")
    assert "retry with the same draft" in panel, "error panel must offer a draft-preserving retry"
    assert "draft_full" in panel, "the retry needs the snapshot's full draft"


def test_bespoke_wizard_views_stay_retired():
    """D11 (2026-07-16): the run page IS the setup surface — clarify sessions render at
    #/run/clarification:<ts> with the setup panel; the bespoke wizard views and the
    #/wizard route must not resurface."""
    assert not (STATIC / "views" / "wizard.js").exists()
    assert not (STATIC / "views" / "wizard-create.js").exists()
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "#/wizard" not in app, "the #/wizard route is retired — sessions live on run pages"
    assert "/static/views/new-routine.js" in app, "the draft stage lives at #/new-routine"
    run = (STATIC / "views" / "run.js").read_text(encoding="utf-8")
    assert "createSetupPanel" in run, "the run view must mount the setup panel on clarify runs"
