"""The files card offers no click for a row whose verdict is still being checked (2026-11-21).

A row on a network share is judged by a background job (`web/fileverdicts.py`); until its
verdict is kept, `/api/runs/{id}/files` serves it as `servable: null` with `pending: true`. The
card used to treat anything but `false` as servable, so an unknown row would have been offered
as a click — the one claim the server is careful never to make. The REAL response is served
with only those two fields overridden, so what is tested is the card's reading of them.
"""
import json
import re

from playwright.sync_api import expect


def test_an_unknown_row_gets_no_click_until_its_verdict_lands(ui, ui_page):
    run_dir = ui.seed_run("uir", "20261121-120000", "finished", summary="done")
    rdir = ui.routine_dir("uir")
    (rdir / "artifacts").mkdir(parents=True, exist_ok=True)
    (rdir / "artifacts" / "kept.md").write_text("kept", encoding="utf-8")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "observation", "turn": 1, "payload": {
            "kind": "write_file", "path": "artifacts/kept.md", "bytes": 4}}) + "\n")

    # The view may fetch the card more than once on mount (the transcript's write observation
    # pokes it), so the unknown state is held until the test has SEEN it, then released —
    # never "the first response only", which a second mount-time fetch could overtake.
    landed = {"yes": False}
    calls = []

    def until_landed(route):
        resp = route.fetch()
        body = resp.json()
        calls.append(landed["yes"])
        if not landed["yes"]:        # the share's check has not landed yet
            body["files"][0]["servable"] = None
            body["pending"] = True
        route.fulfill(response=resp, json=body)

    ui_page.route(re.compile(r"/api/runs/uir:20261121-120000/files$"), until_landed)
    ui_page.goto(f"{ui.url}/#/run/uir:20261121-120000")
    row = ui_page.locator(".file-row").first
    expect(row).to_contain_text("artifacts/kept.md")
    # unknown: a placeholder, no button — never offered as servable
    expect(row.locator(".file-act")).to_have_attribute("title", re.compile("network share"))
    expect(row.locator("button.file-act")).to_have_count(0)
    # `pending` makes the card fetch again ON ITS OWN (no navigation, no poke after this
    # point); the landed verdict brings the buttons on a later poll. The backoff tops out at
    # 15 s, so the wait allows one full step past it.
    landed["yes"] = True
    expect(row.locator("button.file-act")).to_have_count(2, timeout=20_000)
    assert True in calls, "the buttons came from something other than the pending re-fetch"
