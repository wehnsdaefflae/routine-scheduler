"""The GATE HISTORY beside the run history, on the routine page (R2764).

The operator's words: "like the list of previous runs under 'What a finished run delivers' we
also need a history of the previous gates. which ones fired, which ones didn't. report that".

`runs/<ts>/gate.json` has held every verdict since F457 and no console read one, so the page
showed the gate's CONFIG (what it would ask) and never its answers. The runs table is already
the fire history — a skipped fire leaves a run dir (`daemon/runner.py` claims it at :230,
before `run_gate.admit` at :330) — so the verdict belongs on those rows, where the fire it
judged is: one line per fire, which ones fired, which ones didn't, and the check that decided.

A routine with no gate must read exactly as it did: no column, no tally, no empty cells on
every row of every ungated routine in the fleet.
"""

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

SKIP_TS, RUN_TS = "20261023-030000", "20261023-150000"


def _gate(ui, slug, ts, meta):
    atomic_write_json(ui.routine_dir(slug) / "runs" / ts / "gate.json", meta)


def _two_fires(ui):
    """One fire the gate skipped and one it admitted, as run_gate records them."""
    ui.seed_run("uir", SKIP_TS, "finished", summary="Run gate skipped: no new mail since 0a1b2c")
    _gate(ui, "uir", SKIP_TS, {
        "version": 1, "fire_reason": "schedule", "decision": "skip",
        "reason": "no new mail since 0a1b2c",
        "checks": [{"id": "inbox", "kind": "inbox", "work": False,
                    "reason": "no new mail since 0a1b2c"}]})
    ui.seed_run("uir", RUN_TS, "finished", summary="answered three threads")
    _gate(ui, "uir", RUN_TS, {
        "version": 1, "fire_reason": "schedule", "decision": "run",
        "reason": "3 new messages",
        "checks": [{"id": "inbox", "kind": "inbox", "work": True, "reason": "3 new messages"}]})


def test_the_runs_table_says_which_fires_the_gate_let_through_and_which_it_skipped(ui, ui_page):
    _two_fires(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    runs = ui_page.locator(".runs-box")
    expect(runs).to_be_visible()
    # the column exists, and both verdicts are on it
    expect(runs.locator("th", has_text="gate").first).to_be_visible()
    expect(runs).to_contain_text("skipped")
    expect(runs).to_contain_text("fired")
    # the reason the gate gave, on the row of the fire it decided
    expect(runs).to_contain_text("no new mail since 0a1b2c")
    expect(runs).to_contain_text("3 new messages")


def test_the_gate_cell_names_the_check_that_decided_that_fire(ui, ui_page):
    """Which check answered — the id and kind, not just the verdict: a gate is a LIST of
    checks and "skipped" without the one that said no is not a diagnosis."""
    _two_fires(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    cell = ui_page.locator(".runs-box .gate-cell").first
    expect(cell).to_be_visible()
    title = cell.get_attribute("title") or ""
    assert "inbox" in title, title


def test_the_tally_answers_the_question_at_a_glance(ui, ui_page):
    """"which ones fired, which ones didn't" — counted over the fires on show, so the answer
    does not have to be assembled by eye from the rows."""
    _two_fires(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(ui_page.locator(".runs-box")).to_contain_text("1 fired")
    expect(ui_page.locator(".runs-box")).to_contain_text("1 skipped")


def test_on_a_phone_the_gate_chip_says_it_is_the_gate(ui, ui_page):
    """Below 860px the runs table stacks and every `inline` cell becomes a line of one card
    (base.css) — so "skipped" would sit beside "finished" with nothing saying which is which.
    `data-label` is the stacked cell's header, which the stylesheet renders as `::before`; this
    reads that generated text, because the attribute alone proves only that it was spelled."""
    _two_fires(ui)
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(ui_page.locator(".runs-box .gate-cell").first).to_be_visible()
    label = ui_page.evaluate(
        "() => getComputedStyle(document.querySelector('.runs-box td:has(.gate-cell)'),"
        " '::before').content")
    assert "gate" in (label or "").lower(), label


def test_an_ungated_routine_reads_exactly_as_it_did(ui, ui_page):
    """Most of the fleet has no gate. Their rows get no column and no tally — an empty cell
    per run would be noise on every routine that never opted in (F457)."""
    ui.seed_run("uir", RUN_TS, "finished", summary="did the thing")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    runs = ui_page.locator(".runs-box")
    expect(runs).to_contain_text("did the thing")
    expect(runs.locator("th", has_text="gate")).to_have_count(0)
    expect(runs.locator(".gate-cell")).to_have_count(0)
