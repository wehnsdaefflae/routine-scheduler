"""The selected-isolation reading on a page someone opens (D170 phase 2). The selector's whole
purpose is that its COST is arguable before anything is built on it, and that argument happens
on the Stats tab: which utils carry the profile, how many calls that is per run, at the
measured per-call figures. None of that is a claim until the page renders it, which is what
this suite loads.
"""

import json
import re

from playwright.sync_api import expect


def _util(ui, name, header) -> None:
    """One global util in the instance's library, with the header the profile derives from."""
    d = ui.server_cfg.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(f'"""{name} — a util.\n\nusage: gu {name}\n{header}"""\n'
                               'print("ok")\n', encoding="utf-8")


def _calls(ui, counts) -> None:
    """A counted run's usage record, the way the engine writes one."""
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "workflow-usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": "uir:1", "ts": "2026-07-01T07:00:00+00:00",
                             "utils": counts}) + "\n")


def _a_counted_run(ui) -> None:
    """Stats renders its body only once the instance HAS a run — the util table is a section
    of that page, not a page of its own."""
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")


def _section(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/stats")
    return ui_page.locator(".stat-section",
                           has=ui_page.get_by_role("heading", name="Global utils"))


def _name_cell(section, name):
    """The util's own cell. The profile chip sits INSIDE it, beside the name — which is why
    an exact-text match on the name finds nothing: the cell holds two elements.

    Anchored at the START and with NO trailing word boundary, because `el()` joins its
    children with no separator: the cell's textContent is literally `scrapeuntrusted-input`,
    so a `\\b` after the name can never match a marked row (measured — it failed exactly
    that way). The start anchor is what keeps the names apart.
    """
    return section.locator("td").filter(has_text=re.compile(rf"^{re.escape(name)}"))


def test_the_selected_util_is_marked_and_the_unselected_one_is_not(ui, ui_page):
    _a_counted_run(ui)
    _util(ui, "scrape", "net: outbound\n")
    _util(ui, "mailbox", "net: outbound\nsecrets: GMAIL_APP_PASSWORD\n")
    _calls(ui, {"scrape": {"ok": 3}, "mailbox": {"ok": 40}})
    section = _section(ui, ui_page)
    expect(section).to_be_visible()
    expect(_name_cell(section, "scrape")).to_contain_text("untrusted-input")
    # the credentialed util is on the same page and carries no chip: a mark on every row
    # would say nothing, and the exclusion is the half that keeps the cost inside the ruling
    expect(_name_cell(section, "mailbox")).not_to_contain_text("untrusted-input")


def test_the_caption_states_the_cost_per_run_not_a_share_of_all_calls(ui, ui_page):
    """The ruling bounds the selector by a RATE, so the page has to answer in calls per run
    against the measured per-call figures — a percentage of the fleet's calls would not.
    """
    _a_counted_run(ui)
    _util(ui, "scrape", "net: outbound\n")
    _calls(ui, {"scrape": {"ok": 3}})
    section = _section(ui, ui_page)
    expect(section).to_contain_text("untrusted-input profile")
    expect(section).to_contain_text("calls per run")
    expect(section).to_contain_text("392 ms")
    expect(section).to_contain_text("179 MB")


def test_the_real_seeded_library_divides_the_way_the_criterion_says(ui, ui_page):
    """Not a fixture of my own making: the library every instance BOOTS with. `websearch`
    declares `net: outbound` and no credential, so it is selected; `dir-tree` opens no
    network and `vision` speaks to a keyed account, so neither is. If the criterion were
    wrong about real headers this is where it would show.
    """
    _a_counted_run(ui)
    _calls(ui, {"websearch": {"ok": 2}})
    section = _section(ui, ui_page)
    expect(section).to_be_visible()
    expect(_name_cell(section, "websearch")).to_contain_text("untrusted-input")
    for unselected in ("dir-tree", "vision"):
        expect(_name_cell(section, unselected)).not_to_contain_text("untrusted-input")
