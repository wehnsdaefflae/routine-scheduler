"""Per-run cost on the routine's DEVELOPMENT page, in the real console (D163).

`readmodels/cost_profile.py` shipped in 0.495.0 and no surface read it, so the operator who
asked for "a real cost profile per routine" could not see one. The panel
(components/cost-panel.js) renders it at #/changes/<slug> beside Outcome.

The assertion that matters is the HONESTY one: the read model decides whether its own figures
can be trusted (a depth-0 run reporting less volume than its own `#sub<n>` child rows add up to
was understated by the accounting defect closed in 0.494.0, R3112), and a window it calls mixed
must SAY so where its median is read — in words, in the warn tone, not only in a payload field.
Every assertion below first asserts the element is PRESENT: a selector matching nothing passes
vacuously, and a console that lost the panel entirely would pass with it.
"""

from __future__ import annotations

from playwright.sync_api import expect

from helpers import measured_run, write_usage_stream

RUNS = 10


def _runs(n: int = RUNS) -> list[dict]:
    return [measured_run(i, slug="uir", volume=4_000_000) for i in range(1, n + 1)]


def _understating_child(recs: list[dict]) -> dict:
    """The R3112 symptom on the newest run: a child row bigger than the parent that folds it."""
    parent = recs[-1]
    return {**parent, "run_id": f"{parent['run_id']}#sub1", "depth": 1, "volume": 9_000_000}


def _token(page, name: str) -> str:
    """The computed value of a CSS custom property, resolved the way the page resolves it."""
    return page.evaluate(
        """(name) => { const p = document.createElement('span');
                       p.style.color = `var(${name})`; document.body.append(p);
                       const c = getComputedStyle(p).color; p.remove(); return c; }""", name)


def test_the_panel_names_what_one_run_costs(ui, ui_page):
    """The figure a budget is set in, on screen: the median over every run, the window pair the
    trend flag judges, and — the history being sound — the integrity line saying so."""
    write_usage_stream(ui.routines, _runs())
    ui_page.goto(f"{ui.url}/#/changes/uir")

    expect(ui_page.locator("#sec-cost")).to_have_count(1)
    expect(ui_page.locator("#sec-cost")).to_have_text("Per-run cost")
    figure = ui_page.locator("[data-cost-typical]")
    expect(figure).to_have_count(1)
    expect(figure).to_contain_text("4.00M volume")
    expect(figure).to_contain_text("20 turns")
    expect(figure).to_contain_text("per run")
    expect(ui_page.locator(".cost-panel .sub")).to_contain_text(f"median over {RUNS} runs")

    for window in ("baseline", "recent"):
        row = ui_page.locator(f"[data-cost-window='{window}']")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("4.00M")
        expect(row.locator(".cost-mixed")).to_have_count(0)   # a sound window says nothing

    # a sound history says so in words, and raises no warn panel
    expect(ui_page.locator(".cost-integrity")).to_have_count(0)
    expect(ui_page.locator(".cost-panel")).to_contain_text(
        "account for their own child rows")


def test_a_mixed_window_says_so_where_its_median_is_read(ui, ui_page):
    """The one way this surface could mislead: showing a median the read model calls a floor as
    a clean figure. The warn panel explains it in words, and the affected window row is marked —
    in the WARN ink, which proves the stylesheet rule actually reaches the class."""
    recs = _runs()
    write_usage_stream(ui.routines, [*recs, _understating_child(recs)])
    ui_page.goto(f"{ui.url}/#/changes/uir")

    panel = ui_page.locator(".cost-integrity")
    expect(panel).to_have_count(1)
    expect(panel).to_contain_text("mixed accounting")
    expect(panel).to_contain_text("span two different quantities")
    expect(panel).to_contain_text("0.494.0")          # the reader's own prose, verbatim
    expect(panel).to_contain_text("report less volume than their own child rows sum to")
    expect(panel).to_contain_text("The recent window above carries understated runs")
    expect(panel).to_have_class("panel warn cost-integrity")

    recent = ui_page.locator("[data-cost-window='recent'] .cost-mixed")
    expect(recent).to_have_count(1)
    expect(recent).to_contain_text("mixed")
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector("
        "\"[data-cost-window='recent'] .cost-mixed\")).color") == _token(ui_page, "--warn")

    # the sound side is NOT marked — the mark names a side, it is not a blanket disclaimer
    expect(ui_page.locator("[data-cost-window='baseline']")).to_have_count(1)
    expect(ui_page.locator("[data-cost-window='baseline'] .cost-mixed")).to_have_count(0)


def test_a_routine_that_never_ran_says_so(ui, ui_page):
    """No usage stream at all: the panel reports no reading rather than rendering zeros."""
    ui_page.goto(f"{ui.url}/#/changes/uir")
    empty = ui_page.locator("[data-cost='no-runs']")
    expect(empty).to_have_count(1)
    expect(empty).to_contain_text("no run in the usage stream yet")
