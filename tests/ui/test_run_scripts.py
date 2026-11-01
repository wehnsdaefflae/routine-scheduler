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


def test_the_section_is_captured_for_a_person_to_look_at(ui, ui_page):
    """LEAVE THE CAPTURE BEHIND, at both widths (interface-craft).

    Every other test here asserts a property; none of them LOOKS. The 0.488.0 release shipped
    after a red gate found a defect no assertion of mine had named — a script whose filename and
    whose docstring header spell its name differently kept a redundant lead — and the thing that
    found it was reading the failure dump, i.e. looking. So: write the rail section to a PNG at
    desktop and at phone width and name the path, because a rail that collapses under 980px is
    exactly where a new section goes wrong unseen.

    The capture goes under `<repo>/.audit-wt/captures/` — inside the checkout, which is what the
    `uitest` container mounts, and gitignored (.gitignore:26). `RSCHED_GATE_ARTIFACTS`, the
    convention test_message_concepts_render.py uses, matches no carried env prefix
    (deploy/uitest/protocol.py:61) and so cannot reach a browser-stage job; `RSCHED_UI_` can.

    TWO THINGS THE CAPTURES DO NOT SHOW, so nobody reads them for more than they are:

    - an element screenshot captures the page REGION the element occupies, so the fixed bottom
      bar paints over the phone capture, and the rail is clipped at about viewport height — the
      second script card is below that fold. The assertions, not the image, are what cover it.
    - the probe below reads `covered: False` only AFTER scrolling to the end of the document.
      Probing straight after `scroll_into_view_if_needed` reported the card covered by the
      bottom bar, and that reading is an artefact of the minimal scroll, not a defect: it would
      report EVERY last element on every page as covered. It nearly became a finding of mine.
    """
    import os
    from pathlib import Path

    (ui.routine_dir("uir") / "scripts" / "tidy_up.py").write_text(SECOND, encoding="utf-8")
    ui.seed_run("uir", TS, "finished", summary="done")

    out = Path(os.environ.get("RSCHED_UI_CAPTURES")
               or Path(__file__).resolve().parents[2] / ".audit-wt" / "captures")
    out.mkdir(parents=True, exist_ok=True)

    shots = []
    for label, width in (("desktop", 1425), ("phone", 390)):
        ui_page.set_viewport_size({"width": width, "height": 900})
        ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
        expect(ui_page.locator("[data-run-script='outcome']")).to_be_visible()
        # the whole rail, not the card alone: a section is wrong in CONTEXT, not in isolation.
        # `.run-rail` alone matches TWO elements — the left rail (`.run-rail.left`, the routine
        # navigator) and the right one. His order was about the RIGHT sidebar, so pin that.
        rail = ui_page.locator(".run-rail:not(.left)")
        path = out / f"run-scripts-{label}.png"
        rail.screenshot(path=path)
        shots.append(path)
        # and the capture must not be of an empty box: both cards present at BOTH widths
        assert ui_page.locator("[data-run-script]").count() == 2, (
            f"only {ui_page.locator('[data-run-script]').count()} script card(s) at {label} "
            f"width {width} — the capture would show less than the desktop assertions claim")
        box = rail.bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, f"{label}: rail has no box {box}"

        # LOOKING AT THE PHONE CAPTURE RAISED THIS and the assertions had not: SCRIPTS is the
        # LAST rail section, and at phone width the console's bottom dock floats over the rail.
        # A section a person must scroll to is fine; one permanently under the dock is not.
        if label == "phone":
            last = ui_page.locator("[data-run-script]").last
            last.scroll_into_view_if_needed()
            # …and then ALL THE WAY DOWN. `scroll_into_view_if_needed` does the MINIMAL scroll,
            # which parks the element's bottom exactly at the viewport edge — i.e. under the
            # fixed bottom bar — so probing after it alone reports every last element as
            # "covered" whatever the stylesheet does. That artefact nearly became a finding of
            # mine. The real question is whether the page can be scrolled far enough to clear
            # the bar, which is what `.workspace`'s `padding-bottom: 62px` (base.css:870) is
            # for; so scroll to the document end first and probe THERE.
            ui_page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            ui_page.wait_for_timeout(150)
            reachable = ui_page.evaluate("""() => {
              const probe = (el) => {
                if (!el) return null;
                const r = el.getBoundingClientRect();
                const hit = document.elementFromPoint(r.left + r.width / 2,
                                                      r.top + r.height / 2);
                return {covered: !(el === hit || el.contains(hit)),
                        by: hit ? (hit.className || hit.tagName) : null,
                        tag: el.tagName, cls: String(el.className).slice(0, 40),
                        top: Math.round(r.top), bottom: Math.round(r.bottom)};
              };
              const cards = document.querySelectorAll('[data-run-script]');
              const rail = document.querySelector('.run-rail:not(.left)');
              return {card: probe(cards[cards.length - 1]),
                      railLast: probe(rail && rail.lastElementChild),
                      railPadBottom: rail ? getComputedStyle(rail).paddingBottom : null,
                      vh: window.innerHeight};
            }""")
            print("phone geometry:", reachable)  # noqa: T201 — the measurement, named
            # THE CLAIM: scrolled to the end of the page, a reader can see and touch the last
            # script card — it is not stranded under the fixed bottom bar. That is what
            # `.workspace { padding-bottom: 62px }` (base.css:870) guarantees, and a new LAST
            # rail section is exactly where such a guarantee gets lost unnoticed. `railLast` is
            # kept in the dump as the control: if BOTH read covered, the finding is the rail's,
            # not this section's.
            assert not reachable["card"]["covered"], (
                "scrolled to the end of the page, the last script card is still covered by "
                f"{reachable['card']['by']!r} — SCRIPTS is the last rail section and the phone "
                f"bottom bar is over it: {reachable}")
    print("captures:", ", ".join(str(p) for p in shots))  # noqa: T201 — where to look


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
