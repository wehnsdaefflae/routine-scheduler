"""F577: two clicks a second apart are a second apart in the trace file, not one instant.

The friction signal the UI traces exist to produce is inter-event timing, and before this it was
the one quantity the store destroyed: `static/trace.js` sent no timestamp and the server stamped
a whole flush with one `now`. The ledger's evidence was thirteen clicks at exactly two instants
five seconds apart — a quantisation artefact that reads as rage-clicking.

This drives the REAL console: the shipped trace.js installed by app.js, a real click on a real
control, the real POST, the real day file. The flush is forced with `pagehide` — the worst case
named in the finding (that flush can carry a whole session's tail), and the one that puts both
clicks in a single batch, so what is measured is precisely the interval a shared batch stamp
used to erase.
"""

from __future__ import annotations

import datetime as dt
import json

from .conftest import until

GAP_MS = 1200


def _click_rows(ui) -> list[dict]:
    rows = [json.loads(ln) for p in sorted((ui.routines / ".ui-traces").glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r.get("kind") == "click" and r.get("target") == "Routines"]


def test_two_clicks_keep_the_time_between_them(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    nav = ui_page.locator("#nav a[href='#/routines']")

    nav.click()                          # first click: navigates, and is traced
    ui_page.wait_for_timeout(GAP_MS)     # the user sits for over a second
    nav.click()                          # second click: same hash, so only the click is traced
    # One flush, carrying both: `pagehide` is the keepalive path trace.js uses on unload.
    ui_page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")

    until(lambda: len(_click_rows(ui)) >= 2, what="both click traces to be stored",
          page=ui_page)
    first, second = _click_rows(ui)[:2]
    assert first["ts_src"] == second["ts_src"] == "client", (first, second)
    # ONE batch — the stamp that used to BE `ts`, and the reason the interval vanished
    assert first["received"] == second["received"], (first, second)
    gap = (dt.datetime.fromisoformat(second["ts"])
           - dt.datetime.fromisoformat(first["ts"])).total_seconds()
    assert GAP_MS / 1000 * 0.7 < gap < 5, (gap, first, second)
