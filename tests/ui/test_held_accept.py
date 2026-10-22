"""D178 + F668: the settings accept refused mid-run SAYS so and stays visible.

On 2026-10-11 the operator pressed "accept changes" on `freelance-radar` three times in one
second while a run was 78 minutes from ending. Each press was refused — correctly: a
multi-field accept during a live run would leave the routine with neither its old settings nor
its new ones — and the refusal toast said only "try again after it ends". It named no time and
left nothing behind, so the next day's traces show he never retried: he had concluded the
button was broken.

These tests read the RENDERED WORDS, not the attributes that point at them: a pending accept
the operator is supposed to SEE is only built if a reader can scan the sentence.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import until
from .helpers import stored_config, visible_toast

RUN_TS = "20260715-120000"          # `make_routine` budgets max_wall_clock_min: 5


def _running(ui, slug="uir", ts=RUN_TS):
    return ui.seed_run(slug, ts, "running")


def _open_limits(page):
    group = page.locator("details.rgroup", has=page.locator(".rgroup-title",
                                                            has_text="Limits & reach"))
    group.evaluate("(d) => { d.open = true; "
                   "d.querySelectorAll('details.rmore').forEach((m) => { m.open = true; }); }")
    return group


def _change_retention(page, value="12"):
    keep = page.locator('.sf-field[data-field="keep_runs"] input[data-keep-runs]')
    keep.fill(value)
    keep.press("Tab")


def test_the_refused_accept_tells_the_operator_when_it_can_be_retried(ui, ui_page):
    """The click gets an answer a reader can act on: a run is active, and WHEN it is over."""
    _running(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_limits(ui_page)
    _change_retention(ui_page)
    ui_page.locator(".accept-bar [data-accept]").click()
    toast = visible_toast(ui_page)
    expect(toast).to_contain_text("a run is active")
    # the WHEN — a clock time, not "later". 12:00 start + 5 wall-clock minutes.
    expect(toast).to_contain_text(re.compile(r"\d{1,2}:\d{2}"))
    assert (stored_config(ui).get("retention") or {}).get("keep_runs") != 12


def test_the_held_accept_is_visible_on_the_page_so_he_need_not_click_again(ui, ui_page):
    """D178's words: 'persist the attempt as a visible pending accept with the run's expected
    end, so you can see it is waiting and need not click again'. Read as a sentence."""
    _running(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_limits(ui_page)
    _change_retention(ui_page)
    ui_page.locator(".accept-bar [data-accept]").click()
    banner = ui_page.locator(".held-accept")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("waiting")
    expect(banner).to_contain_text(re.compile(r"\d{1,2}:\d{2}"))
    expect(banner).to_contain_text("1 change")
    # and it survives a reload — the attempt is persisted, not a toast that scrolled away
    ui_page.reload()
    expect(ui_page.locator(".held-accept")).to_contain_text("waiting")


def test_three_clicks_in_one_second_leave_one_waiting_accept(ui, ui_page):
    """The measured harm, driven through the real button: three presses, one record, one
    sentence on the page."""
    _running(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_limits(ui_page)
    _change_retention(ui_page)
    accept = ui_page.locator(".accept-bar [data-accept]")
    accept.evaluate("b => { b.click(); b.click(); b.click(); }")
    expect(ui_page.locator(".held-accept")).to_be_visible()
    expect(ui_page.locator(".held-accept")).to_have_count(1)
    held = ui.routines / ".control" / "pending-accepts" / "uir"
    until(lambda: len(list(held.glob("pa-*.json"))) == 1,
          what="exactly one held accept after three clicks")
