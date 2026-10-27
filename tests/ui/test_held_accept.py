"""D178 + F668: the settings accept refused mid-run SAYS so and stays visible.

On 2026-10-11 the operator pressed "accept changes" on `freelance-radar` three times in one
second while a run was 78 minutes from ending. Each press was refused — correctly: a
multi-field accept during a live run would leave the routine with neither its old settings nor
its new ones — and the refusal toast said only "try again after it ends". It named no time and
left nothing behind, so the next day's traces show he never retried: he had concluded the
button was broken.

These tests read the RENDERED WORDS, not the attributes that point at them: a pending accept
the operator is supposed to SEE is only built if a reader can scan the sentence.

F761 then moved the refusal's CHANNEL. The sentence was right and the banner worked, but the
same words also went out as an ERROR toast, and a 409 arriving as a failure reads as "your
click did not work": on 2026-10-23 he pressed accept at 03:06:27, 03:06:34 and 03:06:43, and
four of the console's eight `error` events over those two days were this one refusal. A held
accept is a STATE, so the banner's own `role="status"` is the whole of the answer now and the
error channel is asserted unused.
"""

from __future__ import annotations

import json
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
    """The click gets an answer a reader can act on: a run is active, and WHEN it is over.

    Read off the BANNER since F761. The answer used to be a toast as well, in the console's
    error channel, and that channel is what made a refusal read as a failed click — so the
    words are asserted where they now live, and `visible_toast` is kept pointed at this click
    to prove nothing speaks there at all.
    """
    _running(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_limits(ui_page)
    _change_retention(ui_page)
    ui_page.locator(".accept-bar [data-accept]").click()
    banner = ui_page.locator(".held-accept")
    expect(banner).to_contain_text("applies when the run ends")
    # the WHEN — a clock time, not "later". 12:00 start + 5 wall-clock minutes.
    expect(banner).to_contain_text(re.compile(r"\d{1,2}:\d{2}"))
    expect(visible_toast(ui_page)).to_have_count(0)      # and no toast, of either channel
    assert (stored_config(ui).get("retention") or {}).get("keep_runs") != 12


def _traces(ui, kind: str) -> list[dict]:
    """Every stored ui-trace row of one kind — the friction evidence the audit lens counts."""
    rows = [json.loads(ln) for p in sorted((ui.routines / ".ui-traces").glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r.get("kind") == kind]


def test_the_held_refusal_is_a_state_and_never_speaks_in_the_error_channel(ui, ui_page):
    """F761: the held refusal arrived as an ERROR TOAST — the console's channel for failures —
    so the answer to his click read as "your click failed", and he clicked again. MEASURED on
    2026-10-22/23: four of the console's eight `error` events were this refusal, and
    `routine|accept changes` was pressed at 03:06:27, 03:06:34 and 03:06:43 — three times in
    18 seconds.

    The state was never the problem (the banner above the controls says the sentence, and
    says it correctly): the CHANNEL was. So what answers the click is the banner's own
    `role="status"`, landed on so a reader at the foot of the page sees it — and the error
    channel, which is `#toast.err` plus the `error` trace it files, is not used at all.
    """
    _running(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_limits(ui_page)
    _change_retention(ui_page)
    ui_page.locator(".accept-bar [data-accept]").click()

    banner = ui_page.locator(".held-accept")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("waiting")            # the HELD state, as a sentence
    expect(banner).to_have_attribute("role", "status")   # …in a channel that is not the error one
    # The error channel: `toast(…, {error: true})` wears `err` on the one toast host and files
    # an `error` trace with it (static/util.js). Neither is this refusal's. The class OUTLIVES
    # the toast's own visibility (`toast` clears `hidden`, never the class), so this is not a
    # race against the 6 s timeout: a refusal that spoke there leaves the mark behind.
    expect(ui_page.locator("#toast.err")).to_have_count(0)
    expect(banner).to_be_in_viewport()                   # …brought to the reader who pressed
    ui_page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")   # force the flush
    # The click and any error toast ride the SAME batch, so the click's arrival is what makes
    # the absence of an `error` row a fact rather than a race.
    until(lambda: any(r.get("target") == "accept changes" for r in _traces(ui, "click")),
          what="the accept click's own trace", page=ui_page)
    assert _traces(ui, "error") == [], _traces(ui, "error")


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
