"""A view that is gone must stop asking the daemon for things.

Every poller in the console is armed by a view and has to be disarmed by that view's
teardown. Nothing in the suite could see a request that fires AFTER navigation, which is how
the run view shipped a task-tree poller it never stopped: its `isLive()` predicate reads a
state variable frozen at whatever it held when the view was torn down, so leaving a live run
left a GET /api/runs/<id>/tree every three seconds for the rest of the browser session — one
more per run page visited, serving a rail nobody could see.

The check is deliberately blunt and route-shaped: mount a view over the stub runner, navigate
to a page that polls nothing, and assert that the old view's paths go silent. A poller that
outlives its view fails here whatever arms it.
"""

from __future__ import annotations

from playwright.sync_api import expect

QUIET_MS = 4500      # > the tree poll (3 s) and the activity feed's (4 s)


def _watch(page):
    """Collect every /api request path from now on. Returns the list (it keeps filling)."""
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "/api/" in r.url else None)
    return seen


def _leave(page):
    """Leave for Help — a static view with no bus listener and no poller of its own. An
    in-page hash change, because that is how an operator leaves: the SPA's teardown runs and
    the browser never reloads."""
    page.evaluate("location.hash = '#/help'")
    page.wait_for_selector("#view h1")


def test_run_view_stops_its_task_tree_poll(ui, ui_page):
    """The finding this file exists for: a LIVE run's tree poller must die with the view."""
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    # the rail's tasks card is up, so the poller is armed
    expect(ui_page.locator(".tasktree")).to_be_visible()
    ui_page.wait_for_timeout(3500)          # let at least one poll round happen while mounted
    _leave(ui_page)

    after = _watch(ui_page)
    ui_page.wait_for_timeout(QUIET_MS)
    tree = [u for u in after if "/tree" in u]
    assert not tree, f"the task-tree poll outlived the run view: {tree}"


def test_leaving_a_run_silences_every_run_scoped_request(ui, ui_page):
    """The general rule, not just the one poller: after teardown nothing keyed to that run may
    still be asked for — tree, files, plan, transcript or events."""
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    expect(ui_page.locator(".tasktree")).to_be_visible()
    _leave(ui_page)

    after = _watch(ui_page)
    ui_page.wait_for_timeout(QUIET_MS)
    leaked = [u for u in after if "/api/" in u and "20260714-070000" in u]
    assert not leaked, f"requests for a run nobody is looking at: {leaked}"


def test_collapsing_the_activity_section_stops_its_poll(ui, ui_page):
    """The activity feed polls FOUR endpoints every 4 s while a run is active. It is started
    lazily when its section opens — and has to stop again when the section closes, or one
    click open and one click closed leaves a request a second running for content nobody can
    see, for the life of the tab."""
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/#/routines")
    panel = ui_page.locator(".activity-panel")
    expect(panel).to_be_visible()
    panel.locator("summary").click()                       # open — the feed starts
    expect(panel.locator(".logbar")).to_be_visible()
    panel.locator("summary").click()                       # closed again
    expect(panel).not_to_have_attribute("open", "")

    after = _watch(ui_page)
    ui_page.wait_for_timeout(QUIET_MS)
    # the dashboard's own load() asks for /api/routines on a bus tick; the feed's signature is
    # the 300-run window, which nothing else on the page requests
    feed = [u for u in after if "limit=300" in u]
    assert not feed, f"the activity feed kept polling while collapsed: {feed}"
