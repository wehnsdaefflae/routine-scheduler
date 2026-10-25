"""The MOMENT TEST on the Changes page (components/change-moment.js over
readmodels/change_frictions.py): a change whose rates could not be judged still says which
repeated failure it removed and which it brought in — and is therefore not folded away among
the changes that "came faster than it ran"."""

from __future__ import annotations

from playwright.sync_api import expect

from helpers import measured_run, write_usage_stream

from .test_changes import WIDE, _at


def test_a_change_the_moment_test_judged_stands_on_its_own_row(ui, ui_page):
    runs = [measured_run(i, slug="uir", frictions={"edit_miss": 2}) for i in (1, 2)]
    runs += [measured_run(i, slug="uir", recipe="c2a9f3e1", frictions={"script:sync": 1})
             for i in (3, 4)]
    runs += [measured_run(5, slug="uir", recipe="c3b8e2d0", frictions={})]
    write_usage_stream(ui.routines, _at(runs))
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")

    judged = ui_page.locator('tr.chg-row:visible:has([data-moment="judged"])')
    expect(judged).to_have_count(1)
    expect(judged.locator("[data-verdict]")).to_have_attribute("data-verdict", "too few runs")
    gone = judged.locator(".mo-fixed")
    expect(gone).to_have_text("✓1 gone")
    expect(gone).to_have_attribute("title", "edit_miss")
    expect(judged.locator(".mo-introduced")).to_have_text("+1 new")
    colours = ui_page.evaluate("""() => {
      const c = (sel) => getComputedStyle(document.querySelector(sel)).color;
      const probe = (v) => { const s = document.createElement('span'); s.style.color = `var(${v})`;
                              document.body.append(s); const out = getComputedStyle(s).color; s.remove(); return out; };
      return { fixed: c('.mo-mark.mo-fixed'), ok: probe('--ok'),
               brought: c('.mo-mark.mo-introduced'), err: probe('--err') };
    }""")
    assert colours["fixed"] == colours["ok"] and colours["brought"] == colours["err"], colours

    judged.locator(".chg-toggle").click()
    detail = ui_page.locator("tr.chg-detail:visible table.mo-table")
    expect(detail.locator('tr[data-sig="edit_miss"]')).to_contain_text("2 → 0")
    expect(detail.locator('tr[data-sig="edit_miss"]')).to_contain_text("gone after")
    # the next change reached back across this one for its second run before: it says so
    newest = ui_page.locator("tr.chg-row").first
    expect(newest.locator(".mo-confounded, .faint").first).to_be_visible()
