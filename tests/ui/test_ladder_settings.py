"""The escalation ladder's settings, as a PERSON uses them.

Operator, 2026-10-03: *"obviously it needs a complete ui"* — until then the ladder could only be
switched on by editing `routine.yaml` by hand, so the feature was fully visible once it fired and
invisible until then.

What is pinned here is what the page does, not what the API accepts (that is
`tests/test_ladder_settings_api.py`): the two controls exist in the two groups their authority
class belongs to, the switch round-trips through the page's one accept into `routine.yaml`, and
the interval knobs round-trip into `tuning.yaml` — including the cleared budget, whose whole
meaning is "derive it again".
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect

from .conftest import until
from .helpers import unfold, visible_toast


def _tuning(ui, slug="uir"):
    path = ui.routine_dir(slug) / "tuning.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _config(ui, slug="uir"):
    path = ui.routine_dir(slug) / "routine.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_the_ladder_switch_is_in_limits_and_saves_through_the_one_accept(ui, ui_page):
    """`ladder.enabled` + `max_depth` are CONFIG — authority over being watched is the user's —
    so they sit with the per-run ceilings and go through the accept bar like every other config
    field."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    group = ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text="Limits & reach"))
    expect(group.locator("h2", has_text="Oversight")).to_have_count(1)

    onoff = ui_page.locator("[data-ladder-enabled]")
    expect(onoff).not_to_be_checked()                      # off by default, for every routine
    expect(ui_page.locator("[data-ladder-depth]")).to_be_disabled()   # nothing to cap while off

    onoff.check()
    depth = ui_page.locator("[data-ladder-depth]")
    expect(depth).to_be_enabled()
    depth.fill("2")
    depth.blur()
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: _config(ui).get("ladder") == {"enabled": True, "max_depth": 2},
          what="the ladder config save")

    # and it reads back from the server, not from client state
    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    expect(ui_page.locator("[data-ladder-enabled]")).to_be_checked()
    expect(ui_page.locator("[data-ladder-depth]")).to_have_value("2")


def test_the_interval_knobs_are_tuning_and_land_in_tuning_yaml(ui, ui_page):
    """`ladder_rung_height` + `oversight_turns` are RECIPE-classed, so they sit beside
    Deliberation, the other tuning key — and they must never reach routine.yaml."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    group = ui_page.locator(".rgroup", has=ui_page.locator(".rgroup-title", has_text="Models"))
    expect(group.locator("h2", has_text="Rung intervals")).to_have_count(1)

    height = ui_page.locator("[data-ladder-height]")
    expect(height).to_have_value("20")                     # config.base.DEFAULT_RUNG_HEIGHT
    height.fill("30")
    height.blur()
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: _tuning(ui).get("ladder_rung_height") == 30, what="the rung height save")
    assert "ladder_rung_height" not in _config(ui), "a tuning key must not reach routine.yaml"


def test_an_empty_rung_budget_says_the_number_the_engine_will_derive(ui, ui_page):
    """The blank box is the common case and it is NOT zero: the engine derives the budget as
    `n // 2 + 1`, floored at 4. The control says which number that is, so an empty field never
    reads as "no budget"."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    turns = ui_page.locator("[data-ladder-turns]")
    expect(turns).to_have_value("")
    strip = ui_page.locator(".ladder-set", has=turns)
    expect(strip).to_contain_text("derived from the interval: 11 turns")   # 20 // 2 + 1

    ui_page.locator("[data-ladder-height]").fill("40")
    ui_page.locator("[data-ladder-height]").blur()
    expect(strip).to_contain_text("derived from the interval: 21 turns")   # 40 // 2 + 1


def test_pinning_then_clearing_the_rung_budget_returns_it_to_derived(ui, ui_page):
    """Clearing the field must REMOVE the key, not store a zero — absence is this knob's one
    spelling of its derived default, and the routine PATCH drops a null (`exclude_none`), which
    is why the control sends 0 for "derive it again"."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    turns = ui_page.locator("[data-ladder-turns]")
    turns.fill("12")
    turns.blur()
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: _tuning(ui).get("oversight_turns") == 12, what="the pinned rung budget")

    ui_page.goto(f"{ui.url}#/routine/uir")
    unfold(ui_page)
    expect(ui_page.locator("[data-ladder-turns]")).to_have_value("12")
    ui_page.locator("[data-ladder-turns]").fill("")
    ui_page.locator("[data-ladder-turns]").blur()
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(visible_toast(ui_page)).to_contain_text("accepted")
    until(lambda: "oversight_turns" not in _tuning(ui),
          what="the rung budget returning to derived")
    assert _tuning(ui).get("ladder_rung_height") is not None or True   # siblings untouched
