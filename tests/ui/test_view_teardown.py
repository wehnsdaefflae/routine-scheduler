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

import re

from playwright.sync_api import expect

from .conftest import until
from .helpers import start_conversation, watch_requests

QUIET_MS = 4500      # > the tree poll (3 s) and the activity feed's (4 s)
STATUS_QUIET_MS = 6500   # three of the restart watch's 2 s polls, against at most one lamp read
API = re.compile("/api/")    # every request the console makes to the daemon


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

    after = watch_requests(ui_page, API)
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

    after = watch_requests(ui_page, API)
    ui_page.wait_for_timeout(QUIET_MS)
    leaked = [u for u in after if "/api/" in u and "20260714-070000" in u]
    assert not leaked, f"requests for a run nobody is looking at: {leaked}"


def test_a_missing_run_does_not_keep_polling_for_itself(ui, ui_page):
    """A pruned run's link (a stale search hit, an old notification) renders "Run not found" —
    and used to return no teardown at all, so the rail it had already mounted kept asking for
    the missing run's task tree every three seconds while that page stood, and its duration
    clock ticked on for the rest of the tab's life."""
    after = watch_requests(ui_page, API)
    ui_page.goto(f"{ui.url}/#/run/uir:20200101-000000")
    expect(ui_page.locator("#view")).to_contain_text("Run not found")
    ui_page.wait_for_timeout(QUIET_MS)
    tree = [u for u in after if "20200101-000000/tree" in u]
    assert len(tree) <= 1, f"the missing run's task tree was polled: {tree}"


def test_a_transcript_page_landing_after_you_left_does_not_scroll_the_next_page(ui, ui_page):
    """The live tail's REST catch-up delivers its page of events after an await — and the view
    it delivered to may be gone by then. Its onEvent follows the newest message by scrolling
    the WINDOW to the bottom, so a long transcript answering after the reader had moved on
    yanked the page they had moved to down to its end."""
    ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    held = []

    def hold_first(route):
        if held:
            route.continue_()
        else:
            held.append(route)

    ui_page.route("**/api/runs/uir:20260714-070000/transcript?offset=0", hold_first)
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    until(lambda: held, what="the transcript page to be asked for", page=ui_page)
    ui_page.evaluate("location.hash = '#/library'")
    expect(ui_page.locator("h2", has_text="Global utils")).to_be_attached()   # long enough to scroll
    ui_page.evaluate("window.scrollTo(0, 0)")
    assert ui_page.evaluate("document.body.scrollHeight > window.innerHeight * 2")

    held[0].continue_()
    ui_page.wait_for_timeout(800)
    assert ui_page.evaluate("window.scrollY") == 0, "the dead run view scrolled the Library"


def test_a_run_view_that_fails_mid_render_leaves_no_broken_callback(ui, ui_page):
    """A render that throws part-way shows "view failed to load" — and whatever it had armed by
    then keeps running. The run view armed its duration clock ~200 lines above the `let` of the
    state that clock reads, so a throw in between left a timer hitting "Cannot access
    'curState' before initialization" every five seconds for the life of the tab."""
    ui.seed_run("uir", "20260714-070000", "running")
    # the run rail's grip is the one caller of this query: make mounting it throw
    ui_page.add_init_script("""(() => {
      const real = window.matchMedia.bind(window);
      window.matchMedia = (q) => {
        if (q === "(min-width: 760px)") throw new Error("injected rail failure");
        return real(q);
      };
    })()""")
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    expect(ui_page.locator("#view")).to_contain_text("view failed to load")
    ui_page.wait_for_timeout(5600)          # past the duration clock's 5 s tick
    # the ui_page fixture fails the test on any uncaught error collected meanwhile


def test_leaving_right_after_a_send_does_not_remount_the_conversation(ui, ui_page):
    """A send that wakes a DIFFERENT run remounts the conversation 700 ms later. Left inside
    that window, the remount ran anyway — into a view already torn down, arming a live tail
    (a live-stream socket), two rail pollers and a scroll listener that nothing would ever stop."""
    slug, _conv_dir = start_conversation(ui, ui_page, "Plan the trip.")
    # the run the view follows is NOT the one the stub runner resumes into, so the send takes
    # the fresh-run remount branch rather than re-attaching the tail in place
    ui.seed_run(slug, "20260714-070000", "finished", home=ui.conversations, summary="done")
    ui_page.reload()
    expect(ui_page.locator(".conv-composer")).to_be_visible()

    ui_page.locator(".conv-view textarea").last.fill("and book the train")
    ui_page.get_by_role("button", name="send").click()
    expect(ui_page.locator(".msg.user.pending")).to_be_visible()
    _leave(ui_page)

    after = watch_requests(ui_page, API)
    ui_page.wait_for_timeout(1500)       # past the 700 ms remount
    leaked = [u for u in after if slug in u]
    assert not leaked, f"the torn-down conversation remounted itself: {leaked}"


def test_leaving_mid_search_leaves_the_next_page_alone(ui, ui_page, make_routine):
    """The Messages search reloads on a 250 ms debounce. Left inside it, the timer still fired:
    it fetched the item list for a page nobody was on, and wrote the Messages filters into the
    NEXT page's URL — where a reload would carry them."""
    make_routine(slug="self-audit")
    ui_page.goto(f"{ui.url}/#/messages")
    search = ui_page.locator("input.search")
    expect(search).to_be_visible()
    search.fill("thing")
    _leave(ui_page)

    after = watch_requests(ui_page, API)
    ui_page.wait_for_timeout(800)
    assert "search=" not in ui_page.url and "status=" not in ui_page.url, ui_page.url
    items = [u for u in after if "/api/items" in u]
    assert not items, f"the Messages search ran after the page was left: {items}"


def test_a_late_remount_does_not_rebuild_the_page_you_moved_to(ui, ui_page):
    """`↻ resume run` remounts its view 800 ms after the click lands. Left inside that window,
    the remount re-rendered whatever page had replaced it — here Help, whose index fetch
    shows it rebuilding itself under the reader."""
    ui.seed_run("uir", "20260715-070000", "failed", summary="died")
    ui_page.route("**/api/runs/uir:20260715-070000/resume-run", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"ok": true}'))
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-070000")
    resume = ui_page.get_by_role("button", name="↻ resume run")
    expect(resume).to_be_visible()
    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if "/docs/index.json" in r.url else None)
    resume.click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("resuming")
    _leave(ui_page)
    rendered = len(seen)                 # Help's own render fetched its index once
    ui_page.wait_for_timeout(1500)       # past the 800 ms remount
    assert len(seen) == rendered, "the run view's late remount re-rendered the Help page"


def test_a_late_library_delete_does_not_pull_you_back(ui, ui_page):
    """Deleting a settings pattern drops its deep link and remounts the Library once the DELETE
    returns. A reader who left while it was in flight was carried BACK to the Library — the
    URL rewritten to #/library under the page they had chosen, then re-rendered."""
    held = []
    ui_page.route("**/api/patterns/watcher", lambda route: held.append(route)
                  if route.request.method == "DELETE" else route.continue_())
    ui_page.goto(f"{ui.url}/#/library/pattern/watcher")
    row = ui_page.locator('.pat[data-pattern="watcher"]')
    expect(row.locator(".pat-body")).to_be_visible()
    row.locator("[data-delete-pattern]").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="delete", exact=True).click()
    until(lambda: held, what="the DELETE to be sent", page=ui_page)
    _leave(ui_page)

    held[0].continue_()
    ui_page.wait_for_timeout(800)
    assert "#/help" in ui_page.url, f"the late delete navigated away from Help: {ui_page.url}"
    expect(ui_page.locator("#view h1")).to_have_text("Help")


def test_collapsing_the_activity_section_stops_its_poll(ui, ui_page):
    """The activity feed polls THREE endpoints every 4 s while a run is active. It is started
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

    after = watch_requests(ui_page, API)
    ui_page.wait_for_timeout(QUIET_MS)
    # the dashboard's own load() asks for /api/routines on a bus tick; the feed's signature is
    # the 300-run window, which nothing else on the page requests
    feed = [u for u in after if "limit=300" in u]
    assert not feed, f"the activity feed kept polling while collapsed: {feed}"


def test_leaving_settings_stops_the_restart_watch(ui, ui_page):
    """Settings → Server watches a requested restart by asking /api/status every 2 s, for up to
    three minutes — and it is armed on RENDER whenever a restart is already pending. Unlike the
    GitHub and OAuth flows on the same page it never asked whether its panel was still on
    screen, so leaving Settings left that poll running for the full three minutes, and every
    visit while the restart waited for its quiet gap started one more."""
    def pending(route):
        response = route.fetch()
        route.fulfill(response=response, json={**response.json(), "restart_requested": True})
    ui_page.route("**/api/status", pending)

    ui_page.goto(f"{ui.url}/#/settings?section=server")
    expect(ui_page.locator("#view")).to_contain_text("restart is already requested")
    _leave(ui_page)

    after = watch_requests(ui_page, API)
    ui_page.wait_for_timeout(STATUS_QUIET_MS)
    polls = [u for u in after if u.endswith("/api/status")]
    # one may be the daemon lamp's own 30 s backstop landing inside the window; the watch's
    # 2 s cadence puts three here
    assert len(polls) <= 1, f"the restart watch outlived Settings: {len(polls)} status reads"


def test_a_pending_file_card_refresh_dies_with_its_card(ui, ui_page):
    """The files card coalesces a burst of file observations into one refetch 1.5 s later
    (components/fileactivity.js poke). Leaving the view inside that window used to send the
    refetch anyway, for a card nobody can see."""
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/#/help")
    ui_page.wait_for_selector("#view h1")
    ui_page.evaluate("""async () => {
      const { createFileActivity } = await import("/static/components/fileactivity.js");
      const box = document.createElement("div");
      document.body.append(box);
      window.__card = { box, files: createFileActivity(box,
        { url: "/api/runs/uir:20260714-070000/files" }) };
    }""")
    expect(ui_page.locator(".filelist")).to_have_count(1)
    ui_page.wait_for_timeout(500)                     # its own first read is out and back

    after = watch_requests(ui_page, API)
    ui_page.evaluate("() => { window.__card.files.poke(); window.__card.box.remove(); }")
    ui_page.wait_for_timeout(2500)                    # > the 1.5 s coalescing window
    files = [u for u in after if u.endswith("/files")]
    assert not files, f"the files card refetched after it was gone: {files}"


def test_a_task_tree_stopped_before_its_first_read_lands_stays_stopped(ui, ui_page):
    """stop() cleared the timer and nothing else, so a stop() that landed while the FIRST
    refresh was still in flight was undone by that refresh's `.then(poll)` — the poll re-armed
    and kept asking every three seconds. The run view's "run not found" path does exactly
    that, and carried a liveness flag of its own only to paper over it."""
    held = []
    ui_page.route("**/api/runs/uir:20260714-070000/tree",
                  lambda route: route.continue_() if held else held.append(route))
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate("""() => import('/static/components/tasktree.js').then((m) => {
        const host = document.createElement('div');
        document.body.append(host);
        window.__tree = m.createTaskTree(host, {
          treeUrl: '/api/runs/uir:20260714-070000/tree', isLive: () => true });
    })""")
    until(lambda: held, what="the first tree read to be asked for", page=ui_page)
    ui_page.evaluate("window.__tree.stop()")
    after = watch_requests(ui_page, API)
    held[0].continue_()
    ui_page.wait_for_timeout(QUIET_MS)
    tree = [u for u in after if u.endswith("/tree")]
    assert not tree, f"a stopped task tree kept polling: {tree}"


def test_leaving_a_development_view_stops_its_run_finished_reread(ui, ui_page):
    """The Changes views re-read on a finished run, debounced (components/run-finished.js). A
    finish that lands after the reader left must not reach the daemon: the listener and its
    pending timer both go with the view — and while it IS mounted, a finish is a reason."""
    ui_page.goto(f"{ui.url}/#/changes/uir")
    ui_page.wait_for_selector(".dev-none, table.chg-table")
    finished = """window.dispatchEvent(new CustomEvent("rsched-bus", { detail: {
        event: "run_finished", run_id: "uir:20260714-070000", state: "finished" } }));"""

    mounted = watch_requests(ui_page, API)
    ui_page.evaluate(finished)
    until(lambda: any("/api/changes/uir" in u for u in mounted), page=ui_page,
          what="the mounted view's re-read on a finished run")

    ui_page.evaluate(finished)                 # arms the debounce …
    _leave(ui_page)                            # … and the view goes before it fires
    after = watch_requests(ui_page, API)
    ui_page.evaluate(finished)
    ui_page.wait_for_timeout(QUIET_MS)
    leaked = [u for u in after if "/api/changes" in u or u.endswith("/health")]
    assert not leaked, f"a development view re-read after it was gone: {leaked}"
