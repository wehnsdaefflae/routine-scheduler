"""The run page's ROUTINE RAIL (components/routine-rail.js) — the conversation list's counterpart.

OPERATOR ORDER (F619, 2026-10-04): *"i want a list of all routines next to a routine run page
just like there's a list of all conversations next to a conversation. the only difference is that
the routines can be expanded to the previous runs"*.

Four claims, all about what a PERSON sees beside a run:

- every routine is listed in a LEFT rail beside the run, the way `views/conversations.js` lists
  conversations beside a chat — and the routine THIS run belongs to is the marked one;
- the marked routine arrives EXPANDED to its previous runs, each one a link into that run: the
  "only difference" the order names is the whole point of the rail, so it is not behind a click
  on the row you are already reading;
- another routine's row starts closed and expands on click, fetching ITS runs only then — a rail
  that listed forty routines' runs on arrival would cost forty reads nobody asked for;
- the rail obeys the rail contract the right rail already has (static/resizable.js): a resizable
  column while the viewport can hold one, and CLOSED on a phone, where it stacks in the page flow
  and an open one would put every routine above the run the reader came for.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .helpers import watch_requests

TS = "20261012-090000"
OLD_TS = "20261011-090000"
WIDE = {"width": 1425, "height": 900}
PHONE = {"width": 390, "height": 844}


def _rail(page):
    return page.locator(".run-view > .run-rail.left")


def test_the_run_page_lists_every_routine_beside_the_run(ui, ui_page, make_routine):
    make_routine(slug="zzz")
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    rail = _rail(ui_page)
    expect(rail).to_be_visible()
    expect(rail.locator("[data-routine='uir']")).to_contain_text("Test uir")
    expect(rail.locator("[data-routine='zzz']")).to_contain_text("Test zzz")
    # the routine this run belongs to is the marked one, as the open conversation is in its rail
    expect(rail.locator("[data-routine='uir'] > .rr-row")).to_have_class(re.compile(r"\bon\b"))
    expect(rail.locator("[data-routine='zzz'] > .rr-row")).not_to_have_class(re.compile(r"\bon\b"))
    # the row itself is the EXPANDER, so the way to the routine's own page is its own link —
    # a name that navigated would mean the one click the rail exists for never expands anything
    expect(rail.locator("[data-routine='zzz'] a.rr-open"))\
        .to_have_attribute("href", "#/routine/zzz")

    # a column to the LEFT of the run, which is what "next to" means
    rail_box, main_box = rail.bounding_box(), ui_page.locator(".run-main").bounding_box()
    assert rail_box["x"] + rail_box["width"] <= main_box["x"] + 1, (rail_box, main_box)


def test_the_routine_being_read_arrives_expanded_to_its_previous_runs(ui, ui_page):
    ui.seed_run("uir", TS, "finished", summary="done")
    ui.seed_run("uir", OLD_TS, "failed", summary="it broke")

    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    node = _rail(ui_page).locator("[data-routine='uir']")
    assert node.evaluate("e => e.open") is True, "the routine being read arrives collapsed"
    previous = node.locator(f"a.rr-run[href='#/run/uir:{OLD_TS}']")
    expect(previous).to_be_visible()
    expect(previous).to_contain_text("2026-10-11")          # the run's own date, not its id
    expect(previous).to_contain_text("failed")              # and what became of it
    # the run being read is marked among them, so the list says where you are
    expect(node.locator(f"a.rr-run[href='#/run/uir:{TS}']"))\
        .to_have_class(re.compile(r"\bon\b"))
    # and the expander SHOWS it is one: the caret comes from the stylesheet, per [open]
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector(\"[data-routine='uir'] > .rr-row\"),"
        " '::before').content") not in ("none", "normal", "")


def test_a_previous_runs_date_stays_on_one_line(ui, ui_page):
    """The rendered-look defect, pinned where it can be measured: at the rail's 218px the first
    draft let "2026-10-11 09:00 finished" break over two lines, and a column of half-wrapped
    dates reads as a fault. The date is the run's IDENTITY so it never wraps; the state word
    gives way instead, which it can — its dot carries the same meaning in colour.

    Found by LOOKING at the rail, not by the four green tests around this one (the lesson
    static/rail.css records for the jobs card's separator).
    """
    ui.seed_run("uir", TS, "finished", summary="done")
    ui.seed_run("uir", OLD_TS, "waiting_user", summary="asked")

    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    when = _rail(ui_page).locator(f"a.rr-run[href='#/run/uir:{OLD_TS}'] .rr-when")
    expect(when).to_be_visible()
    box = when.bounding_box()
    line = ui_page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('.rr-when')).lineHeight)")
    assert box["height"] <= line * 1.6, (box["height"], line)   # one line, not two


def test_another_routine_expands_on_click_and_only_then_reads_its_runs(ui, ui_page,
                                                                      make_routine):
    make_routine(slug="zzz")
    ui.seed_run("uir", TS, "finished", summary="done")
    ui.seed_run("zzz", OLD_TS, "finished", summary="zzz ran")

    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    node = _rail(ui_page).locator("[data-routine='zzz']")
    expect(node.locator(".rr-row")).to_be_visible()
    assert node.evaluate("e => e.open") is False, "every routine arrived expanded"
    expect(node.locator("a.rr-run")).to_have_count(0)

    seen = watch_requests(ui_page, re.compile(r"/api/runs\?"))
    node.locator(".rr-row").click()
    run = node.locator(f"a.rr-run[href='#/run/zzz:{OLD_TS}']")
    expect(run).to_be_visible()
    assert any("routine=zzz" in u for u in seen), f"zzz's runs were not read on expanding: {seen}"


def test_the_rail_is_a_column_while_one_fits_and_stays_out_of_the_way_on_a_phone(ui, ui_page):
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(_rail(ui_page)).to_be_visible()
    expect(ui_page.locator(".run-view > .sb-grip.runrail-l")).to_be_visible()

    # the phone tier: it stacks in the flow with no grip, and CLOSED — the rail contract
    # static/resizable.js holds for every rail, decided at mount, so the view is re-entered
    ui_page.set_viewport_size(PHONE)
    ui_page.reload()
    rail = _rail(ui_page)
    expect(rail).to_be_visible()
    assert rail.evaluate("e => e.open") is False, "the routine list opens over the run on a phone"
    expect(ui_page.locator(".run-view > .sb-grip.runrail-l")).to_be_hidden()
