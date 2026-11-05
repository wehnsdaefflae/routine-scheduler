"""The Decisions page shows the settled archive as a WINDOW and says so (F810).

`/api/questions` sends settled decisions only when a reader asks (`web/decisions_window.py`),
so the page — the one surface that shows history — asks for the newest 25, names how many
exist, and fetches more on a click instead of a list that silently stops. When the page goes
away it releases the window: the badge's 30 s read carries no history again.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .conftest import until
from .helpers import watch_requests


def _archive(ui, slug, n, day):
    """`n` settled records, as a consumed answer leaves them in questions/answered/."""
    adir = ui.routines / slug / "questions" / "answered"
    for i in range(n):
        stamp = f"2026-10-{day:02d}T{8 + i // 60:02d}:{i % 60:02d}:00+02:00"
        atomic_write_json(adir / f"q-{slug}-{i}.json", {
            "qid": f"q-{slug}-{i}", "question": f"Settled {slug} {i}?", "options": ["a"],
            "asked": "20261001-070000", "mode": "deferred", "answered": True, "answer": "a",
            "answer_source": "web", "consumed": stamp})


def test_the_settled_band_is_a_window_with_a_way_to_widen_it(ui, ui_page, make_routine):
    make_routine(slug="uir2")
    _archive(ui, "uir", 20, 2)
    _archive(ui, "uir2", 10, 3)          # 30 settled in all; the per-dir cap is 25
    lists = watch_requests(ui_page, re.compile(r"/api/questions\?settled=\d+$"))
    ui_page.goto(f"{ui.url}/#/questions")

    cards = ui_page.locator(".question-item.answered")
    expect(cards).to_have_count(25)
    more = ui_page.locator("[data-settled-more]")
    expect(more).to_have_text("show 5 older")
    expect(ui_page.locator("#view")).to_contain_text("settled: showing the newest 25 of 30")
    assert any(u.endswith("settled=25") for u in lists), lists

    more.click()
    expect(cards).to_have_count(30)
    expect(ui_page.locator("[data-settled-more]")).to_have_count(0)
    assert any(u.endswith("settled=50") for u in lists), lists


def test_leaving_the_page_releases_the_window(ui, ui_page):
    _archive(ui, "uir", 3, 2)
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".question-item.answered")).to_have_count(3)

    ui_page.goto(f"{ui.url}/#/help")
    ui_page.wait_for_selector("#view h1")
    sent: list[str] = []
    ui_page.on("request", lambda r: sent.append(r.url) if "/api/questions" in r.url else None)
    ui_page.evaluate("() => import('/static/questions-store.js')"
                     ".then((m) => m.loadQuestions())")
    until(lambda: sent, what="a questions read after leaving the page", page=ui_page)
    assert all(u.endswith("/api/questions") for u in sent), sent
