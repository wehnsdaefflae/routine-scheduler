"""PRODUCTION and DEVELOPMENT, kept apart in the real console (operator, 2026-10-08: "in the ui,
we need to clearly distinguish between production related information and development related
information … make sure though not to clutter the interface which is text heavy anyways").

The rail's Develop group (Changes, Stats, Library) is one iris band apart from the production
groups at every width; #/changes and #/changes/<slug> render the change read models
(readmodels/change_effects.py, model_fit.py) as chips and mono numbers; Recipe health moved there
from the routine page, which keeps exactly ONE line about development — summons-toned when its
newest change regressed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import expect

from helpers import measured_run, write_usage_stream

from .helpers import unfold
from .test_mobile_nav import NAV, PHONE

WIDE = {"width": 1400, "height": 900}
DEV = ("changes", "stats", "library")


def _at(runs: list[dict]) -> list[dict]:
    """The same records, dated one a day up to yesterday — so "when" reads as the past."""
    start = datetime.now(UTC) - timedelta(days=len(runs) + 1)
    return [{**r, "ts": (start + timedelta(days=i)).isoformat()} for i, r in enumerate(runs)]


def seed_changes(ui, make_routine) -> None:
    """`uir`: a recipe change that cost more and delivered less (regressed), then a model
    change still collecting its runs (measuring). `uis`: held still across an engine release
    that halved its tokens (improved) — the release row."""
    make_routine(slug="uis")
    uir = ([measured_run(i, slug="uir") for i in range(1, 6)]
           + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1)
              for i in range(6, 11)]
           + [measured_run(i, slug="uir", recipe="c2a9f3e1", model="Sonnet high", tokens=90_000,
                           met=1) for i in (11, 12)])
    uis = ([measured_run(i, slug="uis") for i in range(1, 6)]
           + [measured_run(i, slug="uis", engine="0.397.0", tokens=20_000) for i in range(6, 11)])
    write_usage_stream(ui.routines, _at(uir) + _at(uis))


def seed_regression(ui) -> None:
    """`uir` alone, its newest change a regression."""
    write_usage_stream(ui.routines, _at(
        [measured_run(i, slug="uir") for i in range(1, 6)]
        + [measured_run(i, slug="uir", recipe="c2a9f3e1", tokens=90_000, met=1)
           for i in range(6, 11)]))


def _group_of(page, nav: str) -> str:
    return page.evaluate(
        """(nav) => { const a = document.querySelector(`.topbar a[data-nav="${nav}"]`);
                     return a.closest('.nav-zone.dev') ? 'dev' : 'prod'; }""", nav)


def _bg(page, selector: str) -> str:
    return page.evaluate(f"() => getComputedStyle(document.querySelector({selector!r})).backgroundColor")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_rail_sets_development_apart_in_both_themes(ui, ui_page, theme):
    """Changes, Stats and Library ride one iris band labelled Develop; Work and Fleet stay
    plain. The band is a tint of its own on either theme's rail, and the page you are on inside
    it is marked in iris, not the signal the production destinations use."""
    ui_page.add_init_script(f"localStorage.setItem('rsched_theme', {theme!r})")
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    zone = ui_page.locator(".nav-zone.dev")
    expect(zone.locator(".nav-group")).to_have_text("Develop")
    expect(zone.locator("a[data-nav]")).to_have_count(3)
    assert [_group_of(ui_page, n) for n in DEV] == ["dev"] * 3
    for nav in ("conversations", "questions", "messages", "dashboard", "settings", "help"):
        assert _group_of(ui_page, nav) == "prod", f"{nav} rides the Develop band"

    assert _bg(ui_page, ".nav-zone.dev") not in (_bg(ui_page, ".topbar"), "rgba(0, 0, 0, 0)")
    marks = ui_page.evaluate("""() => {
      const probe = document.createElement('span'); probe.style.color = 'var(--iris)';
      document.body.append(probe); const iris = getComputedStyle(probe).color; probe.remove();
      const label = getComputedStyle(document.querySelector('.nav-zone.dev .nav-group')).color;
      const active = document.querySelector('.nav-zone.dev a.active');
      return { iris, label, nav: active?.dataset.nav,
               shadow: getComputedStyle(active).boxShadow };
    }""")
    assert marks["nav"] == "changes"
    assert marks["label"] == marks["iris"], marks
    assert marks["iris"] in marks["shadow"], marks


def test_the_band_survives_the_icon_rail_and_the_bottom_bar(ui, ui_page):
    """Below 1180px the labels go and below 860px the rail is one row of icons: the Develop
    band stays a tinted run of them — never a row of its own — and the bar still fits."""
    ui_page.set_viewport_size({"width": 1000, "height": 900})
    ui_page.goto(f"{ui.url}/#/stats")
    zone = ui_page.locator(".nav-zone.dev")
    expect(zone).to_be_visible()
    expect(zone.locator(".nav-group")).to_be_hidden()
    assert _bg(ui_page, ".nav-zone.dev") != _bg(ui_page, ".topbar")

    ui_page.set_viewport_size(PHONE)
    ui_page.goto(f"{ui.url}/#/stats")
    expect(ui_page.locator(NAV).first).to_be_visible()
    rows = ui_page.evaluate(f"""() => [...document.querySelectorAll({NAV!r})]
        .map((a) => Math.round(a.getBoundingClientRect().top))""")
    assert len(set(rows)) == 1, f"the bottom bar broke into rows: {rows}"
    box = zone.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"] + 1
    assert _bg(ui_page, ".nav-zone.dev") != "rgba(0, 0, 0, 0)"
    # the page you are on is marked in the band's colour there too
    active = ui_page.locator(".nav-zone.dev a.active")
    expect(active).to_have_attribute("data-nav", "stats")


def test_the_changes_page_renders_releases_model_changes_and_routines(ui, ui_page, make_routine):
    seed_changes(ui, make_routine)
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes")
    expect(ui_page.locator(".kicker.dev")).to_have_text("development")

    release = ui_page.locator('tr[data-release="0.397.0"]')
    expect(release.locator('[data-verdict="improved"]')).to_have_text("▲1 improved")
    expect(release).to_contain_text("×0.50")
    expect(release.locator("td").nth(2)).to_have_text("1")       # one routine judged

    model = ui_page.locator('tr[data-change="model:"]')
    expect(model).to_contain_text("model Opus high→Sonnet high")
    chip = model.locator('a[data-verdict="measuring"]')
    expect(chip).to_contain_text("uir")
    expect(chip).to_have_attribute("href", "#/changes/uir")

    row = ui_page.locator('table.chg-routines tr[data-routine="uir"]')
    expect(row.locator("td").nth(1)).to_have_text("2")
    expect(row.locator('[data-verdict="measuring"]')).to_be_visible()
    expect(ui_page.locator('table.chg-routines tr[data-routine="uis"]')).to_have_count(0)
    row.get_by_role("link", name="uir").click()
    expect(ui_page).to_have_url(f"{ui.url}/#/changes/uir")
    expect(ui_page.locator("#crumbs")).to_contain_text("Changes›uir")


def test_the_changes_page_says_when_nothing_is_measured(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/changes")
    expect(ui_page.locator("[data-unmeasured]")).to_contain_text(
        "measurement starts with the runs recorded from this release on")
    expect(ui_page.locator("#view h2")).to_have_count(0)


def test_a_routines_development_view(ui, ui_page, make_routine):
    """Its changes newest first, one opened onto the signals it was judged on; the model fit;
    and the Recipe health that used to sit on the production page."""
    seed_changes(ui, make_routine)
    ui_page.set_viewport_size(WIDE)
    ui_page.goto(f"{ui.url}/#/changes/uir")
    rows = ui_page.locator("tr.chg-row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator("[data-verdict]")).to_have_attribute("data-verdict", "measuring")
    regressed = rows.nth(1)
    expect(regressed).to_contain_text("recipe c1→c2a9f3e")
    expect(regressed).to_contain_text("5 → 5")
    expect(regressed).to_contain_text("engine 0.396.0")
    expect(regressed.locator("[data-verdict]")).to_have_attribute("data-verdict", "regressed")
    for dim, state in (("correctness", "same"), ("completeness", "worse"),
                       ("effectiveness", "worse")):
        expect(regressed.locator(f'.dim-mark[data-dim="{dim}"]')).to_have_attribute(
            "data-state", state)

    detail = ui_page.locator("tr.chg-detail").nth(1)
    expect(detail).to_be_hidden()
    regressed.locator(".chg-toggle").click()
    expect(detail).to_be_visible()
    tokens = detail.locator('tr[data-signal="tokens"]')
    expect(tokens).to_contain_text("40.0k → 90.0k")
    expect(tokens.locator(".sig-mark")).to_have_text("▲")
    expect(tokens.locator(".sig-mark")).to_have_attribute("data-tone", "worse")
    met = detail.locator('tr[data-signal="met_rate"]')
    expect(met).to_contain_text("100% → 50%")
    expect(met.locator(".sig-mark")).to_have_text("▼")
    expect(met.locator(".sig-mark")).to_have_attribute("data-tone", "worse")
    expect(detail.locator('tr[data-signal="turns"] .sig-mark')).to_have_attribute("data-tone", "same")
    # the keyboard folds it again
    regressed.locator(".chg-toggle").press("Enter")
    expect(detail).to_be_hidden()

    fit = ui_page.locator("table.fit-table")
    expect(fit.locator('tr[data-model="Opus high"]')).to_contain_text("10")
    expect(fit.locator('tr[data-model="Sonnet high"]')).to_contain_text("50%")

    expect(ui_page.locator("#sec-recipe-health")).to_have_text("Recipe health")
    expect(ui_page.get_by_text("recipe versions aren't tracked")).to_be_visible()


def test_the_routine_page_points_to_development_in_one_line(ui, ui_page, make_routine):
    """The production page keeps one quiet line — the count and the newest verdict — and no
    Recipe health of its own."""
    seed_changes(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    line = ui_page.locator(".page-head [data-dev-line]")
    expect(line).to_contain_text("2 measured changes")
    expect(line.locator('[data-verdict="measuring"]')).to_be_visible()
    expect(line).to_have_attribute("href", "#/changes/uir")
    expect(line).not_to_have_class("dev-line summons")
    unfold(ui_page)
    expect(ui_page.locator("#sec-recipe-health")).to_have_count(0)
    expect(ui_page.get_by_text("Recipe health", exact=True)).to_have_count(0)
    line.click()
    expect(ui_page.locator("tr.chg-row")).to_have_count(2)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_regression_summons_from_the_routine_page(ui, ui_page, theme):
    seed_regression(ui)
    ui_page.add_init_script(f"localStorage.setItem('rsched_theme', {theme!r})")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    line = ui_page.locator("[data-dev-line]")
    expect(line).to_contain_text("1 measured change")
    expect(line).to_have_class("dev-line summons")
    chip = line.locator('[data-verdict="regressed"]')
    expect(chip).to_have_class("chip bare v-summons")
    colours = ui_page.evaluate("""() => {
      const probe = document.createElement('span'); probe.style.color = 'var(--summons)';
      document.body.append(probe); const summons = getComputedStyle(probe).color; probe.remove();
      return { summons, chip: getComputedStyle(document.querySelector(
        '[data-dev-line] [data-verdict]')).color };
    }""")
    assert colours["chip"] == colours["summons"], colours
