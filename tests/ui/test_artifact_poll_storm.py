"""Many artifact writes in one transcript cost ONE list fetch, not one each.

components/chat.js fires `onArtifact` for every write_file/edit_file observation that lands in
a deliverable dir, and opening a conversation REPLAYS the whole transcript — so a long
conversation's history fired one full `/artifacts` fetch per historic write. Measured in
`.ui-traces` on 2026-11-13: 25 bursts, the largest 70 identical fetches in 9.6 s, each ~4 s and
climbing to 13 s as they queued against each other; inside those 305 s of windows every OTHER
endpoint's slow rows ran 39% slower (3,786 ms vs 2,721 ms median). The panel now coalesces the
burst the way `fileactivity.js` already did (F891).

The sub-run card had the same shape and is pinned here too: `views/conversations.js` called
`subRunTree.refresh()` per subrun event, replayed ones included — 676 of 827 client-stamped
`/tree` rows in the same two trace days arrived under a second apart, 695 of them inside the
artifact bursts (F892).
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

WRITES = 12
SUBRUNS = 8


def _seed_conversation(ui, ui_page) -> tuple[str, object]:
    """A live conversation whose rail holds the artifact panel."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Write me many reports.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    run_dir = ui.seed_run(slug, "20260715-120000", "running", home=ui.conversations)
    return slug, run_dir


def test_a_replayed_write_history_costs_one_artifact_fetch(ui, ui_page):
    """WRITES write observations already in the transcript must not become WRITES fetches."""
    slug, run_dir = _seed_conversation(ui, ui_page)
    arts = ui.conversations / slug / "artifacts"
    arts.mkdir(exist_ok=True)
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for i in range(WRITES):
            (arts / f"note-{i}.md").write_text(f"# note {i}", encoding="utf-8")
            fh.write(json.dumps({"type": "observation", "turn": i + 1, "payload": {
                "kind": "write_file", "path": f"artifacts/note-{i}.md", "bytes": 9}}) + "\n")

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if r.url.endswith("/artifacts") else None)
    ui_page.reload()

    panel = ui_page.locator(".art-list")
    expect(panel.locator(".art-item", has_text="note-11.md")).to_be_visible()
    ui_page.wait_for_timeout(2000)          # past the 1,500 ms coalesce window

    # the view's own first render fetches the list; the replayed writes add ONE coalesced
    # refetch between them — never one per write
    assert len(seen) <= 3, (
        f"{WRITES} replayed artifact writes caused {len(seen)} /artifacts fetches: {seen}")


def test_replayed_subrun_events_cost_at_most_two_tree_fetches(ui, ui_page):
    """F892, the same replay against the sub-run card: SUBRUNS children already in the
    transcript must not become one /tree fetch each.
    """
    _slug, run_dir = _seed_conversation(ui, ui_page)
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for i in range(1, SUBRUNS + 1):
            # the spawning action first: subrun_start carries no turn of its own, and a node
            # whose start_turn is None renders as a plain row rather than a link (D193)
            fh.write(json.dumps({"ts": "2026-07-15T10:02:29+00:00", "type": "assistant_action",
                                 "turn": i, "payload": {"kind": "spawn",
                                                        "say": f"starting child-{i}"}}) + "\n")
            fh.write(json.dumps({"ts": "2026-07-15T10:02:30+00:00", "type": "subrun_start",
                                 "payload": {"n": i, "label": f"child-{i}",
                                             "workflow": "general-task", "mode": "parallel",
                                             "depth": 1,
                                             "budget": {"turns": 20, "tokens": 0}}}) + "\n")
            fh.write(json.dumps({"ts": "2026-07-15T10:09:00+00:00", "type": "subrun_end",
                                 "payload": {"n": i, "label": f"child-{i}", "mode": "parallel",
                                             "status": "ok", "turns": 7,
                                             "summary": "done"}}) + "\n")

    seen: list[str] = []
    ui_page.on("request", lambda r: seen.append(r.url) if r.url.endswith("/tree") else None)
    ui_page.reload()

    expect(ui_page.locator(".sr-row", has_text="child-8")).to_be_visible()
    ui_page.wait_for_timeout(2000)          # past the 1,500 ms coalesce window

    # the card's own first render fetches the tree; the 16 replayed events add ONE coalesced
    # refetch — never one per event. The 3 s live poller may add one more inside the wait.
    assert len(seen) <= 4, (
        f"{SUBRUNS} replayed subruns caused {len(seen)} /tree fetches: {seen}")


def test_a_live_write_still_reaches_the_panel(ui, ui_page):
    """Coalescing must not cost the panel its liveness — the write still lands in the list."""
    slug, run_dir = _seed_conversation(ui, ui_page)
    ui_page.reload()
    expect(ui_page.locator(".art-list")).to_contain_text("No artifacts yet")

    reports = ui.conversations / slug / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "late.md").write_text("# late", encoding="utf-8")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "observation", "turn": 1, "payload": {
            "kind": "write_file", "path": "reports/late.md", "bytes": 6}}) + "\n")

    expect(ui_page.locator(".art-list .art-item", has_text="late.md")).to_be_visible()
