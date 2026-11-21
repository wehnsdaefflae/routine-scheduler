"""The Stats page's Cost regressions panel on the real console (operator decision #9, 2026-11-21):
the latest `cost_trend_degraded` finding per routine, its attribution, and a link to the routine's
development view — and NOT on the Blocked work panel, because a regression is not blocked work.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect


def _stream(ui, rows) -> None:
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "health-events.jsonl").open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _now(page) -> str:
    return page.evaluate("() => new Date().toISOString().replace('Z', '+00:00')")


def _section(page, name):
    return page.locator(".stat-section", has=page.get_by_role("heading", name=name))


def _finding(ts, routine, scope, **over) -> dict:
    return {"ts": ts, "event": "cost_trend_degraded", "routine": routine,
            "run_id": f"{routine or 'uir'}:1", "scope": scope, "peers_compared": 4,
            "peers_degraded": 1, "window": 5, "turns_median_before": 20,
            "turns_median_after": 41, "volume_median_before": 1000000,
            "volume_median_after": 2500000, "fail_rate_before": 0.0, "fail_rate_after": 0.2,
            "detail": f"{scope} verdict · the last 5 runs against the 5 before them", **over}


def test_the_latest_finding_per_routine_with_whose_it_is_and_a_dev_view_link(ui, ui_page):
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    ts = _now(ui_page)
    _stream(ui, [
        _finding(ts, "uir", "routine", detail="older reading"),
        _finding(ts, "uir", "routine", detail="the newest reading"),
        _finding(ts, "", "host", detected_on="uir", peers_degraded=3),
    ])
    ui_page.goto(f"{ui.url}/#/stats")

    panel = _section(ui_page, "Cost regressions")
    expect(panel.locator("tbody tr")).to_have_count(2)       # one per subject, newest only
    expect(panel).to_contain_text("the newest reading")
    expect(panel).not_to_contain_text("older reading")
    expect(panel).to_contain_text("this routine's")
    expect(panel).to_contain_text("the host")
    expect(panel).to_contain_text("1 of 4 comparable routines degraded")
    expect(panel).to_contain_text("20 → 41")
    links = panel.locator('a[href="#/changes/uir"]')
    expect(links).to_have_count(2)                           # the routine, and "detected on"
    # a regression is not work that was due and did not happen
    expect(_section(ui_page, "Blocked work")).to_contain_text("nothing blocked")

    links.first.click()
    expect(ui_page).to_have_url(f"{ui.url}/#/changes/uir")


def test_no_finding_reads_as_the_good_reading(ui, ui_page):
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    ui_page.goto(f"{ui.url}/#/stats")
    expect(_section(ui_page, "Cost regressions")).to_contain_text("no cost regressions")
