"""A CONVERSATION is switched into oversight from its own page — D216 (d), the last part.

The engine half was never missing: `engine/ladder.ladder_settings` reads `ctx.routine.ladder`
with no conversation gate, and a conversation IS a `RoutineConfig`, so the field existed and the
engine honoured it. What was missing was the SURFACE — and the PATCH vocabulary behind it, where
`ladder` was absent from `ConversationPatch` (which is `extra="forbid"`, so naming it was a 422).

These tests load what the operator sees, because the gap was a PAGE. They mount the same two
controls the routine page uses, through `ladderPanel`, so what is asserted here is the shared
component's own vocabulary (`data-ladder-enabled`, `data-ladder-rung-height`,
`data-ladder-interval`) over a conversation slug.
"""

import yaml
from playwright.sync_api import expect

from .helpers import start_conversation, visible_toast


def _open_caps(ui_page):
    """The conversation's capabilities panel, open, with the ladder panel rendered."""
    # the panel's OWN disclosure (`> summary`): cards inside it fold behind summaries of their
    # own, so a descendant match is not unique.
    ui_page.locator(".conv-caps > summary").click()
    ui_page.wait_for_selector("[data-ladder-enabled]")


def test_a_conversation_is_switched_into_oversight_from_its_own_page(ui, ui_page):
    """Press the switch: the config reaches THIS conversation's own routine.yaml, which is what
    each reply boots from. Before this panel the only way there was hand-editing the file.
    """
    slug, conv = start_conversation(ui, ui_page, "review the migration plan")
    _open_caps(ui_page)

    switch = ui_page.locator("[data-ladder-enabled]")
    expect(switch).to_have_count(1)
    expect(ui_page.locator("[data-ladder-rung-height]")).to_have_count(1)
    switch.check()
    expect(visible_toast(ui_page)).to_contain_text("ladder saved")

    stored = yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8")) or {}
    assert stored["ladder"]["enabled"] is True
    assert stored["ladder"]["rung_height"] == 3      # the default, carried by the merge


def test_the_rung_height_and_the_turn_interval_land_in_their_own_files(ui, ui_page):
    """The ladder's split across two authority classes is the point, and it survives here: the
    switch and the height are CONFIG (routine.yaml, the user's), the turn interval is TUNING
    (tuning.yaml, recipe-classed). One panel, two files — not one blob.
    """
    slug, conv = start_conversation(ui, ui_page, "plan the sprint")
    _open_caps(ui_page)

    # Each commit is its own PATCH, so each is awaited by its RESPONSE rather than by the
    # toast: the toast from the previous save is still on screen, so `to_contain_text("ladder
    # saved")` passes before the next request has even been sent — which is how the first
    # version of this test read an empty tuning.yaml while the API-level test of the same write
    # passed.
    def commit(fill):
        with ui_page.expect_response(
                lambda r: "/api/conversations/" in r.url and r.request.method == "PATCH") as got:
            fill()
        assert got.value.status == 200, got.value.text()

    commit(lambda: ui_page.locator("[data-ladder-enabled]").check())
    expect(visible_toast(ui_page)).to_contain_text("ladder saved")
    height = ui_page.locator("[data-ladder-rung-height]")
    interval = ui_page.locator("[data-ladder-interval]")
    expect(interval).to_have_count(1)

    def set_height():
        height.fill("2")
        height.dispatch_event("change")

    def set_interval():
        interval.fill("20")
        interval.dispatch_event("change")

    commit(set_height)
    commit(set_interval)

    stored = yaml.safe_load((conv / "routine.yaml").read_text(encoding="utf-8")) or {}
    assert stored["ladder"] == {"enabled": True, "rung_height": 2}
    tuning = yaml.safe_load((conv / "tuning.yaml").read_text(encoding="utf-8")) or {}
    assert tuning["ladder_turn_interval"] == 20
    assert "ladder" not in tuning, "the config half leaked into the recipe-classed file"


def test_the_supervisor_picker_is_on_the_model_row(ui, ui_page):
    """The overseer's model is a ROLE (D164) that falls back to this thread's own `main`. The
    picker had to exist here for a second reason: `validate_models` replaces the role block
    wholesale, so a `supervisor` the header did not SEND was deleted by every model save.
    """
    start_conversation(ui, ui_page, "write the release note")
    row = ui_page.locator(".conv-model")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("supervisor")
    picker = row.locator('select[title*="supervisor"]')
    expect(picker).to_have_count(1)
    expect(picker.locator("option").first).to_contain_text("this thread's main model")
