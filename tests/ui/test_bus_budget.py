"""The machine check for CLAUDE.md's live-refresh rule: "a live refresh on a bus event
fetches only what that event can change".

`llm_task` / `llm_process` fire several times a second while a run works and cannot change a
routine's state, a decision, a schedule or a run list — every view is supposed to ignore them.
The rule lived only in prose, and three views broke it in three different ways: the Decisions
page refetched its whole question set on EVERY event with no filter and no timer; the watch
ribbon dragged a week-schedule computation along at 20 s; the dashboard's own guard was the
only one that had it right. The cost lands on the daemon, which is answering for the runs the
console is watching (2026-09-12: /api/domains every 600 ms, every request queued 20-50 s).

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
# llm_task/llm_process from the EVENT and re-reads /api/llm-tasks every 10 s only to catch
# events the bus dropped, and the daemon lamp re-reads /api/status every 30 s. Neither is
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


def test_a_reconnect_does_not_reread_the_heaviest_endpoints(ui, ui_page):
    """A `reconnect` tick means "anything may have moved while the stream was down" — but the
    bus reconnects on capped backoff during a daemon restart, and a FULL dashboard load re-runs
    its two heaviest reads (/api/schedule/week, /api/domains) exactly while the daemon is
    coldest. Config-shaped state does not move minute to minute, so a reconnect that arrives
    moments after a full load catches up on run state like any other run event."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list, .grid, .empty")
    ui_page.wait_for_timeout(800)

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if _counts(r.url) else None)
    ui_page.evaluate("""
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: { event: "reconnect" } }));
    """)
    ui_page.wait_for_timeout(3000)          # the dashboard's 2 s debounce, with room

    heavy = [u for u in seen if "/schedule/week" in u or "/api/domains" in u]
    assert not heavy, f"a fresh reconnect re-read the config-shaped endpoints: {heavy}"
    assert any("/api/routines" in u for u in seen), (
        "the run states still have to catch up — this test must not pass by the dashboard "
        f"ignoring the reconnect entirely (requests seen: {seen})")
