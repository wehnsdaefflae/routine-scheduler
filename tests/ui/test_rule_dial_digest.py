"""The settings digest names `rule_confirm`, so an override on it shows a difference (D252c).

`components/settings-digest.js`'s capabilities line named four of the five approval dials —
`confirm` (util approval), `remind_confirm`, `config_confirm` and `revise_confirm` — and never
`rule_confirm`. The dial itself has existed since the rule-authoring card gained it
(`components/abilities.js`), and `rule_confirm` is in `grants.APPROVAL_DIALS`, so
`effective_settings` has always carried the value into the payload the digest reads: the value
arrived and nothing rendered it.

What that costs is small and exact: the digest is what a changed-field note, an override note,
a closed "more" menu and the Library's pattern settings render from — so turning rule approval
DOWN (the one direction that matters, since `always` is every routine's default) showed no
difference anywhere, and a reader comparing two routines could not see that one of them lets a
rule change land without the operator's click.

Its own file because `tests/ui/test_ability_settings.py`, where the `config_confirm` and
`revise_confirm` digest assertions live, is 346 lines against the 350-line bar: four lines of
slack is not room for a test, and buying one by compacting prose in a file that is nearly at a
ceiling is how the ceiling stops meaning anything.
"""

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
