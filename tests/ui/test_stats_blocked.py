"""The fleet's BLOCKED WORK, on a page someone opens (R2653).

`readmodels/health_stream.py` and `GET /api/health/blocked` shipped, and no console ever read
them: measured at 0.432.0, `health/blocked` had no hit anywhere in `static/`. The endpoint's own
handler docstring explains that it ships a `vocabulary` payload "so the console renders a label
it was not compiled with" — UI design for a consumer that did not exist. So a refused fire, a
stopped lane chain and a capped trigger, none of which produces a run, stayed as invisible as
they were before the read model: F316's week of missed lane fires passed with zero signal.

ZERO IS THE GOOD READING, which is the whole reason the panel renders when there is nothing to
show: an absent panel and a healthy one are indistinguishable, and only one reassures. Both
directions are pinned here, through the real page.
"""

import json

from playwright.sync_api import expect


def _blocked(ui, rows) -> None:
    """Put health events on the instance's stream, the way the engine writes them."""
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "health-events.jsonl").open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _now_iso(page) -> str:
    """A stamp inside the fold's 7-day window, in the shape `ids.now_iso` writes (local time
    with its offset) — the fold compares instants, so the offset must be there."""
    return page.evaluate("() => new Date().toISOString().replace('Z', '+00:00')")


def _a_counted_run(ui) -> None:
    """Stats renders its body only once the instance HAS a run (`if (!t.runs)` short-circuits
    to an empty state), so every case here needs one — the blocked panel is a section of that
    page, not a page of its own."""
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")


def test_the_panel_renders_what_the_fleet_owed_and_did_not_do(ui, ui_page):
    _a_counted_run(ui)
    ts = _now_iso(ui_page)
    _blocked(ui, [
        {"ts": ts, "event": "fire_refused", "routine": "uir", "run_id": "",
         "detail": "the routine was still active"},
        {"ts": ts, "event": "fire_refused", "routine": "uir", "run_id": "",
         "detail": "the routine was still active (again)"},
        {"ts": ts, "event": "backup_stale", "routine": "", "run_id": "",
         "detail": "no completed snapshot for 4 days"},
    ])
    ui_page.goto(f"{ui.url}/#/stats")
    section = ui_page.locator(".stat-section", has=ui_page.get_by_role("heading",
                                                                      name="Blocked work"))
    expect(section).to_be_visible()
    # the event, the SERVER's label for it, the count, and the newest detail
    expect(section).to_contain_text("fire_refused")
    expect(section).to_contain_text("a fire produced no run")
    expect(section).to_contain_text("the routine was still active (again)")
    expect(section).to_contain_text("backup_stale")
    # a routine subject links to its page; an instance-wide event says so instead of a blank
    expect(section.get_by_role("link", name="uir")).to_be_visible()
    expect(section).to_contain_text("the instance")


def test_a_healthy_fleet_still_gets_the_panel_saying_so(ui, ui_page):
    """An absent panel and a healthy one are indistinguishable — so the healthy one exists."""
    _a_counted_run(ui)
    ui_page.goto(f"{ui.url}/#/stats")
    section = ui_page.locator(".stat-section", has=ui_page.get_by_role("heading",
                                                                      name="Blocked work"))
    expect(section).to_be_visible()
    expect(section).to_contain_text("nothing blocked")


def test_a_corrected_run_leaves_the_panel_and_its_lane_close_says_so(ui, ui_page):
    """F637/F703. A run that ended not-ok, was resumed and finished `ok` must read `ok` here
    too: its own row goes (the work was done), while the lane close it stopped STAYS — those
    members really did not run — and stops claiming the member failed.
    """
    _a_counted_run(ui)
    ts = _now_iso(ui_page)
    _blocked(ui, [
        {"ts": ts, "event": "wrong_turn_detected", "routine": "uir", "run_id": "uir:r1",
         "reason": "attempts_exhausted", "turn": 4, "detail": "turn 4: out of attempts"},
        {"ts": ts, "event": "lane_chain_stopped", "routine": "grp-77", "run_id": "lr-4",
         "detail": "morning: 2 member runs, 1 not-ok (uir)", "stopped_by": "uir:r1"},
        {"ts": ts, "event": "run_corrected", "routine": "uir", "run_id": "uir:r1",
         "superseded": ["wrong_turn_detected"], "detail": "resumed and finished ok"},
    ])
    ui_page.goto(f"{ui.url}/#/stats")
    section = ui_page.locator(".stat-section", has=ui_page.get_by_role("heading",
                                                                      name="Blocked work"))
    expect(section).to_be_visible()
    expect(section).not_to_contain_text("wrong_turn_detected")
    expect(section).to_contain_text("lane_chain_stopped")
    expect(section).to_contain_text("superseded")
    expect(section).to_contain_text("corrected")


def test_a_lane_subject_is_not_parsed_into_a_route(ui, ui_page):
    """A lane id is opaque by the fold's own contract: the row links to where lanes live, and
    never to a route built out of the id's characters."""
    _a_counted_run(ui)
    _blocked(ui, [{"ts": _now_iso(ui_page), "event": "lane_chain_stopped",
                   "routine": "grp-3235d2ee", "run_id": "", "detail": "member 2 failed"}])
    ui_page.goto(f"{ui.url}/#/stats")
    section = ui_page.locator(".stat-section", has=ui_page.get_by_role("heading",
                                                                      name="Blocked work"))
    link = section.get_by_role("link", name="a lane")
    expect(link).to_have_attribute("href", "#/routines")
    expect(section).to_contain_text("grp-3235d2ee")
