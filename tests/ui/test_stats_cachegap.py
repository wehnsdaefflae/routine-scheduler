"""The between-runs prompt-cache gap, on the page someone opens (F507/D163).

The readmodel can be right and the operator still never see it: the Stats tab is where this
measurement is supposed to answer "what does this routine cost me". So the section is loaded
through the real page here, and the two things that make the number HONEST are pinned as UI,
not just as arithmetic:

- a fire whose turn 1 could not be read shows as UNDETERMINED — never silently absent, never
  folded into cold. A partial measurement that renders as a complete one is the one failure
  this table must not have;
- the headline names the warm re-write it does NOT price, so the premium cannot be read as the
  whole bill.
"""

import json

from playwright.sync_api import expect

# what this instance records for a claude-proxy model: in, out, cache_read, cache_write
RATES = [5.0, 25.0, 0.5, 6.25]


def _prices(ui) -> None:
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "model-prices.json").write_text(
        json.dumps({"fetched": "2026-10-24T19:01:24+00:00",
                    "rows": {"proxy|m": {"rates": RATES}}}), encoding="utf-8")


def _fire(ui, slug, ts, usage, *, lead=0):
    """A retained run whose transcript's head carries `usage` on its first metered turn."""
    run = ui.seed_run(slug, ts, "finished", summary="ok")
    events = [{"ts": "t", "type": "user_injection", "payload": {"source": "web", "text": "hi"}}
              for _ in range(lead)]
    events.append({"ts": "2026-10-25T14:00:02+00:00", "type": "assistant_action", "turn": 1,
                   "payload": {"kind": "read_file"}, "usage": usage})
    with (run / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return run


def _section(ui_page):
    return ui_page.locator(".stat-section", has=ui_page.get_by_role(
        "heading", name="Between-runs prompt-cache gap"))


def test_the_cold_start_premium_reaches_the_stats_tab(ui, ui_page):
    """A cold fire's premium is the re-written prefix at the gap between the two recorded
    rates: 53992 x (6.25-0.5)/1e6 = $0.310454, rendered against the routine it cost.
    """
    _prices(ui)
    _fire(ui, "uir", "20261025-140002",
          {"in": 2, "out": 258, "cache_write": 53992, "model": "proxy/m"})

    ui_page.goto(f"{ui.url}/#/stats")
    section = _section(ui_page)
    expect(section).to_be_visible()
    expect(section).to_contain_text("started COLD")
    expect(section).to_contain_text("$0.31")
    expect(section.get_by_role("link", name="uir")).to_be_visible()
    # the honesty line: the warm re-write this view does not price is NAMED on the page
    expect(section).to_contain_text("left unpriced")


def test_a_fire_it_could_not_read_shows_as_undetermined(ui, ui_page):
    """The defect class this table must not have. A run dir with no readable first turn is a
    fire whose gap is UNKNOWN: the page says undetermined and does not count it as cold.
    """
    _prices(ui)
    _fire(ui, "uir", "20261025-140002",
          {"in": 2, "out": 258, "cache_write": 53992, "model": "proxy/m"})
    ui.seed_run("uir", "20261025-150000", "finished", summary="no transcript")

    ui_page.goto(f"{ui.url}/#/stats")
    section = _section(ui_page)
    expect(section).to_be_visible()
    expect(section).to_contain_text("undetermined")
    expect(section).to_contain_text("not zero")


def test_a_turn_one_far_down_the_head_is_not_lost_from_the_table(ui, ui_page):
    """Measured 2026-10-26: 77 of 676 real metered runs carry turn 1 beyond transcript line 8,
    pushed down by pre-turn-1 injections. Those fires must appear on the page, priced.
    """
    _prices(ui)
    _fire(ui, "uir", "20261025-140002",
          {"in": 2, "out": 258, "cache_write": 53992, "model": "proxy/m"}, lead=40)

    ui_page.goto(f"{ui.url}/#/stats")
    section = _section(ui_page)
    expect(section).to_contain_text("$0.31")
    expect(section).not_to_contain_text("no retained transcript")
