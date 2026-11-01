"""The run rail's SCRIPTS section (components/runscripts.js).

OPERATOR ORDER 2026-11-01: *"a routine's scripts should have a section in the right routine run
sidebar"*. A routine's `scripts/` are the tooling its runs call every fire, and the run page —
where a run is actually watched — named none of them: the only way to see what a run could call
was to open the recipe browser and list a directory.

Four claims, all about what a PERSON sees:

- a run page names each of the routine's scripts, with the one-line summary from its own
  docstring header, in a foldable rail section like every other;
- the name is not repeated in the line below it (`parse_header` keeps the whole first line, so
  the summary arrives as "outcome — what this routine…"; the rail is narrow);
- consecutive scripts are separate blocks — the jobs card's run-on-paragraph defect (F646,
  `static/rail.css`) would otherwise repeat here, since this card reuses that class;
- a routine with NO scripts/ gains no caption, and the section's read does not poll: a routine's
  script set is a property of the recipe, not of the run.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .helpers import watch_requests

TS = "20261101-090000"

SECOND = '''# /// script
# dependencies = []
# ///
"""tidy-up — delete the temp files a run leaves behind.

usage:
  tidy-up [--dry-run]
"""
print("ok")
'''


def test_the_run_page_names_the_routines_scripts(ui, ui_page):
    # the fixture routine already carries scripts/outcome.py (helpers.declare_outcome)
    (ui.routine_dir("uir") / "scripts" / "tidy_up.py").write_text(SECOND, encoding="utf-8")
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    cap = ui_page.locator('.run-rail .rail-cap[data-rail="scripts"]')
    expect(cap).to_be_visible()
    outcome = ui_page.locator("[data-run-script='outcome']")
    tidy = ui_page.locator("[data-run-script='tidy_up']")
    expect(outcome).to_be_visible()
    expect(tidy).to_be_visible()
    expect(tidy).to_contain_text("tidy_up")
    expect(tidy).to_contain_text("delete the temp files a run leaves behind.")
    # the summary's leading "<name> —" is stripped: the name is already the heading above it
    assert "tidy-up —" not in tidy.inner_text()


def test_each_script_is_its_own_block_from_the_stylesheet(ui, ui_page):
    """The rendered-look claim, pinned where a class can be checked. The jobs card's hairline
    (static/rail.css, F646) keyed only on `[data-run-job]`, so scripts reusing `.runjob` would
    read as one run-on paragraph — the exact defect that rule was added for."""
    (ui.routine_dir("uir") / "scripts" / "tidy_up.py").write_text(SECOND, encoding="utf-8")
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(ui_page.locator("[data-run-script='tidy_up']")).to_be_visible()
    # the FIRST block has nothing above it to separate from, the second carries the hairline
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector(\"[data-run-script='outcome']\"))"
        ".borderTopWidth") == "0px"
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector(\"[data-run-script='tidy_up']\"))"
        ".borderTopWidth") == "1px"


def test_a_routine_without_scripts_gains_no_section(ui, ui_page):
    for p in (ui.routine_dir("uir") / "scripts").glob("*.py"):
        p.unlink()
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(ui_page.locator(".run-rail .rail-cap").first).to_be_visible()
    caps = [c.strip().lower() for c in
            ui_page.locator(".run-rail .rail-cap").all_inner_texts()]
    assert "scripts" not in caps
    assert ui_page.locator("[data-run-script]").count() == 0


def test_the_scripts_read_does_not_poll(ui, ui_page):
    """ONE read, no timer. A routine's script set cannot change under a live run (the console
    queues recipe edits to run end), and `tests/ui/test_view_teardown.py` catches a leaked
    poller by the RUN id in its URL — this path is the ROUTINE's, so the claim is pinned here,
    the way the jobs section's is."""
    ui.seed_run("uir", TS, "running", summary="")
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(ui_page.locator("[data-run-script='outcome']")).to_be_visible()

    after = watch_requests(ui_page, re.compile("/api/"))
    ui_page.wait_for_timeout(6000)
    repeats = [u for u in after if "/scripts" in u]
    assert not repeats, f"the scripts section polled: {repeats}"
