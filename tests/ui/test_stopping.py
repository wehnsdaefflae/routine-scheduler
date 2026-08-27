"""The GOAL panel in the REAL console (F334/D98) — the half that was specified in 0.208.0,
deferred into F324's rail build, and lost when F324 closed without it.

What these pin is exactly what the user asked for: the sidebar shows the goal conditions,
visually separates what is DONE from what is not, and shows how they are logically connected —
a flat tick list cannot say "either of these two ends the job".
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json


def _goal(conv_dir, doc):
    (conv_dir / "state").mkdir(parents=True, exist_ok=True)
    atomic_write_json(conv_dir / "state" / "stopping.json", doc)


def _start_conversation(ui, ui_page, text="Publish the PDF."):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill(text)
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


DOC = {
    "mode": "any",
    "groups": [{"id": "g1", "name": "publish", "mode": "all"},
               {"id": "g2", "name": "hatch", "mode": "any"}],
    "conditions": [
        {"id": "s1", "text": "the PDF is verified", "status": "met", "group": "g1"},
        {"id": "s2", "text": "it is published", "status": "open", "group": "g1",
         "requires": ["s1"]},
        {"id": "s3", "text": "the user says stop", "status": "open", "group": "g2"},
    ],
}


def test_panel_shows_done_and_not_done_and_how_they_connect(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, DOC)
    ui_page.reload()

    expect(ui_page.locator(".rail-cap", has_text="goal")).to_be_visible()
    rows = ui_page.locator(".goal-row")
    expect(rows).to_have_count(3)

    # DONE vs NOT DONE, visually: the met condition carries the met class (green mark,
    # struck-through text); the open ones do not
    expect(ui_page.locator(".goal-row.met")).to_have_count(1)
    expect(ui_page.locator(".goal-row.met")).to_contain_text("the PDF is verified")
    expect(ui_page.locator(".goal-row.open")).to_have_count(2)

    # LOGICALLY CONNECTED: two groups, each with its own connective, under a root connective
    expect(ui_page.locator(".goal-group")).to_have_count(2)
    expect(ui_page.locator(".goal-mode")).to_have_count(3)      # root + one per group
    expect(ui_page.locator(".goal-head .goal-mode")).to_have_text("ANY")
    expect(ui_page.locator(".goal-group").first).to_contain_text("ALL")
    expect(ui_page.locator(".goal-group").nth(1)).to_contain_text("ANY")

    # the per-group tally says how far through each one the run is
    expect(ui_page.locator(".goal-group").first).to_contain_text("1/2")


def test_a_blocked_condition_says_what_it_waits_for(ui, ui_page):
    """A dependency is only useful if the reader can see it — "dormant" alone leaves them
    guessing which condition it is waiting on."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {**DOC, "conditions": [
        {"id": "s1", "text": "draft written", "status": "open", "group": "g1"},
        {"id": "s2", "text": "draft reviewed", "status": "open", "group": "g1",
         "requires": ["s1"]}]})
    ui_page.reload()
    blocked = ui_page.locator(".goal-row.blocked")
    expect(blocked).to_have_count(1)
    expect(blocked).to_contain_text("draft reviewed")
    expect(blocked).to_contain_text("waiting on s1")


def test_the_verdict_chip_reads_met_only_when_the_goal_is_satisfied(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, DOC)
    ui_page.reload()
    expect(ui_page.locator(".goal-verdict")).to_have_text("in progress")

    # the root is ANY, so satisfying the escape-hatch group alone ends the job
    _goal(conv_dir, {**DOC, "conditions": [
        *DOC["conditions"][:2],
        {"id": "s3", "text": "the user says stop", "status": "met", "group": "g2"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-verdict")).to_have_text("goal met")
    expect(ui_page.locator(".goal-verdict")).to_have_class("goal-verdict met")


def test_clicking_a_mark_cycles_the_status_and_saves(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {"mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
                     "conditions": [{"id": "s1", "text": "verify it", "status": "open",
                                     "group": "g1"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-row.open")).to_have_count(1)

    ui_page.locator(".goal-mark").click()               # open -> met
    expect(ui_page.locator(".goal-row.met")).to_have_count(1)
    ui_page.get_by_role("button", name="save goal").click()
    expect(ui_page.locator("#toast")).to_contain_text("goal saved")

    stored = json.loads((conv_dir / "state" / "stopping.json").read_text())
    assert stored["conditions"][0]["status"] == "met"


def test_a_group_connective_is_editable_and_persists(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {"mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
                     "conditions": [{"id": "s1", "text": "a", "status": "open", "group": "g1"},
                                    {"id": "s2", "text": "b", "status": "met", "group": "g1"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-verdict")).to_have_text("in progress")

    ui_page.locator(".goal-group .goal-mode").click()   # ALL -> ANY
    ui_page.get_by_role("button", name="save goal").click()
    expect(ui_page.locator("#toast")).to_contain_text("goal saved")
    # one met member now satisfies the group, so the whole goal is met
    expect(ui_page.locator(".goal-verdict")).to_have_text("goal met")
    assert json.loads((conv_dir / "state" / "stopping.json").read_text())["groups"][0]["mode"] \
        == "any"


def test_no_conditions_explains_what_the_panel_is_for(ui, ui_page):
    """An empty panel that just says "none" teaches nobody why they would want one."""
    _slug, _conv_dir = _start_conversation(ui, ui_page)
    expect(ui_page.locator(".goals")).to_contain_text("No stopping conditions")
    expect(ui_page.locator(".goals")).to_contain_text("runaway backstop")
    expect(ui_page.locator(".goal-verdict")).to_have_count(0)   # no goal set → no verdict
