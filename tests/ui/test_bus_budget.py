"""The machine check for CLAUDE.md's live-refresh rule: "a live refresh on a bus event
fetches only what that event can change".

`llm_task` / `llm_process` fire several times a second while a run works and cannot change a
routine's state, a decision, a schedule or a run list — every view is supposed to ignore them.
The rule lived only in prose, and three views broke it in three different ways: the Decisions
page refetched its whole question set on EVERY event with no filter and no timer; the watch
ribbon dragged a week-schedule computation along at 20 s; the dashboard's own guard was the
only one that had it right. The cost lands on the daemon, which is answering for the runs the
console is watching (2026-09-12: one config-shaped read every 600 ms, every request queued
20-50 s).

So: mount a view, dispatch a storm of those two kinds at it, and assert the console asks the
daemon for NOTHING. That is the rule exactly — not "no path more than once", which would fight
views that legitimately refetch on a run event.
"""

from __future__ import annotations

import pytest

STORM = """
for (let i = 0; i < 20; i++) {
  window.dispatchEvent(new CustomEvent("rsched-bus",
    { detail: { event: i % 2 ? "llm_task" : "llm_process", id: "t" + i, status: "running" } }));
}
"""

# The app shell's two periodic backstops, which are nobody's bus handler: the LLM dock mirrors
# llm_task/llm_process from the EVENT and re-reads /api/llm-tasks every 10 s (in a visible tab)
# only to catch events the bus dropped, and the daemon lamp re-reads /api/status every 30 s. Neither is
# triggered by an event, so neither is what this file is about.
BACKSTOPS = ("/api/llm-tasks", "/api/status")


def _counts(url):
    return "/api/" in url and not any(b in url for b in BACKSTOPS)


# One route per bus-listening view, with something on the page to wait for first so the test
# storms a MOUNTED view rather than a half-rendered one.
# Each `ready` marker is a POST-LOAD one: a request is recorded when it is issued, so the view's
# own mount fetches must all be out before the watch starts, or a slow box reds this for the
# wrong reason.
VIEWS = [
    ("#/questions", ".q-group-head, .empty"),
    ("#/routines", "table.list, .grid, .empty"),
    ("#/conversations", ".conv-new textarea"),
    ("#/help", "#view h1"),
    # the DEVELOPMENT views re-read on a finished run only (components/run-finished.js)
    ("#/changes", "[data-unmeasured], #sec-releases"),
    ("#/changes/uir", ".dev-none, table.chg-table"),
]


@pytest.mark.parametrize(("route", "ready"), VIEWS)
def test_llm_events_cost_the_daemon_nothing(ui, ui_page, route, ready):
    ui.seed_run("uir", "20260714-070000", "running")
    ui_page.goto(f"{ui.url}/{route}")
    ui_page.wait_for_selector(ready)
    ui_page.wait_for_timeout(800)          # and the global chrome's (ribbon, docks)

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if _counts(r.url) else None)
    ui_page.evaluate(STORM)
    # Every coalescer on this path has a LEADING edge, so a missing filter shows up at once;
    # the wait covers the dashboard's 2 s trailing debounce on top.
    ui_page.wait_for_timeout(2500)

    assert not seen, f"{route} asked the daemon for {seen} on llm_task/llm_process events"


def test_a_finished_run_reads_the_routine_detail_once(ui, ui_page):
    """The routine page answers its own routine's run_finished by re-reading what a run can
    move, and it read the routine's detail TWICE for it, a moment apart: once for the header
    chip and the next fire, once more for the runs table. One read serves both."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    ui_page.wait_for_timeout(1500)          # the page's own mount reads are out

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if _counts(r.url) else None)
    ui_page.evaluate("""
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: {
        event: "run_finished", run_id: "uir:20260714-070000", state: "finished" } }));
    """)
    ui_page.wait_for_timeout(1500)

    detail = [u for u in seen if u.split("?")[0].endswith("/api/routines/uir")]
    assert len(detail) == 1, f"one run_finished read the detail {len(detail)} times: {seen}"


def test_a_reconnect_does_not_reread_the_heaviest_endpoints(ui, ui_page):
    """A `reconnect` tick means "anything may have moved while the stream was down" — but the
    bus reconnects on capped backoff during a daemon restart, and a FULL dashboard load re-runs
    its heaviest read (/api/schedule/week) exactly while the daemon is coldest. Config-shaped
    state does not move minute to minute, so a reconnect that arrives moments after a full load
    catches up on run state like any other run event."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list, .grid, .empty")
    ui_page.wait_for_timeout(800)

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if _counts(r.url) else None)
    ui_page.evaluate("""
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: { event: "reconnect" } }));
    """)
    ui_page.wait_for_timeout(3000)          # the dashboard's 2 s debounce, with room

    heavy = [u for u in seen if "/schedule/week" in u]
    assert not heavy, f"a fresh reconnect re-read the config-shaped endpoints: {heavy}"
    assert any("/api/routines" in u for u in seen), (
        "the run states still have to catch up — this test must not pass by the dashboard "
        f"ignoring the reconnect entirely (requests seen: {seen})")


def test_the_llm_dock_backstop_rests_in_a_hidden_tab(ui, ui_page):
    """The dock's 10 s /api/llm-tasks reconcile is for a dock somebody can see: a background
    tab skipped nothing and asked the daemon six times a minute for as long as it stayed open.
    Hidden, it rests; shown again, it catches up at once rather than at the next tick."""
    ui_page.add_init_script("""(() => {
      window.__hidden = false;
      Object.defineProperty(Document.prototype, "hidden", { get: () => window.__hidden });
    })()""")
    ui_page.clock.install()
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if "/api/llm-tasks" in r.url else None)
    ui_page.evaluate("window.__hidden = true")
    ui_page.clock.fast_forward(35_000)
    ui_page.wait_for_timeout(300)
    assert not seen, f"a hidden tab reconciled the LLM dock: {seen}"

    ui_page.evaluate("""() => { window.__hidden = false;
                               document.dispatchEvent(new Event("visibilitychange")); }""")
    ui_page.wait_for_timeout(500)
    assert len(seen) == 1, f"coming back did not catch up exactly once: {seen}"
