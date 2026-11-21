"""The settings digest names `rule_confirm`, so an override on it shows a difference (D252c)."""

from __future__ import annotations

_LINES = """async () => {
  const { describe } = await import('/static/components/settings-digest.js');
  const caps = (v) => ({ actions: [], utils: [], rule_confirm: v });
  return [describe('capabilities', caps('creations')), describe('capabilities', caps('never')),
          describe('capabilities', caps('always')), describe('capabilities', {})];
}"""


def test_the_digest_names_rule_approval_only_once_it_departs_from_always(ui, ui_page):
    """The same rule the other two dials follow: `always` is every routine's default and says
    nothing, so naming it would add a line to every digest that reports no policy at all —
    while any other level is a policy a reader needs told.
    """
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h1")
    creations, never, always, absent = ui_page.evaluate(_LINES)

    assert "rule approval: creations" in creations, creations
    assert "rule approval: never" in never, never
    # and it stays silent at the default, present or omitted
    assert "rule approval" not in always, always
    assert "rule approval" not in absent, absent


def test_every_approval_dial_the_payload_carries_is_named_by_the_digest(ui, ui_page):
    """The defect one level up, so the next dial cannot repeat it: the digest is checked
    against `grants.APPROVAL_DIALS` itself rather than against a list written here. A sixth
    dial added to the engine with no digest line fails THIS test instead of shipping silent.
    """
    from rsched.grants import APPROVAL_DIALS

    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h1")
    # every dial at a NON-default level, one at a time: each must put something in the line
    for dial, levels in APPROVAL_DIALS.items():
        other = next((lv for lv in levels if lv != "always"), None)
        assert other, f"{dial} has no level other than always"
        caps = {"actions": [], "utils": [], dial: other}
        # `remind_confirm` is revealed by its layer, exactly as the panel reveals its row
        if dial == "remind_confirm":
            caps["reminders"] = "global"
        line = ui_page.evaluate(
            "async (caps) => (await import('/static/components/settings-digest.js'))"
            ".describe('capabilities', caps)", caps)
        assert other in line, f"{dial}={other} is not named by the digest: {line!r}"
