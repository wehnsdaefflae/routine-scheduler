"""The artifact panel shows the file the reader picked, as the file is written.

components/artifacts.js lists a run's deliverables and renders the one a row opens. Each test
here is a way it used to show something else: an earlier click's file landing over a later
one, "could not load" for a JSON file one comma off, a quoted CSV value split across columns,
and a report written mid-reply that the panel did not list until the reply ended.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect

from .conftest import until

RUN = "20260715-150000"


def _artifacts(ui, files: dict[str, str]) -> None:
    ui.seed_run("uir", RUN, "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    for name, text in files.items():
        (art / name).write_text(text, encoding="utf-8")


def test_the_viewer_shows_the_artifact_picked_last(ui, ui_page):
    """Two clicks put two fetches in flight. The first one landing LAST must not repaint the
    viewer with the file the reader has already moved off."""
    _artifacts(ui, {"slow.md": "# the slow one", "fast.md": "# the fast one"})
    held = []

    def hold(route):        # Playwright wraps a Python function, never a bound builtin
        held.append(route)

    ui_page.route(re.compile(r".*/artifact\?path=artifacts%2Fslow\.md"), hold)
    ui_page.goto(f"{ui.url}/#/run/uir:{RUN}")

    ui_page.locator(".art-item", has_text="slow.md").click()
    until(lambda: held, what="the slow artifact's fetch", page=ui_page)
    ui_page.locator(".art-item", has_text="fast.md").click()
    name = ui_page.locator(".art-viewer-head .art-name")
    expect(name).to_have_text("fast.md")

    held[0].continue_()                    # the superseded response arrives after all
    ui_page.wait_for_timeout(600)
    expect(name).to_have_text("fast.md")
    expect(ui_page.locator(".art-viewer .prose")).to_have_text("the fast one")
    expect(ui_page.locator(".art-row.on")).to_contain_text("fast.md")


def test_a_json_artifact_that_does_not_parse_still_shows_its_text(ui, ui_page):
    _artifacts(ui, {"data.json": '{"rows": 3,}'})
    ui_page.goto(f"{ui.url}/#/run/uir:{RUN}")
    ui_page.locator(".art-item", has_text="data.json").click()
    expect(ui_page.locator(".art-viewer pre.art-pre")).to_have_text('{"rows": 3,}')


def test_a_quoted_csv_value_keeps_its_comma(ui, ui_page):
    """A value with the separator in it is quoted by every csv writer; it is ONE cell."""
    _artifacts(ui, {"people.csv": 'name,city\n"Smith, John",Berlin\n"say ""hi""",Paris\n\n'})
    ui_page.goto(f"{ui.url}/#/run/uir:{RUN}")
    ui_page.locator(".art-item", has_text="people.csv").click()
    rows = ui_page.locator(".art-viewer .art-table tr")
    expect(rows).to_have_count(3)                        # the blank last line is not a row
    expect(rows.nth(0).locator("th")).to_have_text(["name", "city"])
    expect(rows.nth(1).locator("td")).to_have_text(["Smith, John", "Berlin"])
    expect(rows.nth(2).locator("td")).to_have_text(['say "hi"', "Paris"])


def test_a_report_written_mid_reply_appears_in_the_conversation_panel(ui, ui_page):
    """The panel lists artifacts/, reports/ AND output/ (R339). A write into any of them,
    seen while the reply works, refreshes it — the hook used to know only artifacts/."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Write me a report.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    run_dir = ui.seed_run(slug, "20260715-120000", "running", home=ui.conversations)
    ui_page.reload()
    panel = ui_page.locator(".art-list")
    expect(panel).to_contain_text("No artifacts yet")

    reports = ui.conversations / slug / "reports"
    reports.mkdir()
    (reports / "findings.md").write_text("# findings", encoding="utf-8")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "observation", "turn": 1, "payload": {
            "kind": "write_file", "path": "reports/findings.md", "bytes": 10}}) + "\n")

    expect(panel.locator(".art-item", has_text="findings.md")).to_be_visible()
