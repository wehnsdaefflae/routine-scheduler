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


#: Goal-scoped on purpose: "publish the PDF" is a job with an END, and the verdict chip and the
#: per-group tallies are about the FINAL GOAL only — a per-run bound has no durable verdict to
#: show (engine/stopping.py). The scope toggle itself is covered at the foot of this file.
DOC = {
    "mode": "any",
    "groups": [{"id": "g1", "name": "publish", "mode": "all"},
               {"id": "g2", "name": "hatch", "mode": "any"}],
    "conditions": [
        {"id": "s1", "text": "the PDF is verified", "status": "met", "group": "g1",
         "scope": "goal"},
        {"id": "s2", "text": "it is published", "status": "open", "group": "g1",
         "requires": ["s1"], "scope": "goal"},
        {"id": "s3", "text": "the user says stop", "status": "open", "group": "g2",
         "scope": "goal"},
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


def test_a_long_condition_note_wraps_instead_of_overflowing(ui, ui_page):
    """F421 (operator 2026-09-01): a long condition note sat in a nowrap .goal-meta, so it ran
    off the sidebar AND starved .goal-text into per-word wrapping. The meta/note now wrap."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {**DOC, "conditions": [
        {"id": "s1", "text": "the digest is published and the shortlink resolves",
         "status": "open", "group": "g1",
         "note": "blocked on the CDN purge that finishes only after the nightly cache sweep"},
    ]})
    ui_page.reload()
    note = ui_page.locator(".goal-note").first
    expect(note).to_be_visible()
    # F421: the note must be allowed to wrap, not nowrap (which overflowed the sidebar)
    assert note.evaluate("el => getComputedStyle(el).whiteSpace") != "nowrap"


def test_a_paragraph_long_note_does_not_starve_the_condition(ui, ui_page):
    """The other half of F421, still live in 0.279.0 (operator screenshot): letting the note WRAP
    says how it breaks, not how much of the row it may claim. A paragraph-long note still won the
    flex negotiation and squeezed .goal-text — `flex: 1; min-width: 0` — to one word per line, so
    the panel reporting the accounting was destroyed by the accounting."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {**DOC, "conditions": [
        {"id": "s1", "status": "open", "group": "g1",
         "text": "the collaborative steward page is published with login as Mark or Florence, "
                 "read-only lock for the other, and a ten-minute inactivity auto-logout",
         "note": "** — Seite nicht live: Hub-Slug unregistriert (400), Registrierung owned by "
                 "the hub maintainer (R1164 steht + frischer Gruppen-Note). Login/Lock/"
                 "Auto-Logout laufen ueber die Shell des Hubs (append-only Modell). Mockup "
                 "live als Interim, Dokumente liegen als PDF vor und warten auf das Deploy."},
    ]})
    ui_page.reload()
    text = ui_page.locator(".goal-text").first
    expect(text).to_be_visible()
    width = text.evaluate("el => el.getBoundingClientRect().width")
    # One word per line is what a starved column looks like; a readable one is far wider than
    # the longest word in it. 160px is well below any sane column and far above a starved one.
    assert width > 160, f".goal-text starved to {width}px by its own note"


def test_a_long_note_does_not_crush_the_meta_on_a_narrow_routine_run(ui, ui_page):
    """F421 v3 (operator screenshot 2026-09-04, mobile miz-grant-steward run): the ROUTINE run
    view renders the goal panel with showStage=true, so a condition row also carries the per-stage
    'any stage' input. On a NARROW viewport that fixed input, the requires select and the
    min-width:22ch condition text filled the single flex row, leaving .goal-meta ([s<n>] + its
    note) — the only shrinkable child (min-width:0) — collapsed to ~1 char and wrapped one letter
    per line: a tall, thin vertical column. .goal-row now wraps and .goal-meta keeps a min-width
    floor, so the meta stays wider than it is tall."""
    ui_page.set_viewport_size({"width": 390, "height": 900})
    ui.seed_run("uir", "20260715-160000", "finished", summary="done")
    (ui.routine_dir("uir") / "state").mkdir(parents=True, exist_ok=True)
    atomic_write_json(ui.routine_dir("uir") / "state" / "stopping.json", {
        "mode": "all", "groups": [{"id": "g1", "name": "review iteration", "mode": "all"}],
        "conditions": [
            {"id": "s1", "status": "met", "group": "g1",
             "text": "the collaborative steward page is published with login as Mark or "
                     "Florence, read-only lock for the other, and a ten-minute inactivity "
                     "auto-logout with a visible explained countdown, and edits persisted",
             "note": "with one host-limited caveat - the page cannot enforce a true concurrent "
                     "lock, only a best-effort inactivity auto-logout"}]})
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-160000")

    meta = ui_page.locator(".goal-meta").first
    expect(meta).to_be_visible()
    box = meta.evaluate("el => { const r = el.getBoundingClientRect(); "
                        "return {w: r.width, h: r.height}; }")
    # the crush symptom is a vertical character column: width << height. A readable meta wraps at
    # ~34ch and is wider than it is tall, whatever the exact px.
    assert box["w"] > box["h"], (
        f".goal-meta is a {box['w']:.0f}x{box['h']:.0f} vertical column "
        "(crushed one char per line) on a narrow run view")


def test_the_verdict_chip_reads_met_only_when_the_goal_is_satisfied(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, DOC)
    ui_page.reload()
    expect(ui_page.locator(".goal-verdict")).to_have_text("in progress")

    # the root is ANY, so satisfying the escape-hatch group alone ends the job
    _goal(conv_dir, {**DOC, "conditions": [
        *DOC["conditions"][:2],
        {"id": "s3", "text": "the user says stop", "status": "met", "group": "g2",
         "scope": "goal"}]})
    ui_page.reload()
    # "retired" is in the chip because it is what actually happens: a satisfied final goal stops
    # the routine running, and a chip that said only "goal met" would understate it
    expect(ui_page.locator(".goal-verdict")).to_have_text("goal met — retired")
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
                     "conditions": [
                         {"id": "s1", "text": "a", "status": "open", "group": "g1",
                          "scope": "goal"},
                         {"id": "s2", "text": "b", "status": "met", "group": "g1",
                          "scope": "goal"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-verdict")).to_have_text("in progress")

    ui_page.locator(".goal-group .goal-mode").click()   # ALL -> ANY
    ui_page.get_by_role("button", name="save goal").click()
    expect(ui_page.locator("#toast")).to_contain_text("goal saved")
    # one met member now satisfies the group, so the whole goal is met
    expect(ui_page.locator(".goal-verdict")).to_have_text("goal met — retired")
    assert json.loads((conv_dir / "state" / "stopping.json").read_text())["groups"][0]["mode"] \
        == "any"


def test_no_conditions_explains_what_the_panel_is_for(ui, ui_page):
    """An empty panel that just says "none" teaches nobody why they would want one."""
    _slug, _conv_dir = _start_conversation(ui, ui_page)
    expect(ui_page.locator(".goals")).to_contain_text("No stopping conditions")
    expect(ui_page.locator(".goals")).to_contain_text("runaway backstop")
    expect(ui_page.locator(".goal-verdict")).to_have_count(0)   # no goal set → no verdict


def test_a_disputed_verdict_is_visible_not_buried(ui, ui_page):
    """v2: the verifier objected and the run re-asserted, so the verdict stands — but a
    disagreement nobody can see is the same mistake that lost the panel in the first place."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {"mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
                     "conditions": [{"id": "s1", "text": "verify it", "status": "met",
                                     "group": "g1", "note": "I checked",
                                     "disputed": "no action opened the file"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-row.met")).to_have_count(1)     # the verdict stands
    disputed = ui_page.locator(".goal-disputed")
    expect(disputed).to_be_visible()
    expect(disputed).to_have_attribute("title", "a check of the run's transcript disagreed: "
                                                "no action opened the file")


# ---- the scope toggle: which conditions can end the ROUTINE, not just the run --------------------

def test_a_run_bound_shows_last_runs_verdict_and_no_goal_chip(ui, ui_page):
    """The scope split made visible. A per-run bound is re-asked every run, so its mark is not a
    state that carries forward — what the LAST run concluded is the only useful thing beside it,
    and it must read as history. And with no goal declared there is no verdict chip at all: a
    routine meant to run forever has no finish line to report on."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {"mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
                     "conditions": [{"id": "s1", "text": "one increment landed", "status": "open",
                                     "group": "g1", "scope": "run", "last_verdict": "met",
                                     "note": "shipped the parser fix"}]})
    ui_page.reload()
    row = ui_page.locator(".goal-row").first
    expect(row).to_have_class("goal-row open scope-run")
    expect(row.locator(".goal-scope")).to_have_text("per run")
    expect(row).to_contain_text("last run: met — shipped the parser fix")
    expect(ui_page.locator(".goal-verdict")).to_have_count(0)


def test_switching_a_condition_to_the_final_goal_persists(ui, ui_page):
    """Only this panel can create a goal condition — no run writes this file — which is what
    makes it safe for a met goal to retire the routine."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    _goal(conv_dir, {"mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
                     "conditions": [{"id": "s1", "text": "the application is submitted",
                                     "status": "open", "group": "g1"}]})
    ui_page.reload()
    expect(ui_page.locator(".goal-scope")).to_have_text("per run")

    ui_page.locator(".goal-scope").click()               # per run -> final goal
    expect(ui_page.locator(".goal-scope")).to_have_text("final goal")
    ui_page.get_by_role("button", name="save goal").click()
    expect(ui_page.locator("#toast")).to_contain_text("goal saved")

    stored = json.loads((conv_dir / "state" / "stopping.json").read_text())
    assert stored["conditions"][0]["scope"] == "goal"
    # a goal now exists, so the panel has a verdict to report
    expect(ui_page.locator(".goal-verdict")).to_have_text("in progress")
